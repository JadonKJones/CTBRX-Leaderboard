"""Derived leaderboards: per-mod-combo, per-country, and per-player timelines.

These aggregate in Python rather than SQL (mods live in a JSON column). Fine at
current scale; revisit with a materialised table if the DB gets large.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .extensions import db
from .models import Beatmap, RankSnapshot, Score, User
from .pipeline import PP_STATUSES, RANKED_STATUSES, WEIGHT, _weighted_totals


# canonical display order for mod acronyms (rate mods DT/NC/HT/DC sit late, FL last-ish)
MOD_ORDER = ["EZ", "HD", "HR", "DT", "NC", "HT", "DC", "FL", "MR", "NF", "SD", "PF", "CL", "AC"]


def mod_combo(mods: list[str] | None) -> str:
    """['RX', 'DTx1.5', 'HD'] -> 'HDDT'   (RX stripped, rates normalised, canonical order)."""
    base = {m.split("x")[0].upper() for m in (mods or [])} - {"RX", ""}
    ordered = sorted(base, key=lambda m: (MOD_ORDER.index(m) if m in MOD_ORDER else 99, m))
    return "".join(ordered) or "NM"


def _visible_scores(include_unranked: bool = False):
    """Lightweight rows (not ORM objects) for the mod-combo aggregations.

    mods live in a JSON column so the grouping has to happen in Python; pulling
    only the needed columns keeps it cheap. By default only ranked/approved maps
    count; include_unranked also folds in loved maps.
    """
    statuses = PP_STATUSES if include_unranked else RANKED_STATUSES
    return (
        db.session.query(
            Score.user_id, Score.beatmap_id, Score.pp, Score.accuracy,
            Score.total_score, Score.mods,
        )
        .join(Beatmap, Score.beatmap_id == Beatmap.id)
        .filter(
            Score.hidden.is_(False),
            Score.pp.isnot(None),
            Beatmap.status.in_(statuses),
        )
        .all()
    )


def available_combos(min_scores: int = 3) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for s in _visible_scores(include_unranked=True):
        c = mod_combo(s.mods)
        counts[c] = counts.get(c, 0) + 1
    combos = [(c, n) for c, n in counts.items() if n >= min_scores]
    combos.sort(key=lambda x: x[1], reverse=True)
    return combos


def mod_leaderboard(combo: str, limit: int = 100, include_unranked: bool = False) -> list[dict]:
    combo = combo.upper()
    best_by_user: dict[int, dict[int, Score]] = {}
    for s in _visible_scores(include_unranked=include_unranked):
        if mod_combo(s.mods) != combo:
            continue
        maps = best_by_user.setdefault(s.user_id, {})
        cur = maps.get(s.beatmap_id)
        if cur is None or (s.pp or 0) > (cur.pp or 0):
            maps[s.beatmap_id] = s

    rows = []
    for uid, maps in best_by_user.items():
        total_pp, total_acc = _weighted_totals(list(maps.values()))
        rows.append({
            "user_id": uid,
            "pp": total_pp or 0.0,
            "accuracy": total_acc,
            "plays": len(maps),
            "score": sum(s.total_score or 0 for s in maps.values()),
        })
    rows.sort(key=lambda r: r["pp"], reverse=True)
    rows = rows[:limit]

    users = {
        u.id: u
        for u in db.session.query(User).filter(User.id.in_([r["user_id"] for r in rows])).all()
    }
    for r in rows:
        r["user"] = users.get(r["user_id"])
    return rows

import time

_mod_champs_cache = None
_mod_champs_time = 0

def get_mod_champions() -> dict[int, list[tuple[str, int]]]:
    """Returns a dict mapping user_id to a list of (mod combo, rank) they are top 3 in."""
    global _mod_champs_cache, _mod_champs_time
    if _mod_champs_cache is not None and time.time() - _mod_champs_time < 300:
        return _mod_champs_cache

    from .pipeline import SCAN_MOD_COMBOS
    champs = {}
    for mods in SCAN_MOD_COMBOS:
        if not mods: continue
        combo_str = "".join(mods)
        board = mod_leaderboard(combo_str, limit=3, include_unranked=False)
        for i, entry in enumerate(board):
            if entry.get("user_id"):
                uid = entry["user_id"]
                if uid not in champs: champs[uid] = []
                champs[uid].append((combo_str, i + 1))
    
    _mod_champs_cache = champs
    _mod_champs_time = time.time()
    return champs

_acc_champs_cache = None
_acc_champs_time = 0

def get_acc_champions() -> dict[int, int]:
    global _acc_champs_cache, _acc_champs_time
    if _acc_champs_cache is not None and time.time() - _acc_champs_time < 300:
        return _acc_champs_cache
    from sqlalchemy import func
    valid = (
        db.session.query(Score.user_id)
        .filter(Score.is_best.is_(True), Score.hidden.is_(False))
        .group_by(Score.user_id)
        .having(func.count(Score.id) >= 10)
        .subquery()
    )
    top = (
        db.session.query(User)
        .filter(User.total_accuracy.isnot(None), User.id.in_(valid))
        .order_by(User.total_accuracy.desc(), User.total_pp.desc())
        .limit(100)
        .all()
    )
    
    champs = {}
    current_rank = 1
    last_acc = None
    
    for u in top:
        acc = round(u.total_accuracy or 0, 2)
        if acc == 100.0:
            champs[u.id] = 1
            last_acc = 100.0
        else:
            if current_rank == 1 and last_acc == 100.0:
                current_rank = 2
            
            if last_acc is not None and acc < last_acc:
                current_rank += 1
                
            if current_rank > 3:
                break
                
            champs[u.id] = current_rank
            last_acc = acc
            
    _acc_champs_cache = champs
    _acc_champs_time = time.time()
    return champs

_score_champs_cache = None
_score_champs_time = 0

def get_score_champions() -> dict[int, int]:
    global _score_champs_cache, _score_champs_time
    if _score_champs_cache is not None and time.time() - _score_champs_time < 300:
        return _score_champs_cache
    from sqlalchemy import func
    top = (
        db.session.query(Score.user_id)
        .filter(Score.is_best.is_(True), Score.hidden.is_(False))
        .group_by(Score.user_id)
        .order_by(func.sum(Score.total_score).desc())
        .limit(3)
        .all()
    )
    champs = {r[0]: i + 1 for i, r in enumerate(top)}
    _score_champs_cache = champs
    _score_champs_time = time.time()
    return champs


def country_leaderboard(include_unranked: bool = False) -> list[dict]:
    from sqlalchemy import func

    pp_col = User.total_pp_all if include_unranked else User.total_pp

    score_by_user = dict(
        db.session.query(Score.user_id, func.sum(Score.total_score))
        .filter(Score.is_best.is_(True), Score.hidden.is_(False))
        .group_by(Score.user_id)
        .all()
    )

    rows: dict[str, dict] = {}
    users = (
        db.session.query(User)
        .filter(pp_col.isnot(None))
        .order_by(pp_col.desc())
        .all()
    )
    for u in users:
        c = rows.setdefault(
            u.country_code,
            {"country": u.country_code, "players": 0, "pp": 0.0, "score": 0, "top": u},
        )
        # weighted so a country isn't just "who has the most accounts"
        c["pp"] += (getattr(u, pp_col.key) or 0.0) * (WEIGHT ** c["players"])
        c["score"] += score_by_user.get(u.id, 0) or 0
        c["players"] += 1
    out = list(rows.values())
    out.sort(key=lambda r: r["pp"], reverse=True)
    return out


_RATES = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}


def _score_rate(mods: list[str] | None) -> float:
    for m in mods or []:
        base, _, rate = m.partition("x")
        if rate:
            try:
                return float(rate)
            except ValueError:
                pass
        if base in _RATES:
            return _RATES[base]
    return 1.0


def osu_level(total_score: int) -> tuple[int, float]:
    """(level, progress 0..1) using osu!'s score curve."""
    def needed(lvl: int) -> float:
        if lvl <= 100:
            return 5000 / 3 * (4 * lvl**3 - 3 * lvl**2 - lvl) + 1.25 * 1.8 ** (lvl - 60)
        return 26_931_190_829.0 + 99_999_999_999.0 * (lvl - 100)

    lvl = 1
    while lvl < 10000 and needed(lvl + 1) <= total_score:
        lvl += 1
    lo, hi = needed(lvl), needed(lvl + 1)
    return lvl, (total_score - lo) / (hi - lo) if hi > lo else 0.0


