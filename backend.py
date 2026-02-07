"""LG Sport V3 - Backend Flask Application

API Flask pour scouting football multi-sources.
- Recherche: joueur/équipe/match
- Normalisation via mappings config.json
- Fusion par majorité avec traçabilité (DataFusionEngine)
- Résumé automatique + fiabilité (processor)
- Profil enrichi au clic (endpoints /api/profile/*)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from normalize import apply_mapping, build_identifiers, compute_master_id, extract_path, guess_entity_list
from processor import enrich_entity

try:
    from data_fusion import DataFusionEngine
    from pdf_generator import PDFGenerator
except ImportError as e:  # pragma: no cover
    print(f"Warning: Could not import optional modules: {e}")
    DataFusionEngine = None  # type: ignore[assignment]
    PDFGenerator = None  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("lgsport")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = RotatingFileHandler(
        LOG_DIR / "lgsport.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


logger = _configure_logging()

# Env
load_dotenv()


def load_config() -> dict[str, Any]:
    config_path = BASE_DIR / "config.json"
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        logger.info("Configuration loaded successfully")
        return cfg
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return {
            "apis": {},
            "fusion_settings": {},
            "server": {"port": 5000, "debug": False},
            "metadata": {"version": "3.0"},
        }


CONFIG: dict[str, Any] = load_config()


class MasterIdStore:
    def __init__(self, cfg: dict[str, Any]):
        db_cfg = cfg.get("database", {}) if isinstance(cfg.get("database", {}), dict) else {}
        self.enabled = bool(db_cfg.get("enabled", False))
        self.db_path = str(db_cfg.get("path", "data/lgsport.db"))
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None

        if self.enabled:
            try:
                self._connect()
                self._init_schema()
                logger.info("MasterIdStore enabled")
            except Exception as e:
                logger.error(f"MasterIdStore init failed: {e}")
                self.enabled = False

    def _connect(self) -> None:
        if self._conn is not None:
            return
        path = (BASE_DIR / self.db_path).resolve() if not self.db_path.startswith("/") else Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")

    def _init_schema(self) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_ids (
              master_id TEXT NOT NULL,
              entity_type TEXT NOT NULL,
              api_name TEXT NOT NULL,
              api_id TEXT,
              payload_json TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (master_id, api_name)
            );
            """
        )
        self._conn.commit()

    def upsert(self, *, master_id: str, entity_type: str, api_name: str, ids: dict[str, Any]) -> None:
        if not self.enabled or not master_id:
            return
        if self._conn is None:
            return
        api_id = ids.get("id")
        payload_json = None
        try:
            payload_json = json.dumps(ids, ensure_ascii=False, sort_keys=True)
        except Exception:
            payload_json = None

        with self._lock:
            self._conn.execute(
                "INSERT INTO api_ids(master_id, entity_type, api_name, api_id, payload_json, updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(master_id, api_name) DO UPDATE SET api_id=excluded.api_id, payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (
                    str(master_id),
                    str(entity_type),
                    str(api_name),
                    str(api_id) if api_id is not None else None,
                    payload_json,
                    datetime.now().isoformat(),
                ),
            )
            self._conn.commit()

    def get_by_master_id(self, master_id: str) -> dict[str, dict[str, Any]]:
        if not self.enabled or not master_id or self._conn is None:
            return {}
        with self._lock:
            cur = self._conn.execute(
                "SELECT api_name, payload_json FROM api_ids WHERE master_id = ?",
                (str(master_id),),
            )
            rows = cur.fetchall()

        out: dict[str, dict[str, Any]] = {}
        for api_name, payload_json in rows:
            try:
                out[str(api_name)] = json.loads(payload_json) if payload_json else {}
            except Exception:
                out[str(api_name)] = {}
        return out


MASTER_STORE = MasterIdStore(CONFIG)

