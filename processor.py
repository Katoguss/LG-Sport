from __future__ import annotations

from typing import Any, Optional


def _field(entity: dict[str, Any], key: str) -> dict[str, Any] | None:
    v = entity.get(key)
    return v if isinstance(v, dict) and ("value" in v or "status" in v) else None


def get_value(entity: dict[str, Any], key: str, default: Any = None) -> Any:
    f = _field(entity, key)
    if f is None:
        return entity.get(key, default)
    return f.get("value", default)


def get_confidence(entity: dict[str, Any], key: str) -> float:
    f = _field(entity, key)
    if f is None:
        return 0.0
    try:
        return float(f.get("confidence") or 0.0)
    except Exception:
        return 0.0


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        t = x.strip().lower().replace(",", ".")
        num = ""
        for ch in t:
            if ch.isdigit() or ch in ".-":
                num += ch
            elif num:
                break
        try:
            return float(num) if num else None
        except Exception:
            return None
    return None


def _per90(value: Optional[float], minutes: Optional[float]) -> Optional[float]:
    if value is None or minutes is None or minutes <= 0:
        return None
    return value * 90.0 / minutes


def compute_reliability(entity: dict[str, Any]) -> dict[str, Any]:
    meta_obj = entity.get("_fusion_metadata")
    meta: dict[str, Any] = meta_obj if isinstance(meta_obj, dict) else {}
    fields_obj = meta.get("fields")
    fields: dict[str, Any] = fields_obj if isinstance(fields_obj, dict) else {}

    confidences: list[float] = []
    majority = 0
    uncertain = 0
    missing = 0

    for _k, m in fields.items():
        if not isinstance(m, dict):
            continue
        status = str(m.get("status") or "")
        try:
            conf = float(m.get("confidence") or 0.0)
        except Exception:
            conf = 0.0

        if status == "missing":
            missing += 1
            continue

        confidences.append(conf)
        if status in {"majority", "single_source"}:
            majority += 1
        elif status == "uncertain":
            uncertain += 1

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "avg_confidence": round(avg_conf, 3),
        "fields_total": len(fields),
        "fields_majority": majority,
        "fields_uncertain": uncertain,
        "fields_missing": missing,
    }


def build_player_summary(entity: dict[str, Any]) -> str:
    full_name = get_value(entity, "full_name") or get_value(entity, "name")
    dob = get_value(entity, "date_of_birth")
    nationality = get_value(entity, "nationality")
    height = get_value(entity, "height")
    weight = get_value(entity, "weight")
    foot = get_value(entity, "preferred_foot")

    position = get_value(entity, "position")
    club = get_value(entity, "current_team") or get_value(entity, "team")
    agent = get_value(entity, "agent")
    bio = get_value(entity, "description_fr") or get_value(entity, "description")

    apps = _to_float(get_value(entity, "appearances"))
    minutes = _to_float(get_value(entity, "minutes"))
    goals = _to_float(get_value(entity, "goals_total"))
    assists = _to_float(get_value(entity, "goals_assists"))
    rating = get_value(entity, "rating")

    g90 = _per90(goals, minutes)
    a90 = _per90(assists, minutes)

    parts: list[str] = []

    header = f"Le joueur {full_name or 'N/A'}"
    meta_bits = []
    if dob:
        meta_bits.append(str(dob))
    if nationality:
        meta_bits.append(str(nationality))
    if height:
        meta_bits.append(f"{height}")
    if weight:
        meta_bits.append(f"{weight}")
    if meta_bits:
        header += f" ({', '.join(meta_bits)})"
    parts.append(header + ".")

    role_bits = []
    if position:
        role_bits.append(str(position))
    if club:
        role_bits.append(f"au club {club}")
    if role_bits:
        parts.append("Il évolue " + " ".join(role_bits) + ".")

    if foot:
        parts.append(f"Pied préféré: {foot}.")

    if agent:
        parts.append(f"Agent: {agent}.")

    season_stats_bits = []
    if apps is not None:
        season_stats_bits.append(f"{int(apps)} matchs")
    if minutes is not None:
        season_stats_bits.append(f"{int(minutes)} minutes")
    if goals is not None:
        season_stats_bits.append(f"{int(goals)} buts")
    if assists is not None:
        season_stats_bits.append(f"{int(assists)} passes décisives")
    if season_stats_bits:
        parts.append("Cette saison: " + ", ".join(season_stats_bits) + ".")

    adv_bits = []
    if g90 is not None:
        adv_bits.append(f"{g90:.2f} but/90")
    if a90 is not None:
        adv_bits.append(f"{a90:.2f} passe/90")
    if rating:
        adv_bits.append(f"rating {rating}")
    if adv_bits:
        parts.append("Indicateurs: " + ", ".join(adv_bits) + ".")

    extras_obj = entity.get("extras")
    extras: dict[str, Any] = extras_obj if isinstance(extras_obj, dict) else {}
    api_obj = extras.get("api_football")
    api_football: dict[str, Any] = api_obj if isinstance(api_obj, dict) else {}

    if isinstance(api_football.get("player_transfers"), dict):
        resp = api_football["player_transfers"].get("response")
        if isinstance(resp, list) and resp:
            parts.append(f"Transferts enregistrés: {len(resp)}.")

    if isinstance(api_football.get("sidelined"), dict):
        resp = api_football["sidelined"].get("response")
        if isinstance(resp, list) and resp:
            parts.append(f"Blessures/indisponibilités: {len(resp)} entrée(s).")

    if bio:
        bio_txt = str(bio).replace("\n", " ").strip()
        if bio_txt:
            parts.append("Bio: " + bio_txt[:220] + ("…" if len(bio_txt) > 220 else "") + ".")

    return " ".join(parts)