def fmt_playtime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def player_profile_stats(user_id: int) -> dict:
    scores = (
        db.session.query(Score)
        .join(Score.beatmap)
        .filter(Score.user_id == user_id, Score.hidden.is_(False))
        .all()
    )
    total_score = sum(s.total_score for s in scores)
    ranked_score = sum(s.total_score for s in scores if s.is_best)
    total_hits = sum(
        s.count_great + s.count_large_droplet + s.count_small_droplet + s.count_miss
        for s in scores
    )
    playtime = sum((s.beatmap.length or 0) / _score_rate(s.mods) for s in scores if s.beatmap)
    max_combo = max((s.combo for s in scores), default=0)
    level, progress = osu_level(total_score)

    grades: dict[str, int] = {}
    for s in scores:
        grades[s.grade] = grades.get(s.grade, 0) + 1

    return {
        "total_score": total_score,
        "ranked_score": ranked_score,
        "total_hits": total_hits,
        "playtime": fmt_playtime(playtime),
        "max_combo": max_combo,
        "level": level,
        "level_progress": round(progress * 100),
        "grades": {
            "XH": grades.get("XH", 0),
            "X": grades.get("X", 0),
            "SH": grades.get("SH", 0),
            "S": grades.get("S", 0),
            "A": grades.get("A", 0),
        },
    }