# Flask
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def _parse_int(value: str | None, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


class APICache:
    def __init__(self, ttl_minutes: int = 60):
        self.cache: dict[str, dict[str, Any]] = {}
        self.ttl_seconds = int(ttl_minutes) * 60

    def _generate_key(self, api_name: str, endpoint: str, params: dict[str, Any]) -> str:
        param_str = json.dumps(params, sort_keys=True, ensure_ascii=False) if params else "{}"
        key_string = f"{api_name}:{endpoint}:{param_str}"
        return hashlib.md5(key_string.encode("utf-8")).hexdigest()

    def get(self, api_name: str, endpoint: str, params: dict[str, Any]) -> Optional[Any]:
        key = self._generate_key(api_name, endpoint, params)
        entry = self.cache.get(key)
        if not entry:
            return None
        if time.time() - float(entry["timestamp"]) < self.ttl_seconds:
            return entry["data"]
        self.cache.pop(key, None)
        return None

    def set(self, api_name: str, endpoint: str, params: dict[str, Any], data: Any) -> None:
        key = self._generate_key(api_name, endpoint, params)
        self.cache[key] = {"data": data, "timestamp": time.time()}

    def clear(self) -> None:
        self.cache.clear()
        logger.info("Cache vidé")


def _inject_api_key(headers: dict[str, str], api_key: Optional[str]) -> dict[str, str]:
    if not headers:
        return {}
    out = dict(headers)
    if api_key:
        for k, v in list(out.items()):
            if isinstance(v, str) and "{api_key}" in v:
                out[k] = v.replace("{api_key}", api_key)
    return out


class APIConnector:
    def __init__(self, config: dict[str, Any]):
        self.raw_config = config
        self.apis: dict[str, Any] = config.get("apis", {})
        self.fusion_config: dict[str, Any] = config.get("fusion_settings", {})
        self.enabled_apis: dict[str, Any] = {
            name: api for name, api in self.apis.items() if api.get("enabled", True)
        }
        self.status: dict[str, Any] = {}
        self.cache = APICache(ttl_minutes=int(self.fusion_config.get("cache_ttl_minutes", 60)))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "LG-Sport/3.0"})
        self._check_api_keys()

    def _check_api_keys(self) -> None:
        for api_name, api_config in self.enabled_apis.items():
            if api_config.get("api_key_required", False):
                key_env = api_config.get("api_key_env")
                api_key = os.getenv(key_env) if key_env else None
                if api_key:
                    self.status[api_name] = {
                        "available": True,
                        "key": "configured",
                    }
                else:
                    self.status[api_name] = {
                        "available": False,
                        "key": "missing",
                        "error": f"Missing {key_env}",
                    }
            else:
                self.status[api_name] = {
                    "available": True,
                    "key": "not_required",
                }

    def get_status(self) -> dict[str, Any]:
        return {
            "apis": self.status,
            "total": len(self.status),
            "available": sum(1 for s in self.status.values() if s.get("available", False)),
            "unavailable": sum(1 for s in self.status.values() if not s.get("available", True)),
            "timestamp": datetime.now().isoformat(),
        }

    def _make_request(
        self,
        api_name: str,
        endpoint: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        use_cache: bool = True,
    ) -> Optional[Any]:
        if use_cache and self.fusion_config.get("cache_enabled", True):
            cached_data = self.cache.get(api_name, endpoint, params or {})
            if cached_data is not None:
                return cached_data

        api_cfg = self.enabled_apis.get(api_name, {})
        timeout = int(api_cfg.get("timeout", 15))

        # API key injection
        api_key = None
        if api_cfg.get("api_key_required"):
            api_key = os.getenv(api_cfg.get("api_key_env", ""))
            if not api_key:
                return None

        effective_headers = _inject_api_key(api_cfg.get("headers", {}) or {}, api_key)
        if headers:
            effective_headers.update(_inject_api_key(headers, api_key))

        # Default query params (ex: lang=fr) configurable par API
        default_params = api_cfg.get("default_params")
        effective_params: Optional[dict[str, Any]] = None
        if isinstance(default_params, dict) or isinstance(params, dict):
            effective_params = {}
            if isinstance(default_params, dict):
                effective_params.update(default_params)
            if isinstance(params, dict):
                effective_params.update(params)

        try:
            response = self.session.get(url, headers=effective_headers or None, params=effective_params, timeout=timeout)
            if response.status_code == 200:
                try:
                    data: Any = response.json()
                except json.JSONDecodeError:
                    data = response.text
                if use_cache and self.fusion_config.get("cache_enabled", True):
                    self.cache.set(api_name, endpoint, params or {}, data)
                return data

            if response.status_code == 429:
                logger.warning(f"⚠️  {api_name}: Rate limit reached")
                return None
            if response.status_code in (401, 403):
                logger.error(f"❌ {api_name}: Auth failed ({response.status_code})")
                if api_name in self.status:
                    self.status[api_name]["available"] = False
                return None

            logger.warning(f"⚠️  {api_name}: HTTP {response.status_code}")
            return None

        except requests.exceptions.Timeout:
            logger.warning(f"⏱️  {api_name}: timeout after {timeout}s")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ {api_name}: connection error")
            return None
        except Exception as e:
            logger.error(f"❌ {api_name}: request exception - {e}")
            return None

    def _retry_request(
        self,
        api_name: str,
        endpoint: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        max_retries: int = 3,
    ) -> Optional[Any]:
        retry_delay = float(self.fusion_config.get("retry_delay_seconds", 2))
        exponential_backoff = bool(self.fusion_config.get("exponential_backoff", True))

        for attempt in range(max_retries):
            result = self._make_request(
                api_name,
                endpoint,
                url,
                headers=headers,
                params=params,
                use_cache=(attempt == 0),
            )
            if result is not None:
                return result

            if attempt < max_retries - 1:
                wait_time = retry_delay * ((2**attempt) if exponential_backoff else 1)
                time.sleep(wait_time)

        return None

    def _build_url(self, api_name: str, endpoint_key: str, **kwargs: Any) -> Optional[str]:
        api_cfg = self.enabled_apis.get(api_name, {})
        endpoints = api_cfg.get("endpoints", {}) or {}
        template = endpoints.get(endpoint_key)
        if not template:
            return None

        try:
            # TheSportsDB uses /{key}{endpoint}
            if api_name == "thesportsdb":
                key = os.getenv(api_cfg.get("api_key_env", ""), api_cfg.get("free_tier_key", "3"))
                endpoint = template.format(**kwargs)
                return f"{api_cfg.get('base_url', '').rstrip('/')}/{key}{endpoint}"

            endpoint = template.format(**kwargs)
            base = api_cfg.get("base_url", "")
            return f"{base}{endpoint}"
        except KeyError:
            # Endpoint template requires other placeholders (ex: by-id endpoint called with a name)
            return None

    def _extract_items(self, api_name: str, entity_type: str, data: Any) -> list[dict[str, Any]]:
        api_cfg = self.enabled_apis.get(api_name, {})
        resp_paths = api_cfg.get("response_list_paths")
        if isinstance(resp_paths, dict):
            paths = resp_paths.get(entity_type)
            if isinstance(paths, list):
                for p in paths:
                    if not isinstance(p, str) or not p:
                        continue
                    v = extract_path(data, p)
                    if isinstance(v, list):
                        return [x for x in v if isinstance(x, dict)]
                    if isinstance(v, dict):
                        return [v]

        # fallback heuristics
        items = guess_entity_list(api_name, entity_type, data)
        if items:
            return items

        # last resort: if root is a dict (by-id endpoints)
        return [data] if isinstance(data, dict) else []

    def _search_one(self, api_name: str, entity_type: str, query: str, season: str) -> Optional[list[dict[str, Any]]]:
        api_cfg = self.enabled_apis.get(api_name, {})
        endpoints = api_cfg.get("endpoints", {}) or {}

        # Player
        if entity_type == "player":
            if "search_player" in endpoints:
                url = self._build_url(api_name, "search_player", player_name=query, season=season)
                if not url:
                    return None
                data = self._retry_request(api_name, "search_player", url)
                return self._extract_items(api_name, "player", data)
            return None

        # Team
        if entity_type == "team":
            if "search_team" in endpoints:
                url = self._build_url(api_name, "search_team", team_name=query, season=season)
                if not url:
                    return None
                data = self._retry_request(api_name, "search_team", url)
                return self._extract_items(api_name, "team", data)
            return None

        # Match
        if entity_type == "match":
            # If numeric -> treat as fixture/event id when possible
            if re.fullmatch(r"\d+", query.strip()):
                for k in ("search_match", "search_fixture", "event_details", "match_stats"):
                    if k in endpoints:
                        url = self._build_url(api_name, k, match_id=query, fixture_id=query, event_id=query)
                        if url:
                            data = self._retry_request(api_name, k, url)
                            return self._extract_items(api_name, "match", data)

            # direct search endpoints
            if "search_match" in endpoints:
                url = self._build_url(api_name, "search_match", match_query=query, season=season)
                if url:
                    data = self._retry_request(api_name, "search_match", url)
                    return self._extract_items(api_name, "match", data)

            if "search_event" in endpoints:
                url = self._build_url(api_name, "search_event", event_name=query)
                if url:
                    data = self._retry_request(api_name, "search_event", url)
                    return self._extract_items(api_name, "match", data)

            # api_football: approximate search using head-to-head if query contains two team names
            if api_name == "api_football" and "h2h" in endpoints and "search_team" in endpoints:
                teams = re.split(r"\s+vs\.?\s+|\s+-\s+|\s+@\s+", query, flags=re.IGNORECASE)
                teams = [t.strip() for t in teams if t.strip()]
                if len(teams) >= 2:
                    t1 = self._search_one(api_name, "team", teams[0], season)
                    t2 = self._search_one(api_name, "team", teams[1], season)
                    if t1 and t2:
                        id1 = (t1[0].get("team") or {}).get("id") if isinstance(t1[0].get("team"), dict) else None
                        id2 = (t2[0].get("team") or {}).get("id") if isinstance(t2[0].get("team"), dict) else None
                        # fallback: try direct key
                        id1 = id1 or t1[0].get("id")
                        id2 = id2 or t2[0].get("id")
                        if id1 and id2:
                            url = self._build_url(api_name, "h2h", team1_id=id1, team2_id=id2)
                            if url:
                                data = self._retry_request(api_name, "h2h", url)
                                return self._extract_items(api_name, "match", data)

            return None

        return None

    def search(self, entity_type: str, query: str, season: str) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {}
        if not self.fusion_config.get("parallel_api_calls", True):
            for api_name in sorted(self.enabled_apis.keys()):
                if not self.status.get(api_name, {}).get("available"):
                    continue
                data = self._search_one(api_name, entity_type, query, season)
                if data:
                    results[api_name] = data
            return results

        max_workers = min(int(self.fusion_config.get("max_parallel_calls", 6)), len(self.enabled_apis) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_api = {
                executor.submit(self._search_one, api_name, entity_type, query, season): api_name
                for api_name in self.enabled_apis.keys()
                if self.status.get(api_name, {}).get("available")
            }
            for future in as_completed(future_to_api):
                api_name = future_to_api[future]
                try:
                    data = future.result()
                    if data:
                        results[api_name] = data
                except Exception as e:
                    logger.error(f"❌ {api_name}: search exception - {e}")
        return results

    def fetch_details(self, api_name: str, endpoint_key: str, **kwargs: Any) -> Any:
        url = self._build_url(api_name, endpoint_key, **kwargs)
        if not url:
            return None
        return self._retry_request(api_name, endpoint_key, url)


def normalize_results(raw_results: dict[str, Any], entity_type: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for api_name, items in (raw_results or {}).items():
        if not isinstance(items, list):
            continue

        api_cfg = CONFIG.get("apis", {}).get(api_name, {})
        mapping = (api_cfg.get("mappings", {}) or {}).get(entity_type) or {}
        if not isinstance(mapping, dict) or not mapping:
            continue

        normalized_list: list[dict[str, Any]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue

            # Filtrer non-football (ex: TheSportsDB peut retourner d'autres sports)
            if api_name == "thesportsdb":
                sport = raw_item.get("strSport")
                if isinstance(sport, str) and sport.strip() and sport.strip().lower() != "soccer":
                    continue

            normalized = apply_mapping(raw_item, mapping)
            normalized["master_id"] = compute_master_id(entity_type, normalized)
            normalized["_source"] = api_name
            normalized["_quality_weight"] = 1.0
            normalized["_identifiers"] = build_identifiers(api_name, entity_type, normalized)
            if isinstance(normalized.get("_identifiers"), dict):
                try:
                    MASTER_STORE.upsert(master_id=str(normalized.get("master_id") or ""), entity_type=entity_type, api_name=api_name, ids=normalized["_identifiers"])  # type: ignore[arg-type]
                except Exception:
                    pass
            normalized_list.append(normalized)

        if normalized_list:
            out[api_name] = normalized_list

    return out


api_connector = APIConnector(CONFIG)

data_fusion = None
if DataFusionEngine and CONFIG.get("fusion_settings", {}).get("enabled", True):
    try:
        data_fusion = DataFusionEngine(CONFIG)
    except Exception as e:
        logger.error(f"Failed to initialize DataFusionEngine: {e}")

pdf_generator = None
if PDFGenerator and CONFIG.get("pdf_settings", {}).get("enabled", True):
    try:
        pdf_generator = PDFGenerator(CONFIG)
    except Exception as e:
        logger.error(f"Failed to initialize PDFGenerator: {e}")


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(api_connector.get_status())


@app.route("/api/seasons", methods=["GET"])
def get_seasons():
    start_year = 2010
    end_year = 2025
    seasons = [{"value": str(y), "label": f"{y}-{y+1}"} for y in range(end_year, start_year - 1, -1)]

    current = str(CONFIG.get("metadata", {}).get("default_season", str(end_year)))
    if current not in {s["value"] for s in seasons}:
        current = str(end_year)

    # label convenience for UI
    return jsonify(
        {
            "success": True,
            "seasons": seasons,
            "current_season": current,
            "timestamp": datetime.now().isoformat(),
        }
    )


def _search(entity_type: str, query: str, season: str, limit: int) -> dict[str, Any]:
    apis_attempted = [
        api_name
        for api_name in api_connector.enabled_apis.keys()
        if api_connector.status.get(api_name, {}).get("available")
    ]

    raw_results = api_connector.search(entity_type, query, season)

    if not raw_results:
        return {
            "success": False,
            "query": query,
            "season": season,
            "results": [],
            "message": "Aucun résultat trouvé",
            "apis_attempted": apis_attempted,
            "apis_responded": [],
            "timestamp": datetime.now().isoformat(),
        }

    normalized = normalize_results(raw_results, entity_type)

    if data_fusion:
        if entity_type == "player":
            fused = data_fusion.fuse_player_data(normalized)
        elif entity_type == "team":
            fused = data_fusion.fuse_team_data(normalized)
        else:
            fused = data_fusion.fuse_match_data(normalized)
    else:
        # fallback: return normalized from first responding API
        first = next(iter(normalized.values()), [])
        fused = first

    enriched = [enrich_entity(entity_type, x) for x in fused[:limit]]

    per_source_counts: dict[str, int] = {api: 0 for api in apis_attempted}
    for k, v in (raw_results or {}).items():
        if isinstance(v, list):
            per_source_counts[str(k)] = len(v)

    apis_responded = [api for api, n in per_source_counts.items() if n > 0]

    return {
        "success": True,
        "query": query,
        "season": season,
        "results": enriched,
        "sources_count": len(apis_responded),
        "sources": apis_responded,
        "apis_attempted": apis_attempted,
        "apis_responded": apis_responded,
        "per_source_counts": per_source_counts,
        "timestamp": datetime.now().isoformat(),
    }


@app.route("/api/search/player", methods=["GET"])
def search_player():
    player_name = (request.args.get("name") or "").strip()
    season = (request.args.get("season") or "2024").strip()
    limit = _parse_int(request.args.get("limit"), default=10, min_value=1, max_value=50)

    if not player_name:
        return jsonify({"error": "Nom du joueur requis"}), 400

    try:
        return jsonify(_search("player", player_name, season, limit))
    except Exception as e:
        logger.error(f"Error in search_player: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": "Erreur serveur"}), 500


@app.route("/api/search/team", methods=["GET"])
def search_team():
    team_name = (request.args.get("name") or "").strip()
    season = (request.args.get("season") or "2024").strip()
    limit = _parse_int(request.args.get("limit"), default=10, min_value=1, max_value=50)

    if not team_name:
        return jsonify({"error": "Nom de l'équipe requis"}), 400

    try:
        return jsonify(_search("team", team_name, season, limit))
    except Exception as e:
        logger.error(f"Error in search_team: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": "Erreur serveur"}), 500


@app.route("/api/search/match", methods=["GET"])
def search_match():
    match_query = (request.args.get("query") or request.args.get("name") or "").strip()
    season = (request.args.get("season") or "2024").strip()
    limit = _parse_int(request.args.get("limit"), default=10, min_value=1, max_value=50)

    if not match_query:
        return jsonify({"error": "Requête de match requise"}), 400

    try:
        return jsonify(_search("match", match_query, season, limit))
    except Exception as e:
        logger.error(f"Error in search_match: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": "Erreur serveur"}), 500