def build_team_summary(entity: dict[str, Any]) -> str:
    name = get_value(entity, "name")
    city = get_value(entity, "city") or get_value(entity, "venue_city")
    stadium = get_value(entity, "stadium") or get_value(entity, "venue") or get_value(entity, "venue_name")
    capacity = get_value(entity, "stadium_capacity") or get_value(entity, "venue_capacity")
    coach = get_value(entity, "coach")
    country = get_value(entity, "country") or get_value(entity, "area")
    bio = get_value(entity, "description_fr") or get_value(entity, "description")

    wins = get_value(entity, "fixtures_wins_total")
    draws = get_value(entity, "fixtures_draws_total")
    loses = get_value(entity, "fixtures_loses_total")
    gf = get_value(entity, "goals_for_total_home")
    ga = get_value(entity, "goals_against_total_home")

    parts: list[str] = []
    parts.append(f"Le club {name or 'N/A'}" + (f" ({country})" if country else "") + ".")

    if city or stadium:
        st = ""
        if city:
            st += f"basé à {city}"
        if stadium:
            st += (", " if st else "") + f"joue au stade {stadium}"
        if capacity:
            st += f" (capacité {capacity})"
        parts.append(st + ".")

    if coach:
        parts.append(f"Entraîneur: {coach}.")

    if any(v is not None for v in (wins, draws, loses)):
        parts.append(
            "Bilan: "
            + "/".join(str(x) for x in (wins or "?", draws or "?", loses or "?"))
            + " (V/N/D)."
        )

    if gf is not None or ga is not None:
        parts.append(f"Buts: pour {gf or '?'}, contre {ga or '?'}.")

    if bio:
        bio_txt = str(bio).replace("\n", " ").strip()
        if bio_txt:
            parts.append("Infos: " + bio_txt[:220] + ("…" if len(bio_txt) > 220 else "") + ".")

    return " ".join(parts)


def build_match_summary(entity: dict[str, Any]) -> str:
    home = get_value(entity, "home_team") or get_value(entity, "home_team_name")
    away = get_value(entity, "away_team") or get_value(entity, "away_team_name")
    date = get_value(entity, "date")
    time = get_value(entity, "time")
    venue = get_value(entity, "venue") or get_value(entity, "venue_name")
    referee = get_value(entity, "referee")
    status = get_value(entity, "status") or get_value(entity, "status_long")
    competition = get_value(entity, "competition") or get_value(entity, "league") or get_value(entity, "league_name")
    bio = get_value(entity, "description_fr")

    score_h = get_value(entity, "score_home") or get_value(entity, "goals_home") or get_value(entity, "score_fulltime_home")
    score_a = get_value(entity, "score_away") or get_value(entity, "goals_away") or get_value(entity, "score_fulltime_away")

    parts: list[str] = []
    parts.append(f"Match {home or 'N/A'} vs {away or 'N/A'}")
    when = []
    if date:
        when.append(str(date))
    if time:
        when.append(str(time))
    if when:
        parts.append("(" + " ".join(when) + ")")
    parts[-1] = parts[-1] + "." if parts else ""

    if competition:
        parts.append(f"Compétition: {competition}.")
    if venue:
        parts.append(f"Stade: {venue}.")
    if referee:
        parts.append(f"Arbitre: {referee}.")
    if status:
        parts.append(f"Statut: {status}.")

    if score_h is not None and score_a is not None:
        parts.append(f"Score: {score_h}-{score_a}.")

    if bio:
        bio_txt = str(bio).replace("\n", " ").strip()
        if bio_txt:
            parts.append("Infos: " + bio_txt[:220] + ("…" if len(bio_txt) > 220 else "") + ".")

    return " ".join(parts)


