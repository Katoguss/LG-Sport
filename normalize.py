from __future__ import annotations

import hashlib
import re
from typing import Any


_INDEX_RE = re.compile(r"^(?P<name>[^\[]+)\[(?P<idx>\d+)\]$")


def extract_path(obj: Any, path: str) -> Any:
    """Extract value from nested dict/list using dot + [idx] syntax.

    Examples:
      - "player.birth.date"
      - "statistics[0].games.minutes"
      - "referees[0].name"
    """

    if obj is None or not path:
        return None

    cur: Any = obj
    for part in path.split("."):
        if cur is None:
            return None

        m = _INDEX_RE.match(part)
        if m:
            name = m.group("name")
            idx = int(m.group("idx"))

            if isinstance(cur, dict):
                cur = cur.get(name)
            else:
                return None

            if isinstance(cur, list) and 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
            continue

        # simple key
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None

    return cur


def apply_mapping(raw_item: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for target_field, source_path in mapping.items():
        if not source_path:
            continue
        out[target_field] = extract_path(raw_item, source_path)
    return out


def guess_entity_list(api_name: str, entity_type: str, data: Any) -> list[dict[str, Any]]:
    """Try to extract a list[dict] of entities from a heterogeneous API payload."""

    if data is None:
        return []

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if not isinstance(data, dict):
        return []

    # Known patterns
    if api_name == "api_football":
        resp = data.get("response")
        if isinstance(resp, list):
            return [x for x in resp if isinstance(x, dict)]

    if api_name == "thesportsdb":
        if entity_type == "player" and isinstance(data.get("player"), list):
            return [x for x in data["player"] if isinstance(x, dict)]
        if entity_type == "team" and isinstance(data.get("teams"), list):
            return [x for x in data["teams"] if isinstance(x, dict)]
        # events
        for k in ("event", "events"):
            if entity_type == "match" and isinstance(data.get(k), list):
                return [x for x in data[k] if isinstance(x, dict)]

    # Generic keys
    candidates = {
        "player": ["players", "player", "response", "results", "data"],
        "team": ["teams", "team", "response", "results", "data"],
        "match": ["matches", "match", "fixtures", "events", "response", "results", "data"],
    }.get(entity_type, ["response", "results", "data"])

    for key in candidates:
        v = data.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            # sometimes: {items:[...]}
            for kk in ("items", "data", "results"):
                vv = v.get(kk)
                if isinstance(vv, list):
                    return [x for x in vv if isinstance(x, dict)]

    return []


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_name(v: Any) -> str:
    s = _norm_str(v)
    if not s:
        return ""
    try:
        import unicodedata

        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
    except Exception:
        pass
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(fc|cf|sc|ac|as|afc|sv|ss|cd|ud|de|la)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _norm_date(v: Any) -> str:
    s = _norm_str(v)
    if not s:
        return ""
    m = _DATE_RE.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def compute_master_id(entity_type: str, normalized_item: dict[str, Any]) -> str:
    if entity_type == "player":
        name = _norm_name(normalized_item.get("full_name") or normalized_item.get("name"))
        dob = _norm_date(normalized_item.get("date_of_birth"))
        nat = _norm_name(normalized_item.get("nationality"))

        if name and dob:
            key = f"player|{name}|{dob}"
        elif name and nat:
            key = f"player|{name}|{nat}"
        elif name:
            key = f"player|{name}"
        else:
            pid = normalized_item.get("id")
            key = f"player|id:{pid}" if pid is not None else "player|unknown"

    elif entity_type == "team":
        name = _norm_name(normalized_item.get("name") or normalized_item.get("short_name"))
        country = _norm_name(normalized_item.get("country") or normalized_item.get("area"))

        if name and country:
            key = f"team|{name}|{country}"
        elif name:
            key = f"team|{name}"
        else:
            tid = normalized_item.get("id")
            key = f"team|id:{tid}" if tid is not None else "team|unknown"

    else:
        home = _norm_name(normalized_item.get("home_team") or normalized_item.get("home_team_name"))
        away = _norm_name(normalized_item.get("away_team") or normalized_item.get("away_team_name"))
        date = _norm_date(normalized_item.get("date") or normalized_item.get("utcDate") or normalized_item.get("datetime"))

        if home and away and date:
            key = f"match|{home}|{away}|{date}"
        elif home or away or date:
            key = f"match|{home}|{away}|{date}"
        else:
            mid = normalized_item.get("id")
            key = f"match|id:{mid}" if mid is not None else "match|unknown"

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return f"{entity_type}_{digest[:16]}"


def build_identifiers(api_name: str, entity_type: str, normalized_item: dict[str, Any]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    ids["master_id"] = compute_master_id(entity_type, normalized_item)

    # always store the canonical id if present
    if normalized_item.get("id") is not None:
        ids["id"] = normalized_item.get("id")

    # common alias fields
    if entity_type == "player":
        for k in ("player_id", "current_team_id", "team_id"):
            if normalized_item.get(k) is not None:
                ids[k] = normalized_item.get(k)

    if entity_type == "team":
        for k in ("team_id", "venue_id"):
            if normalized_item.get(k) is not None:
                ids[k] = normalized_item.get(k)

    if entity_type == "match":
        for k in ("fixture_id", "match_id", "event_id", "home_team_id", "away_team_id"):
            if normalized_item.get(k) is not None:
                ids[k] = normalized_item.get(k)

    # api-specific id keys (if mapping used a different canonical)
    ids["_api"] = api_name
    return ids