@app.route("/api/profile/<entity_type>", methods=["POST"])
def profile(entity_type: str):
    entity_type = (entity_type or "").strip().lower()
    if entity_type not in {"player", "team", "match"}:
        return jsonify({"error": "Type invalide"}), 400

    payload = request.get_json(silent=True) or {}
    entity = payload.get("entity")
    season = str(payload.get("season") or "2024")

    if not isinstance(entity, dict):
        return jsonify({"error": "Données invalides"}), 400

    def _field_value(obj: dict[str, Any], key: str) -> Any:
        v = obj.get(key)
        if isinstance(v, dict) and "value" in v:
            return v.get("value")
        return v

    def _resolve_identifiers(identifiers: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(identifiers or {})

        # 1) Try DB crosswalk by master_id
        master_id = str(_field_value(entity, "master_id") or "").strip()
        if not master_id:
            for _api, v in (resolved or {}).items():
                if isinstance(v, dict) and v.get("master_id"):
                    master_id = str(v.get("master_id") or "").strip()
                    break

        if master_id:
            try:
                stored = MASTER_STORE.get_by_master_id(master_id)
                for api_name, ids in stored.items():
                    if api_name not in resolved and isinstance(ids, dict) and ids:
                        resolved[api_name] = ids
            except Exception:
                pass

        # 2) Resolve missing provider IDs by name search (parallel)
        if entity_type == "player":
            q = str(_field_value(entity, "full_name") or "").strip() or str(_field_value(entity, "name") or "").strip()
        elif entity_type == "team":
            q = str(_field_value(entity, "name") or "").strip() or str(_field_value(entity, "short_name") or "").strip()
        else:
            q = ""

        if not q:
            return resolved

        apis_to_search = [
            api_name
            for api_name in api_connector.enabled_apis.keys()
            if not resolved.get(api_name) and api_connector.status.get(api_name, {}).get("available")
        ]
        if not apis_to_search:
            return resolved

        max_workers = min(int(CONFIG.get("fusion_settings", {}).get("max_parallel_calls", 6)), len(apis_to_search) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_to_api = {
                executor.submit(api_connector._search_one, api_name, entity_type, q, season): api_name
                for api_name in apis_to_search
            }
            for fut in as_completed(fut_to_api):
                api_name = fut_to_api[fut]
                try:
                    raw_items = fut.result()
                except Exception:
                    continue

                if not raw_items or not isinstance(raw_items, list) or not isinstance(raw_items[0], dict):
                    continue

                api_cfg = CONFIG.get("apis", {}).get(api_name, {})
                mapping = (api_cfg.get("mappings", {}) or {}).get(entity_type) or {}
                if not isinstance(mapping, dict) or not mapping:
                    continue

                try:
                    normalized = apply_mapping(raw_items[0], mapping)
                    normalized["master_id"] = compute_master_id(entity_type, normalized)
                    normalized["_source"] = api_name
                    resolved[api_name] = build_identifiers(api_name, entity_type, normalized)
                    try:
                        MASTER_STORE.upsert(master_id=str(normalized.get("master_id") or ""), entity_type=entity_type, api_name=api_name, ids=resolved[api_name])
                    except Exception:
                        pass
                except Exception:
                    continue

        return resolved

    try:
        meta_obj = entity.get("_fusion_metadata")
        meta: dict[str, Any] = meta_obj if isinstance(meta_obj, dict) else {}
        ident_obj = meta.get("identifiers")
        identifiers: dict[str, Any] = ident_obj if isinstance(ident_obj, dict) else {}

        t0 = time.time()
        identifiers = _resolve_identifiers(identifiers)

        detail_raw: dict[str, list[dict[str, Any]]] = {}
        extras: dict[str, dict[str, Any]] = {}

        apis_available = [
            api_name
            for api_name in api_connector.enabled_apis.keys()
            if api_connector.status.get(api_name, {}).get("available")
        ]

        tasks: list[tuple[str, str, str, dict[str, Any]]] = []  # (kind, api_name, endpoint_key, kwargs)
        for api_name in apis_available:
            ids = identifiers.get(api_name) if isinstance(identifiers.get(api_name), dict) else None
            endpoints = (CONFIG.get("apis", {}).get(api_name, {}) or {}).get("endpoints", {}) or {}
            if not ids or not isinstance(endpoints, dict):
                continue

            detail_endpoint: str | None = None
            kwargs: dict[str, Any] = {}

            if entity_type == "player":
                for k in ("player_by_id", "player_details", "search_player"):
                    if k in endpoints:
                        detail_endpoint = k
                        break
                if detail_endpoint:
                    pid = ids.get("id") or ids.get("player_id")
                    if pid is not None:
                        kwargs = {"player_id": pid, "season": season}

                for k in (
                    "player_transfers",
                    "player_trophies",
                    "sidelined",
                    "player_matches",
                    "player_former_teams",
                    "player_contracts",
                ):
                    pid = ids.get("id") or ids.get("player_id")
                    if k in endpoints and pid is not None:
                        tasks.append(("extra", api_name, k, {"player_id": pid}))

            elif entity_type == "team":
                for k in ("team_details", "team_squad", "search_team"):
                    if k in endpoints:
                        detail_endpoint = k
                        break
                if detail_endpoint:
                    tid = ids.get("id") or ids.get("team_id")
                    if tid is not None:
                        kwargs = {"team_id": tid, "season": season}

                for k in (
                    "team_transfers",
                    "team_trophies",
                    "coaches",
                    "team_fixtures",
                    "team_matches",
                    "team_last_events",
                    "team_next_events",
                    "team_honours",
                ):
                    tid = ids.get("id") or ids.get("team_id")
                    if k in endpoints and tid is not None:
                        if k == "team_fixtures":
                            tasks.append(("extra", api_name, k, {"team_id": tid, "season": season, "number": 10}))
                        else:
                            tasks.append(("extra", api_name, k, {"team_id": tid}))

            else:  # match
                fixture_id = ids.get("id") or ids.get("fixture_id") or ids.get("match_id") or ids.get("event_id")
                if fixture_id is not None:
                    for k in (
                        "fixture_statistics",
                        "fixture_events",
                        "fixture_lineups",
                        "fixture_players",
                        "injuries",
                        "predictions",
                        "odds",
                        "search_fixture",
                        "event_details",
                        "match_lineups",
                        "match_head2head",
                    ):
                        if k in endpoints:
                            tasks.append(("extra", api_name, k, {"fixture_id": fixture_id, "match_id": fixture_id, "event_id": fixture_id}))

                    if "search_fixture" in endpoints or "event_details" in endpoints:
                        detail_endpoint = "search_fixture" if "search_fixture" in endpoints else "event_details"
                        kwargs = {"fixture_id": fixture_id, "event_id": fixture_id, "match_id": fixture_id}

            if detail_endpoint and kwargs:
                tasks.append(("detail", api_name, detail_endpoint, kwargs))

        apis_attempted: set[str] = set()
        apis_succeeded: set[str] = set()
        errors: dict[str, Any] = {}

        max_workers = min(int(CONFIG.get("fusion_settings", {}).get("max_parallel_calls", 6)), len(tasks) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_to_task = {
                executor.submit(api_connector.fetch_details, api_name, endpoint_key, **kwargs): (kind, api_name, endpoint_key)
                for (kind, api_name, endpoint_key, kwargs) in tasks
            }
            for fut in as_completed(fut_to_task):
                kind, api_name, endpoint_key = fut_to_task[fut]
                apis_attempted.add(api_name)
                try:
                    data = fut.result()
                except Exception as e:
                    errors.setdefault(api_name, []).append({"endpoint": endpoint_key, "error": str(e)})
                    continue

                if data is None:
                    continue

                apis_succeeded.add(api_name)

                if kind == "detail":
                    items = api_connector._extract_items(api_name, entity_type, data)
                    if items:
                        detail_raw[api_name] = items
                else:
                    extras.setdefault(api_name, {})[endpoint_key] = data

        query_meta = {
            "entity_type": entity_type,
            "season": season,
            "apis_available": apis_available,
            "apis_attempted": sorted(apis_attempted),
            "apis_succeeded": sorted(apis_succeeded),
            "duration_ms": int(round((time.time() - t0) * 1000)),
            "errors": errors,
        }

        # Normalize + fuse details
        normalized_details = normalize_results(detail_raw, entity_type)

        if data_fusion and normalized_details:
            if entity_type == "player":
                fused_list = data_fusion.fuse_player_data(normalized_details)
            elif entity_type == "team":
                fused_list = data_fusion.fuse_team_data(normalized_details)
            else:
                fused_list = data_fusion.fuse_match_data(normalized_details)
            fused = fused_list[0] if fused_list else entity
        else:
            fused = entity

        fused["extras"] = extras
        fused["_query_metadata"] = query_meta
        fused = enrich_entity(entity_type, fused)

        return jsonify({"success": True, "profile": fused, "timestamp": datetime.now().isoformat()})

    except Exception as e:
        logger.error(f"Error in profile: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": "Erreur serveur"}), 500


@app.route("/api/export/pdf", methods=["POST"])
def export_pdf():
    try:
        request_data = request.get_json(silent=True) or {}
        export_type = request_data.get("type")
        data = request_data.get("data")

        if export_type not in {"player", "team", "match"} or not isinstance(data, dict):
            return jsonify({"error": "Données invalides"}), 400

        if not pdf_generator:
            return jsonify({"error": "Générateur PDF non disponible"}), 500

        data = {**data, "entity_type": export_type}
        pdf_path = pdf_generator.generate_report(data, export_type)

        if pdf_path and os.path.exists(pdf_path):
            return send_file(
                pdf_path,
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"lg_sport_{export_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            )

        return jsonify({"error": "Erreur lors de la génération du PDF"}), 500

    except Exception as e:
        logger.error(f"Error in export_pdf: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erreur serveur"}), 500


@app.route("/api/health", methods=["GET"])
def health_check():
    status = api_connector.get_status()
    return jsonify(
        {
            "status": "healthy",
            "version": str(CONFIG.get("metadata", {}).get("version", "3.0")),
            "apis_available": status["available"],
            "apis_total": status["total"],
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/cache/clear", methods=["POST"])
def clear_cache():
    try:
        api_connector.cache.clear()
        return jsonify({"success": True, "message": "Cache vidé", "timestamp": datetime.now().isoformat()})
    except Exception:
        return jsonify({"success": False, "error": "Erreur serveur"}), 500


@app.route("/api/debug/mapping", methods=["GET"])
def debug_mapping():
    from mapping_debug import flatten_paths, mapping_coverage

    api_name = (request.args.get("api") or "").strip()
    entity_type = (request.args.get("type") or "").strip().lower()
    query = (request.args.get("query") or "").strip()
    season = (request.args.get("season") or str(CONFIG.get("metadata", {}).get("default_season", "2025"))).strip()

    if not api_name or api_name not in CONFIG.get("apis", {}):
        return jsonify({"success": False, "error": "API inconnue"}), 400
    if entity_type not in {"player", "team", "match"}:
        return jsonify({"success": False, "error": "Type invalide"}), 400
    if not query:
        return jsonify({"success": False, "error": "query requis"}), 400

    # Force call only this API
    if not api_connector.status.get(api_name, {}).get("available"):
        return jsonify({"success": False, "error": "API indisponible (clé manquante?)"}), 400

    raw_items = api_connector._search_one(api_name, entity_type, query, season) or []
    api_cfg = CONFIG.get("apis", {}).get(api_name, {})
    mapping = (api_cfg.get("mappings", {}) or {}).get(entity_type) or {}

    sample = raw_items[0] if raw_items else {}

    return jsonify(
        {
            "success": True,
            "api": api_name,
            "type": entity_type,
            "count": len(raw_items),
            "sample_paths": flatten_paths(sample),
            "mapping_coverage": mapping_coverage(sample, mapping) if isinstance(sample, dict) else {},
        }
    )


@app.route("/api/test", methods=["GET"])
def test_endpoint():
    return jsonify({"success": True, "message": "LG Sport V3 API is running", "version": str(CONFIG.get("metadata", {}).get("version", "3.0")), "timestamp": datetime.now().isoformat()})


@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Endpoint non trouvé"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Erreur serveur interne"}), 500


if __name__ == "__main__":
    logger.info("\n" + "=" * 70)
    logger.info("🚀 LG SPORT V3 - Starting backend server")
    logger.info("=" * 70)

    (BASE_DIR / "output_pdfs").mkdir(parents=True, exist_ok=True)

    server_config = CONFIG.get("server", {})
    app.run(
        debug=bool(server_config.get("debug", False)),
        host=str(server_config.get("host", "0.0.0.0")),
        port=int(server_config.get("port", 5000)),
    )
