from __future__ import annotations

from typing import Any

from normalize import extract_path


def flatten_paths(obj: Any, prefix: str = "", *, max_depth: int = 6) -> list[str]:
    paths: list[str] = []

    def rec(cur: Any, pfx: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(cur, dict):
            for k, v in cur.items():
                key = str(k)
                np = f"{pfx}.{key}" if pfx else key
                paths.append(np)
                rec(v, np, depth + 1)
        elif isinstance(cur, list):
            # just inspect first element
            if not cur:
                return
            np = f"{pfx}[0]" if pfx else "[0]"
            paths.append(np)
            rec(cur[0], np, depth + 1)

    rec(obj, prefix, 0)
    # uniq + stable
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def mapping_coverage(item: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    total = 0
    filled = 0
    filled_fields: list[str] = []
    missing_fields: list[str] = []

    for k, p in (mapping or {}).items():
        if not isinstance(p, str) or not p:
            continue
        total += 1
        v = extract_path(item, p)
        if v is None or v == "" or v == [] or v == {}:
            missing_fields.append(k)
        else:
            filled += 1
            filled_fields.append(k)

    return {
        "fields_total": total,
        "fields_filled": filled,
        "coverage": round((filled / total), 3) if total else 0.0,
        "filled_fields": filled_fields[:50],
        "missing_fields": missing_fields[:50],
    }