def _to_number_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace('%', '').replace(',', '.')
        try:
            return float(t)
        except Exception:
            return _to_float(v)
    return None


def _extract_api_football_fixture_statistics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    resp = payload.get('response')
    if not isinstance(resp, list) or len(resp) < 2:
        return {}

    def stats_map(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        stats = item.get('statistics')
        if not isinstance(stats, list):
            return {}
        out: dict[str, Any] = {}
        for row in stats:
            if not isinstance(row, dict):
                continue
            t = row.get('type')
            val = row.get('value')
            if not isinstance(t, str):
                continue
            out[t.strip().lower()] = val
        return out

    home_stats = stats_map(resp[0])
    away_stats = stats_map(resp[1])

    # Common API-Football type labels
    mapping = {
        'ball possession': ('possession_home', 'possession_away'),
        'total shots': ('shots_home', 'shots_away'),
        'shots on goal': ('shots_on_target_home', 'shots_on_target_away'),
        'corner kicks': ('corners_home', 'corners_away'),
        'fouls': ('fouls_home', 'fouls_away'),
        'yellow cards': ('yellow_cards_home', 'yellow_cards_away'),
        'red cards': ('red_cards_home', 'red_cards_away'),
        'expected goals': ('xg_home', 'xg_away'),
    }

    derived: dict[str, Any] = {}
    for k, (hk, ak) in mapping.items():
        hv = home_stats.get(k)
        av = away_stats.get(k)
        if hv is None and av is None:
            continue
        # normalize percentage strings and numeric
        if k == 'ball possession':
            derived[hk] = _to_number_or_none(hv)
            derived[ak] = _to_number_or_none(av)
        else:
            derived[hk] = _to_number_or_none(hv)
            derived[ak] = _to_number_or_none(av)

    return derived


def _extract_api_football_fixture_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    resp = payload.get('response')
    if not isinstance(resp, list):
        return []

    out: list[dict[str, Any]] = []
    for ev in resp:
        if not isinstance(ev, dict):
            continue
        time_obj = ev.get('time')
        team_obj = ev.get('team')
        player_obj = ev.get('player')
        assist_obj = ev.get('assist')

        t: dict[str, Any] = time_obj if isinstance(time_obj, dict) else {}
        team: dict[str, Any] = team_obj if isinstance(team_obj, dict) else {}
        player: dict[str, Any] = player_obj if isinstance(player_obj, dict) else {}
        assist: dict[str, Any] = assist_obj if isinstance(assist_obj, dict) else {}

        minute = t.get('elapsed')
        extra = t.get('extra')
        try:
            minute_i = int(minute) if minute is not None else None
        except Exception:
            minute_i = None
        try:
            extra_i = int(extra) if extra is not None else None
        except Exception:
            extra_i = None

        out.append(
            {
                'minute': minute_i,
                'extra': extra_i,
                'team': team.get('name'),
                'player': player.get('name'),
                'assist': assist.get('name'),
                'type': ev.get('type'),
                'detail': ev.get('detail'),
                'comments': ev.get('comments'),
            }
        )

    out.sort(key=lambda x: ((x.get('minute') or 0), (x.get('extra') or 0)))
    return out


def _extract_api_football_predictions(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    resp = payload.get('response')
    if not isinstance(resp, list) or not resp or not isinstance(resp[0], dict):
        return {}

    p = resp[0].get('predictions')
    predictions: dict[str, Any] = p if isinstance(p, dict) else {}

    out: dict[str, Any] = {}

    winner = predictions.get('winner')
    winner_obj: dict[str, Any] = winner if isinstance(winner, dict) else {}
    if winner_obj.get('name'):
        out['prediction_winner'] = winner_obj.get('name')

    advice = predictions.get('advice')
    if isinstance(advice, str) and advice.strip():
        out['prediction_advice'] = advice.strip()

    percent = predictions.get('percent')
    percent_obj: dict[str, Any] = percent if isinstance(percent, dict) else {}
    if percent_obj:
        out['prediction_percent_home'] = _to_number_or_none(percent_obj.get('home'))
        out['prediction_percent_draw'] = _to_number_or_none(percent_obj.get('draw'))
        out['prediction_percent_away'] = _to_number_or_none(percent_obj.get('away'))

    return {k: v for k, v in out.items() if v is not None and v != ''}


def _extract_api_football_odds_1x2(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    resp = payload.get('response')
    if not isinstance(resp, list) or not resp or not isinstance(resp[0], dict):
        return {}

    root = resp[0]
    bookmakers = root.get('bookmakers')
    if not isinstance(bookmakers, list):
        return {}

    best = {'home': None, 'draw': None, 'away': None}
    seen_bookmakers = 0

    def update(side: str, odd_raw: Any) -> None:
        odd = _to_number_or_none(odd_raw)
        if odd is None:
            return
        cur = best.get(side)
        if cur is None or odd > cur:
            best[side] = odd

    for bm in bookmakers:
        if not isinstance(bm, dict):
            continue
        seen_bookmakers += 1
        bets = bm.get('bets')
        if not isinstance(bets, list):
            continue

        bet_obj = None
        for b in bets:
            if not isinstance(b, dict):
                continue
            name = str(b.get('name') or '').strip().lower()
            bid = b.get('id')
            if bid == 1 or name in {'match winner', 'winner', '1x2', 'match result'}:
                bet_obj = b
                break
        if not isinstance(bet_obj, dict):
            continue

        values = bet_obj.get('values')
        if not isinstance(values, list):
            continue

        for v in values:
            if not isinstance(v, dict):
                continue
            label = str(v.get('value') or '').strip().lower()
            odd = v.get('odd')
            if label in {'home', '1'}:
                update('home', odd)
            elif label in {'draw', 'x'}:
                update('draw', odd)
            elif label in {'away', '2'}:
                update('away', odd)

    out: dict[str, Any] = {
        'odds_home': best['home'],
        'odds_draw': best['draw'],
        'odds_away': best['away'],
        'odds_bookmakers_count': seen_bookmakers or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def _extract_api_football_lineups(payload: Any) -> Optional[list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return None
    resp = payload.get('response')
    if not isinstance(resp, list) or not resp:
        return None

    out: list[dict[str, Any]] = []

    def extract_players(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        players: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            p = r.get('player')
            pl: dict[str, Any] = p if isinstance(p, dict) else {}
            if not pl:
                continue
            players.append(
                {
                    'id': pl.get('id'),
                    'name': pl.get('name'),
                    'number': pl.get('number'),
                    'pos': pl.get('pos'),
                    'grid': pl.get('grid'),
                }
            )
        return players

    for team_lineup in resp:
        if not isinstance(team_lineup, dict):
            continue
        team = team_lineup.get('team')
        team_obj: dict[str, Any] = team if isinstance(team, dict) else {}

        out.append(
            {
                'team_id': team_obj.get('id'),
                'team': team_obj.get('name'),
                'formation': team_lineup.get('formation'),
                'coach': (team_lineup.get('coach') or {}).get('name') if isinstance(team_lineup.get('coach'), dict) else team_lineup.get('coach'),
                'startXI': extract_players(team_lineup.get('startXI')),
                'substitutes': extract_players(team_lineup.get('substitutes')),
            }
        )

    return out or None


def _extract_api_football_injuries(payload: Any) -> Optional[list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return None
    resp = payload.get('response')
    if not isinstance(resp, list) or not resp:
        return None

    out: list[dict[str, Any]] = []
    for it in resp[:300]:
        if not isinstance(it, dict):
            continue
        player = it.get('player')
        team = it.get('team')
        fix = it.get('fixture')
        out.append(
            {
                'player': (player.get('name') if isinstance(player, dict) else None) or it.get('player'),
                'reason': it.get('reason'),
                'team': (team.get('name') if isinstance(team, dict) else None) or it.get('team'),
                'type': it.get('type'),
                'fixture': (fix.get('id') if isinstance(fix, dict) else None),
            }
        )
    return out or None


def _apply_derived_fields(entity: dict[str, Any]) -> dict[str, Any]:
    extras_obj = entity.get('extras')
    extras: dict[str, Any] = extras_obj if isinstance(extras_obj, dict) else {}

    api_obj = extras.get('api_football')
    api_football: dict[str, Any] = api_obj if isinstance(api_obj, dict) else {}

    def upsert_single_source_field(key: str, value: Any, source: str) -> None:
        if value is None:
            return
        cur = entity.get(key)
        if isinstance(cur, dict) and 'value' in cur and 'status' in cur:
            if cur.get('value') is None or cur.get('value') == '' or cur.get('status') == 'missing':
                cur['value'] = value
                cur['status'] = 'single_source'
                cur['confidence'] = float(cur.get('confidence') or 0.0) or 1.0
                srcs = cur.get('sources')
                if isinstance(srcs, list):
                    if source not in srcs:
                        srcs.append(source)
                else:
                    cur['sources'] = [source]
                votes = cur.get('votes')
                if not isinstance(votes, dict):
                    votes = {}
                votes[str(value)] = int(votes.get(str(value), 0)) + 1
                cur['votes'] = votes
            return
        entity[key] = {
            'value': value,
            'status': 'single_source',
            'confidence': 1.0,
            'sources': [source],
            'votes': {str(value): 1},
        }

    fixture_stats = api_football.get('fixture_statistics')
    derived = _extract_api_football_fixture_statistics(fixture_stats)
    if derived:
        for k, v in derived.items():
            upsert_single_source_field(k, v, 'api_football')

    fixture_events = api_football.get('fixture_events')
    timeline = _extract_api_football_fixture_events(fixture_events)
    if timeline:
        entity['timeline_events'] = timeline

    # Match: predictions, odds, lineups, injuries
    preds = api_football.get('predictions')
    pred_fields = _extract_api_football_predictions(preds)
    for k, v in pred_fields.items():
        upsert_single_source_field(k, v, 'api_football')

    odds = api_football.get('odds')
    odds_fields = _extract_api_football_odds_1x2(odds)
    for k, v in odds_fields.items():
        upsert_single_source_field(k, v, 'api_football')

    lineups_payload = api_football.get('fixture_lineups')
    simplified_lineups = _extract_api_football_lineups(lineups_payload)
    if simplified_lineups is not None:
        upsert_single_source_field('lineups', simplified_lineups, 'api_football')

    injuries_payload = api_football.get('injuries')
    simplified_injuries = _extract_api_football_injuries(injuries_payload)
    if simplified_injuries is not None:
        upsert_single_source_field('injuries', simplified_injuries, 'api_football')

    # Player: transfer & injury history (best-effort)
    transfers_payload = api_football.get('player_transfers')
    if isinstance(transfers_payload, dict):
        upsert_single_source_field('transfer_history', transfers_payload.get('response'), 'api_football')

    sidelined_payload = api_football.get('sidelined')
    if isinstance(sidelined_payload, dict):
        upsert_single_source_field('injury_history', sidelined_payload.get('response'), 'api_football')

    # Team: recent fixtures (best-effort)
    team_fx_payload = api_football.get('team_fixtures')
    if isinstance(team_fx_payload, dict):
        upsert_single_source_field('recent_matches', team_fx_payload.get('response'), 'api_football')

    return entity


def enrich_entity(entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
    entity = _apply_derived_fields(dict(entity))
    reliability = compute_reliability(entity)

    if entity_type == "player":
        summary = build_player_summary(entity)
        charts = _player_charts(entity)
    elif entity_type == "team":
        summary = build_team_summary(entity)
        charts = _team_charts(entity)
    else:
        summary = build_match_summary(entity)
        charts = _match_charts(entity)

    return {
        **entity,
        "summary": summary,
        "reliability": reliability,
        "charts": charts,
    }


def _player_charts(entity: dict[str, Any]) -> list[dict[str, Any]]:
    goals = _to_float(get_value(entity, "goals_total"))
    assists = _to_float(get_value(entity, "goals_assists"))
    shots = _to_float(get_value(entity, "shots_total"))
    shots_on = _to_float(get_value(entity, "shots_on_target"))
    key_passes = _to_float(get_value(entity, "passes_key"))
    passes = _to_float(get_value(entity, "passes_total"))
    tackles = _to_float(get_value(entity, "tackles_total"))
    interceptions = _to_float(get_value(entity, "tackles_interceptions"))
    yellow = _to_float(get_value(entity, "cards_yellow"))
    red = _to_float(get_value(entity, "cards_red"))
    minutes = _to_float(get_value(entity, "minutes"))

    charts: list[dict[str, Any]] = []

    core = [
        ("Buts", goals),
        ("Passes", assists),
        ("Minutes", minutes),
        ("Tirs", shots),
        ("Cadrés", shots_on),
        ("Passes clés", key_passes),
        ("Passes tot.", passes),
    ]
    labels = [k for k, v in core if v is not None]
    values = [v for _k, v in core if v is not None]
    if labels:
        charts.append({"type": "bar", "title": "Production offensive", "labels": labels, "datasets": [{"label": "Saison", "data": values}]})

    def90 = [("Tacles", tackles), ("Interceptions", interceptions)]
    labels2 = [k for k, v in def90 if v is not None]
    values2 = [v for _k, v in def90 if v is not None]
    if labels2:
        charts.append({"type": "bar", "title": "Actions défensives", "labels": labels2, "datasets": [{"label": "Saison", "data": values2}]})

    disc = [("Jaunes", yellow), ("Rouges", red)]
    labels3 = [k for k, v in disc if v is not None]
    values3 = [v for _k, v in disc if v is not None]
    if labels3:
        charts.append({"type": "doughnut", "title": "Discipline", "labels": labels3, "datasets": [{"label": "", "data": values3}]})

    # simple per90 if minutes exists
    if minutes and minutes > 0:
        g90 = _per90(goals, minutes)
        a90 = _per90(assists, minutes)
        if g90 is not None or a90 is not None:
            labels4 = []
            values4 = []
            if g90 is not None:
                labels4.append("Buts/90")
                values4.append(round(g90, 3))
            if a90 is not None:
                labels4.append("Passes/90")
                values4.append(round(a90, 3))
            charts.append({"type": "bar", "title": "Ratios / 90 min", "labels": labels4, "datasets": [{"label": "", "data": values4}]})

    return charts


def _team_charts(entity: dict[str, Any]) -> list[dict[str, Any]]:
    wins = _to_float(get_value(entity, "fixtures_wins_total"))
    draws = _to_float(get_value(entity, "fixtures_draws_total"))
    loses = _to_float(get_value(entity, "fixtures_loses_total"))

    labels = []
    values = []
    for k, v in (("Victoires", wins), ("Nuls", draws), ("Défaites", loses)):
        if v is not None:
            labels.append(k)
            values.append(v)

    if not labels:
        return []

    return [
        {
            "type": "doughnut",
            "title": "Bilan (V/N/D)",
            "labels": labels,
            "datasets": [{"label": "", "data": values}],
        }
    ]


def _match_charts(entity: dict[str, Any]) -> list[dict[str, Any]]:
    home = get_value(entity, "home_team") or get_value(entity, "home_team_name") or "Home"
    away = get_value(entity, "away_team") or get_value(entity, "away_team_name") or "Away"

    pos_h = _to_float(get_value(entity, "possession_home")) or _to_float(entity.get("possession_home"))
    pos_a = _to_float(get_value(entity, "possession_away")) or _to_float(entity.get("possession_away"))
    shots_h = _to_float(get_value(entity, "shots_home")) or _to_float(entity.get("shots_home"))
    shots_a = _to_float(get_value(entity, "shots_away")) or _to_float(entity.get("shots_away"))
    corners_h = _to_float(get_value(entity, "corners_home")) or _to_float(entity.get("corners_home"))
    corners_a = _to_float(get_value(entity, "corners_away")) or _to_float(entity.get("corners_away"))
    xg_h = _to_float(get_value(entity, "xg_home")) or _to_float(entity.get("xg_home"))
    xg_a = _to_float(get_value(entity, "xg_away")) or _to_float(entity.get("xg_away"))

    charts: list[dict[str, Any]] = []

    if pos_h is not None and pos_a is not None:
        charts.append({"type": "bar", "title": "Possession", "labels": [str(home), str(away)], "datasets": [{"label": "%", "data": [pos_h, pos_a]}]})

    if shots_h is not None and shots_a is not None:
        charts.append({"type": "bar", "title": "Tirs", "labels": [str(home), str(away)], "datasets": [{"label": "", "data": [shots_h, shots_a]}]})

    if corners_h is not None and corners_a is not None:
        charts.append({"type": "bar", "title": "Corners", "labels": [str(home), str(away)], "datasets": [{"label": "", "data": [corners_h, corners_a]}]})

    if xg_h is not None and xg_a is not None:
        charts.append({"type": "bar", "title": "xG", "labels": [str(home), str(away)], "datasets": [{"label": "", "data": [xg_h, xg_a]}]})

    return charts
