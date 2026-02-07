#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LG SPORT V3 - Data Fusion Engine

Objectif: fusion multi-sources par majorité + traçabilité par champ.

Règles:
- Chaque champ est voté par les sources (majorité stricte > 50%).
- En absence de majorité, le champ est marqué "uncertain".
- On conserve les sources et le détail des votes.

Entrée attendue:
- results: dict[api_name] -> list[dict] (items normalisés) ; chaque item doit contenir `_source`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


_EMPTY_STRINGS = {"", "n/a", "na", "null", "none", "unknown", "-"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _EMPTY_STRINGS:
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _safe_str(v: Any) -> str:
    try:
        return str(v)
    except Exception:
        return ""


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _parse_number(s: str) -> Optional[float]:
    if not isinstance(s, str):
        return None
    t = s.strip().lower().replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _normalize_height(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristique: si < 3 => mètres, sinon cm
        if float(value) < 3:
            return int(round(float(value) * 100))
        return int(round(float(value)))
    if isinstance(value, str):
        t = value.strip().lower().replace(",", ".")
        if not t:
            return None
        num = _parse_number(t)
        if num is None:
            return None
        if "cm" in t:
            return int(round(num))
        if "m" in t and "cm" not in t:
            return int(round(num * 100))
        # fallback
        if num < 3:
            return int(round(num * 100))
        return int(round(num))
    return None


def _normalize_weight(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    if isinstance(value, str):
        t = value.strip().lower().replace(",", ".")
        num = _parse_number(t)
        if num is None:
            return None
        return int(round(num))
    return None


def _normalize_for_vote(field: str, value: Any) -> Any:
    if _is_empty(value):
        return None

    if isinstance(value, bool):
        return value

    if field in {"height", "player_height", "stadium_capacity"}:
        return _normalize_height(value)

    if field in {"weight", "player_weight"}:
        return _normalize_weight(value)

    if isinstance(value, (int, float)):
        # arrondi léger pour limiter bruit
        return round(float(value), 3)

    if isinstance(value, str):
        s = _normalize_spaces(value)
        # normaliser unités fréquentes
        if field in {"height"}:
            return _normalize_height(s)
        if field in {"weight"}:
            return _normalize_weight(s)
        return s.lower()

    if isinstance(value, dict):
        # voter sur représentation stable
        return _safe_str(value)

    if isinstance(value, list):
        return _safe_str(value)

    return _safe_str(value)


def _majority_vote(
    field: str,
    candidates: list[tuple[Any, str, float]],
    *,
    tie_breaker: str = "most_informative",
) -> dict[str, Any]:
    """candidates: [(raw_value, source, weight), ...]"""

    total_votes = 0
    counts: dict[Any, float] = {}
    raw_by_key: dict[Any, list[tuple[Any, str, float]]] = {}

    for raw, source, weight in candidates:
        norm = _normalize_for_vote(field, raw)
        if norm is None:
            continue
        total_votes += 1
        counts[norm] = counts.get(norm, 0.0) + 1.0
        raw_by_key.setdefault(norm, []).append((raw, source, weight))

    if total_votes == 0:
        return {
            "value": None,
            "status": "missing",
            "confidence": 0.0,
            "sources": [],
            "votes": {},
        }

    # mode
    best_key = max(counts.keys(), key=lambda k: counts[k])
    best_count = counts[best_key]

    # vérifier majorité stricte
    has_majority = best_count > (total_votes / 2)

    # tie: plusieurs valeurs avec même count
    max_count = max(counts.values())
    tied_keys = [k for k, c in counts.items() if c == max_count]

    chosen_key = best_key
    status = "majority" if has_majority else "uncertain"

    if len(tied_keys) > 1:
        status = "uncertain"

        # aucune source n'est prioritaire: tie-break déterministe basé sur l'information
        def key_score(k: Any) -> tuple[int, str]:
            versions = raw_by_key.get(k, [])
            best_len = -1
            for raw, _src, _w in versions:
                length = len(_safe_str(raw))
                if length > best_len:
                    best_len = length
            return (best_len, _safe_str(k))

        if tie_breaker in {"most_informative", "longest"}:
            chosen_key = max(tied_keys, key=key_score)
        else:
            chosen_key = sorted(tied_keys, key=lambda x: _safe_str(x))[0]

    # choisir une valeur brute "présentable": longueur puis ordre stable
    versions = raw_by_key.get(chosen_key, [])
    versions_sorted = sorted(versions, key=lambda t: len(_safe_str(t[0])), reverse=True)
    chosen_raw = versions_sorted[0][0] if versions_sorted else None

    sources = sorted({src for _raw, src, _w in versions})

    votes_serialized: dict[str, Any] = {}
    for k, c in counts.items():
        votes_serialized[_safe_str(k)] = int(c)

    return {
        "value": chosen_raw,
        "status": status if total_votes > 1 else "single_source",
        "confidence": float(best_count) / float(total_votes),
        "sources": sources,
        "votes": votes_serialized,
    }


def _extract_group_key(entity_type: str, item: dict[str, Any]) -> str:
    mid = item.get("master_id")
    if isinstance(mid, dict):
        mid = mid.get("value")
    if isinstance(mid, str) and mid.strip():
        return mid.strip().lower()

    if entity_type == "player":
        for k in ("id", "name", "full_name"):
            v = item.get(k)
            if isinstance(v, dict):
                v = v.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
            if isinstance(v, (int, float)):
                return f"id:{int(v)}"
        return "unknown"

    if entity_type == "team":
        for k in ("id", "name", "short_name"):
            v = item.get(k)
            if isinstance(v, dict):
                v = v.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
            if isinstance(v, (int, float)):
                return f"id:{int(v)}"
        return "unknown"

    if entity_type == "match":
        # id si possible
        v = item.get("id")
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, (int, float)):
            return f"id:{int(v)}"
        if isinstance(v, str) and v.strip():
            return f"id:{v.strip().lower()}"

        home = item.get("home_team") or item.get("home_team_name")
        away = item.get("away_team") or item.get("away_team_name")
        date = item.get("date") or item.get("utcDate")
        return f"{_safe_str(home)}_{_safe_str(away)}_{_safe_str(date)}".strip().lower()

    return "unknown"


class DataFusionEngine:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.fusion_settings: dict[str, Any] = config.get("fusion_settings", {})
        self.data_quality_weights: dict[str, float] = {}
        self.conflict_resolution: dict[str, Any] = self.fusion_settings.get("conflict_resolution", {})

        self.stats = {"total_fusions": 0, "player_fusions": 0, "team_fusions": 0, "match_fusions": 0}
        logger.info("🔧 DataFusionEngine V3 (majority) initialisé")

    def fuse_player_data(self, results: dict[str, Any]) -> list[dict[str, Any]]:
        self.stats["player_fusions"] += 1
        self.stats["total_fusions"] += 1
        return self._fuse_entities("player", results)

    def fuse_team_data(self, results: dict[str, Any]) -> list[dict[str, Any]]:
        self.stats["team_fusions"] += 1
        self.stats["total_fusions"] += 1
        return self._fuse_entities("team", results)

    def fuse_match_data(self, results: dict[str, Any]) -> list[dict[str, Any]]:
        self.stats["match_fusions"] += 1
        self.stats["total_fusions"] += 1
        return self._fuse_entities("match", results)

    def _iter_items(self, results: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for api_name, api_items in (results or {}).items():
            if not isinstance(api_items, list):
                continue
            for it in api_items:
                if not isinstance(it, dict):
                    continue
                item = it.copy()
                item.setdefault("_source", api_name)
                item.setdefault("_quality_weight", 1.0)
                yield item

    def _fuse_entities(self, entity_type: str, results: dict[str, Any]) -> list[dict[str, Any]]:
        items = list(self._iter_items(results))
        if not items:
            return []

        groups: dict[str, list[dict[str, Any]]] = {}
        for idx, item in enumerate(items):
            key = _extract_group_key(entity_type, item)
            if key == "unknown":
                key = f"unknown_{idx}_{item.get('_source', 'unknown')}"
            groups.setdefault(key, []).append(item)

        fused: list[dict[str, Any]] = []
        for _key, versions in groups.items():
            fused.append(self._fuse_group(entity_type, versions))

        return fused

    def _fuse_group(self, entity_type: str, versions: list[dict[str, Any]]) -> dict[str, Any]:
        internal_keys = {"_source", "_quality_weight", "_identifiers"}

        # Collect all possible fields
        fields: set[str] = set()
        for v in versions:
            for k in v.keys():
                if k.startswith("_") or k in internal_keys:
                    continue
                fields.add(k)

        fused: dict[str, Any] = {}
        field_meta: dict[str, Any] = {}

        for field in sorted(fields):
            candidates: list[tuple[Any, str, float]] = []
            for v in versions:
                if field not in v:
                    continue
                candidates.append((v.get(field), str(v.get("_source", "unknown")), float(v.get("_quality_weight", 1.0))))

            meta = _majority_vote(
                field,
                candidates,
                tie_breaker=str(self.conflict_resolution.get("tie_breaker", "most_informative")),
            )

            field_meta[field] = meta
            fused[field] = {
                "value": meta.get("value"),
                "status": meta.get("status"),
                "confidence": meta.get("confidence"),
                "sources": meta.get("sources"),
                "votes": meta.get("votes"),
            }

        # Merge identifiers
        identifiers: dict[str, Any] = {}
        for v in versions:
            src = str(v.get("_source", "unknown"))
            ids = v.get("_identifiers")
            if isinstance(ids, dict):
                identifiers[src] = ids

        fused["_fusion_metadata"] = {
            "entity_type": entity_type,
            "source_count": len({v.get("_source") for v in versions}),
            "sources": sorted({str(v.get("_source", "unknown")) for v in versions}),
            "fusion_timestamp": datetime.now().isoformat(),
            "fields": field_meta,
            "identifiers": identifiers,
        }

        return fused

    def get_statistics(self) -> dict[str, Any]:
        return dict(self.stats)


__all__ = ["DataFusionEngine"]