def _play_counts(rows) -> list[dict]:
    """[(beatmap_id, n), ...] -> [{beatmap, count}, ...] with one lookup query."""
    maps = {
        b.id: b
        for b in db.session.query(Beatmap).filter(Beatmap.id.in_([bid for bid, _ in rows])).all()
    }
    return [{"beatmap": maps[bid], "count": n} for bid, n in rows if bid in maps]


def most_played(user_id: int, limit: int = 10) -> list[dict]:
    from sqlalchemy import func

    rows = (
        db.session.query(Score.beatmap_id, func.count(Score.id))
        .filter(Score.user_id == user_id, Score.hidden.is_(False))
        .group_by(Score.beatmap_id)
        .order_by(func.count(Score.id).desc())
        .limit(limit)
        .all()
    )
    return _play_counts(rows)


def first_places(user_id: int, limit: int = 15) -> tuple[int, list[Score]]:
    """Scores where this player is #1 on the beatmap's CTBRX board."""
    mine = (
        db.session.query(Score)
        .filter(
            Score.user_id == user_id,
            Score.hidden.is_(False),
            Score.is_best.is_(True),
            Score.pp.isnot(None),
        )
        .all()
    )
    firsts = []
    for s in mine:
        top = (
            db.session.query(Score.user_id)
            .filter(
                Score.beatmap_id == s.beatmap_id,
                Score.hidden.is_(False),
                Score.is_best.is_(True),
                Score.pp.isnot(None),
            )
            .order_by(Score.pp.desc(), Score.total_score.desc())
            .first()
        )
        if top and top[0] == user_id:
            firsts.append(s)
    firsts.sort(key=lambda s: s.pp or 0, reverse=True)
    return len(firsts), firsts[:limit]


def recent_first_places(limit: int = 12) -> list[Score]:
    """Recently-set scores that currently sit #1 on their beatmap's CTBRX board."""
    candidates = (
        db.session.query(Score)
        .filter(Score.is_best.is_(True), Score.pp.isnot(None), Score.hidden.is_(False))
        .order_by(Score.date.desc())
        .limit(limit * 5)
        .all()
    )
    out = []
    for s in candidates:
        top = (
            db.session.query(Score.id)
            .filter(
                Score.beatmap_id == s.beatmap_id,
                Score.is_best.is_(True),
                Score.pp.isnot(None),
                Score.hidden.is_(False),
            )
            .order_by(Score.pp.desc(), Score.total_score.desc())
            .first()
        )
        if top and top[0] == s.id:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def top_pp_scores(limit: int = 12) -> list[Score]:
    return (
        db.session.query(Score)
        .filter(Score.is_best.is_(True), Score.pp.isnot(None), Score.hidden.is_(False))
        .order_by(Score.pp.desc())
        .limit(limit)
        .all()
    )


def recent_scores(limit: int = 15) -> list[Score]:
    """Every Relax osu!catch score as it lands, newest first."""
    return (
        db.session.query(Score)
        .filter(Score.hidden.is_(False))
        .order_by(Score.date.desc())
        .limit(limit)
        .all()
    )


def recent_high_pp(limit: int = 15, percentile: float = 0.90) -> list[Score]:
    """Recently-set scores whose pp lands in the top (1 - percentile) of all scores.

    A chronological feed (newest first), like Akatsuki's "High PP" panel.
    """
    pps = sorted(
        p for (p,) in db.session.query(Score.pp)
        .filter(Score.pp.isnot(None), Score.hidden.is_(False))
        .all()
    )
    if not pps:
        return []
    threshold = pps[min(len(pps) - 1, int(len(pps) * percentile))]
    return (
        db.session.query(Score)
        .filter(Score.pp >= threshold, Score.hidden.is_(False))
        .order_by(Score.date.desc())
        .limit(limit)
        .all()
    )


def trending_beatmaps(days: int = 7, limit: int = 10) -> list[dict]:
    from datetime import timedelta

    from sqlalchemy import func

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(Score.beatmap_id, func.count(Score.id))
        .filter(Score.date > since, Score.hidden.is_(False))
        .group_by(Score.beatmap_id)
        .order_by(func.count(Score.id).desc())
        .limit(limit)
        .all()
    )
    return _play_counts(rows)


def player_timelines(user_id: int) -> dict:
    """Cumulative playcount and reconstructed pp, both as [ms, value] point lists."""
    scores = (
        db.session.query(Score)
        .filter(Score.user_id == user_id, Score.hidden.is_(False))
        .order_by(Score.date.asc())
        .all()
    )
    playcount = [[int(s.date.timestamp() * 1000), i + 1] for i, s in enumerate(scores)]

    pp_points: list[list] = []
    ranked = [s for s in scores if s.pp is not None]
    best: dict[int, Score] = {}
    for s in ranked:
        cur = best.get(s.beatmap_id)
        if cur is None or (s.pp or 0) > (cur.pp or 0):
            best[s.beatmap_id] = s
        total_pp, _ = _weighted_totals(list(best.values()))
        pp_points.append([int(s.date.timestamp() * 1000), round(total_pp or 0.0, 1)])

    snaps = (
        db.session.query(RankSnapshot)
        .filter(RankSnapshot.user_id == user_id)
        .order_by(RankSnapshot.date.asc())
        .all()
    )
    to_ms = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
    rank_series = [[to_ms(s.date), s.rank] for s in snaps]
    peak = min(snaps, key=lambda s: s.rank, default=None)

    return {
        "playcount": playcount,
        "pp": pp_points,
        "rank": rank_series,
        "peak_rank": ({"rank": peak.rank, "date": peak.date.isoformat()} if peak else None),
    }
