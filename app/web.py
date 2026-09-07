"""HTML pages + the small JSON API the pages' own JS calls.

Only three JSON endpoints exist: the leaderboard, beatmap and mod-leaderboard
tables are rendered client-side and page/filter without a reload. Everything
else is server-rendered.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import aliased

from .extensions import db
from .leaderboards import (
    available_combos,
    country_leaderboard,
    first_places,
    mod_leaderboard,
    most_played,
    player_profile_stats,
    player_timelines,
    recent_first_places,
    recent_high_pp,
    recent_scores,
    trending_beatmaps,
)
from .models import Beatmap, Score, User
from .mods import ALLOWED_MODS, ALLOWED_MOD_SETTINGS
from .pipeline import PP_STATUSES, RANKED_STATUSES
from .pp import PP_VERSION

bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")

PAGE = 50
BEATMAP_PAGE = 30
TOGGLE_MODS = ["EZ", "HD", "HR", "DT", "NC", "HT", "DC", "FL", "MR"]


# --------------------------------------------------------------------------- #
#  template filters
# --------------------------------------------------------------------------- #
@bp.app_template_filter("acc")
def _acc(value):
    return "-" if value is None else f"{value * 100:.2f}"


@bp.app_template_filter("pp")
def _pp(value):
    return "-" if value is None else f"{value:.0f}"


@bp.app_template_filter("num")
def _num(value):
    return "-" if value is None else f"{value:,}"


@bp.app_context_processor
def inject_champs():
    from .leaderboards import get_mod_champions, get_acc_champions, get_score_champions
    return {
        "mod_champs": get_mod_champions(),
        "acc_champs": get_acc_champions(),
        "score_champs": get_score_champions(),
    }
# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _resolve_user(key: str) -> User | None:
    if key.isdigit():
        return db.session.get(User, int(key))
    return db.session.query(User).filter(func.lower(User.username) == key.lower()).first()


def _top_scores_for_user(user_id: int, limit: int = 100) -> list[Score]:
    return (
        db.session.query(Score)
        .filter(Score.user_id == user_id, Score.hidden.is_(False), Score.is_best.is_(True))
        .order_by(Score.pp.is_(None), Score.pp.desc(), Score.total_score.desc())
        .limit(limit)
        .all()
    )


def _user_json(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "countryCode": u.country_code,
        "totalPp": u.total_pp,
        "totalAccuracy": u.total_accuracy,
        "hasDiscord": bool(u.discord_link),
    }


# --------------------------------------------------------------------------- #
#  pages
# --------------------------------------------------------------------------- #
@bp.route("/")
def index():
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    
    first_score = db.session.query(func.min(Score.date)).scalar()
    if first_score:
        if first_score.tzinfo is None:
            first_score = first_score.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - first_score).days + 1
    else:
        days = 30
    days = max(30, days)
    
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    def make_cumulative_series(counts, base_total):
        # Initialize with 0s
        daily = {str((datetime.now(timezone.utc) - timedelta(days=i)).date()): 0 for i in range(days-1, -1, -1)}
        for row in counts:
            if row.d in daily:
                daily[row.d] = row.c
        
        # Accumulate
        series = []
        current = base_total
        for val in daily.values():
            current += val
            series.append(current)
        return series

    # Scores
    scores_base = db.session.query(Score).filter(Score.date <= start_date).count()
    scores_counts = db.session.query(func.date(Score.date).label('d'), func.count().label('c')).filter(Score.date > start_date).group_by('d').all()
    
    # Users
    first_user_plays = db.session.query(func.min(Score.date).label('min_date')).group_by(Score.user_id).subquery()
    users_base = db.session.query(first_user_plays).filter(first_user_plays.c.min_date <= start_date).count()
    users_counts = db.session.query(func.date(first_user_plays.c.min_date).label('d'), func.count().label('c')).filter(first_user_plays.c.min_date > start_date).group_by('d').all()
    
    # Beatmaps
    first_plays = db.session.query(func.min(Score.date).label('min_date')).group_by(Score.beatmap_id).subquery()
    maps_base = db.session.query(first_plays).filter(first_plays.c.min_date <= start_date).count()
    maps_counts = db.session.query(func.date(first_plays.c.min_date).label('d'), func.count().label('c')).filter(first_plays.c.min_date > start_date).group_by('d').all()

    # 24 hour rolling for scores today
    start_hour = datetime.now(timezone.utc) - timedelta(hours=24)
    scores_24h_counts = db.session.query(func.strftime('%Y-%m-%d %H', Score.date).label('h'), func.count().label('c')).filter(Score.date > start_hour).group_by('h').all()
    hourly = {}
    for i in range(23, -1, -1):
        dt = datetime.now(timezone.utc) - timedelta(hours=i)
        hourly[dt.strftime('%Y-%m-%d %H')] = 0
    for row in scores_24h_counts:
        if row.h in hourly:
            hourly[row.h] = row.c
    scores_today_series = list(hourly.values())

    stats = {
        "scores_today": db.session.query(Score).filter(Score.date > day_ago).count(),
        "scores": db.session.query(Score).count(),
        "players": db.session.query(User).filter(User.total_pp.isnot(None)).count(),
        "beatmaps": db.session.query(Beatmap).count(),
        "series": {
            "scores": make_cumulative_series(scores_counts, scores_base),
            "players": make_cumulative_series(users_counts, users_base),
            "beatmaps": make_cumulative_series(maps_counts, maps_base),
            "scores_today": scores_today_series
        },
        "days": days
    }
    return render_template(
        "index.html",
        stats=stats,
        first_places=recent_first_places(),
        high_pp=recent_high_pp(),
        recent=recent_scores(),
        trending=trending_beatmaps(),
    )


@bp.route("/leaderboard")
def leaderboard():
    countries = [
        r[0]
        for r in db.session.query(User.country_code)
        .filter(User.total_pp.isnot(None))
        .distinct()
        .order_by(User.country_code)
        .all()
    ]
    return render_template(
        "leaderboard.html",
        countries=countries,
        page=request.args.get("page", 1, type=int),
        country=request.args.get("country", ""),
        search=request.args.get("search", ""),
        page_size=PAGE,
    )


@bp.route("/topscores")
def topscores():
    include_unranked = request.args.get("unranked") in ("1", "true")
    statuses = PP_STATUSES if include_unranked else RANKED_STATUSES
    rows = (
        db.session.query(Score)
        .join(Beatmap, Score.beatmap_id == Beatmap.id)
        .filter(
            Score.pp.isnot(None), Score.hidden.is_(False), Score.is_best.is_(True),
            Beatmap.status.in_(statuses),
        )
        .order_by(Score.pp.desc())
        .limit(100)
        .all()
    )
    podium = [
        {
            "rank": i + 1,
            "name": s.user.username if s.user else "?",
            "href": url_for("web.user_detail", key=s.user_id),
            "avatar": f"https://a.ppy.sh/{s.user_id}",
            "flag_cc": s.user.country_code if s.user else None,
            "value": f"{s.pp:.0f}pp",
            "sub": s.beatmap.title if s.beatmap else None,
        }
        for i, s in enumerate(rows[:3])
    ]
    return render_template(
        "topscores.html", scores=rows, podium=podium, include_unranked=include_unranked
    )


@bp.route("/beatmaps")
def beatmaps():
    return render_template(
        "beatmaps.html",
        page=request.args.get("page", 1, type=int),
        search=request.args.get("search", ""),
        page_size=BEATMAP_PAGE,
    )


@bp.route("/beatmaps/<int:beatmap_id>")
def beatmap_detail(beatmap_id: int):
    b = db.session.get(Beatmap, beatmap_id)
    if b is None:
        abort(404)
    scores = (
        db.session.query(Score)
        .filter(Score.beatmap_id == beatmap_id, Score.hidden.is_(False), Score.is_best.is_(True))
        .order_by(Score.pp.is_(None), Score.pp.desc(), Score.total_score.desc())
        .limit(100)
        .all()
    )
    return render_template("beatmap_detail.html", beatmap=b, scores=scores)


@bp.route("/users/<key>")
def user_detail(key: str):
    u = _resolve_user(key)
    if u is None:
        abort(404)

    rank = country_rank = None
    if u.total_pp is not None:
        rank = db.session.query(func.count(User.id)).filter(User.total_pp > u.total_pp).scalar() + 1
        country_rank = (
            db.session.query(func.count(User.id))
            .filter(User.total_pp > u.total_pp, User.country_code == u.country_code)
            .scalar()
            + 1
        )

    base = db.session.query(Score).filter(Score.user_id == u.id, Score.hidden.is_(False))
    since = datetime.now(timezone.utc) - timedelta(days=14)
    recent = base.filter(Score.date > since).order_by(Score.date.desc()).limit(10).all()

    fp_count, fp_scores = first_places(u.id)
    profile = {
        "rank": rank,
        "country_rank": country_rank,
        "playcount": base.count(),
        "timelines": player_timelines(u.id),
        "most_played": most_played(u.id),
        "first_place_count": fp_count,
        "first_places": fp_scores,
        **player_profile_stats(u.id),
    }

    weighted, factor = [], 1.0
    for s in _top_scores_for_user(u.id, limit=100):
        weighted.append((s, factor))
        factor *= 0.95
    return render_template("user.html", user=u, profile=profile, weighted=weighted, recent=recent)


@bp.route("/mods")
@bp.route("/mods/<combo>")
def mods_index(combo: str = "NM"):
    combo = combo.upper()
    preselect = [] if combo == "NM" else [combo[i:i + 2] for i in range(0, len(combo), 2)]
    return render_template(
        "mods.html", toggle_mods=TOGGLE_MODS, combos=available_combos(), preselect=preselect
    )


@bp.route("/countries")
def countries_board():
    include_unranked = request.args.get("unranked") in ("1", "true")
    rows = country_leaderboard(include_unranked=include_unranked)
    podium = [
        {
            "rank": i + 1,
            "name": r["country"],
            "href": url_for("web.leaderboard", country=r["country"]),
            "flag_cc": r["country"],
            "value": f"{r['pp']:.0f}pp",
            "sub": f"{r['players']} players",
        }
        for i, r in enumerate(rows[:3])
    ]
    return render_template(
        "countries.html", rows=rows, podium=podium, include_unranked=include_unranked
    )


@bp.route("/faq")
def faq():
    return render_template(
        "faq.html",
        allowed_mods=sorted(ALLOWED_MODS),
        allowed_mod_settings=sorted(ALLOWED_MOD_SETTINGS),
        pp_version=PP_VERSION,
    )


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


# --------------------------------------------------------------------------- #
#  JSON (only what the pages fetch)
# --------------------------------------------------------------------------- #
@api_bp.get("/players")
def api_players():
    page = max(1, request.args.get("page", 1, type=int))
    country = request.args.get("countryCode")
    search = request.args.get("search")
    include_unranked = request.args.get("includeUnranked") in ("1", "true")
    sort = request.args.get("sort", "pp")

    pp_col = User.total_pp_all if include_unranked else User.total_pp
    acc_col = User.total_accuracy_all if include_unranked else User.total_accuracy

    score_sum = (
        db.session.query(Score.user_id, func.sum(Score.total_score).label("sc"))
        .filter(Score.is_best.is_(True), Score.hidden.is_(False))
        .group_by(Score.user_id)
        .subquery()
    )

    q = (
        db.session.query(User, score_sum.c.sc)
        .outerjoin(score_sum, score_sum.c.user_id == User.id)
        .filter(pp_col.isnot(None))
    )

    # Fetch all to do in-memory ranking (fast enough for our scale)
    all_users = q.all()
    
    # Sort
    if sort == "acc":
        all_users.sort(key=lambda row: (round(getattr(row[0], acc_col.key) or 0, 2), getattr(row[0], pp_col.key) or 0), reverse=True)
    elif sort == "score":
        all_users.sort(key=lambda row: (row.sc or 0, getattr(row[0], pp_col.key) or 0), reverse=True)
    else:
        all_users.sort(key=lambda row: (getattr(row[0], pp_col.key) or 0), reverse=True)

    # Compute ranks
    ranked_users = []
    for i, row in enumerate(all_users):
        ranked_users.append((row[0], row.sc, i + 1))

    # Filter
    if country:
        ranked_users = [r for r in ranked_users if r[0].country_code == country]
    
    if search:
        if search.isdigit():
            ranked_users = [r for r in ranked_users if r[0].id == int(search)]
        else:
            s_lower = search.lower()
            ranked_users = [r for r in ranked_users if s_lower in r[0].username.lower()]

    total = len(ranked_users)
    start = (page - 1) * PAGE
    rows = ranked_users[start:start + PAGE]

    players = []
    for u, sc, rk in rows:
        d = _user_json(u)
        d["totalPp"] = getattr(u, pp_col.key)
        d["totalAccuracy"] = getattr(u, acc_col.key)
        d["totalScore"] = int(sc or 0)
        d["rank"] = int(rk)
        players.append(d)
    return jsonify({"players": players, "total": total, "page": page})


_MAP_SORTS = {
    "pp": lambda m: (m["pp"] or 0, m["plays"]),
    "plays": lambda m: (m["plays"], m["pp"] or 0),
    "stars": lambda m: (m["stars"] or 0),
    "bpm": lambda m: m["bpm"],
    "length": lambda m: m["length"],
    "recent": lambda m: m["lastPlay"] or "",
}


@api_bp.get("/beatmaps")
def api_beatmaps():
    page = max(1, request.args.get("page", 1, type=int))
    search = (request.args.get("search") or "").strip()
    sort = request.args.get("sort", "pp")
    if sort not in _MAP_SORTS:
        sort = "pp"
    ascending = request.args.get("dir") == "asc"
    want_mods = {m for m in (request.args.get("mods") or "").upper().split(",") if m}

    def _range(name):
        return (
            request.args.get(name + "Min", type=float),
            request.args.get(name + "Max", type=float),
        )

    star_lo, star_hi = _range("star")
    bpm_lo, bpm_hi = _range("bpm")
    len_lo, len_hi = _range("len")
    pp_lo, pp_hi = _range("pp")

    agg = (
        db.session.query(
            Beatmap,
            func.count(Score.id).label("plays"),
            func.max(Score.pp).label("maxpp"),
            func.max(Score.date).label("lastplay"),
        )
        .join(Score, Score.beatmap_id == Beatmap.id)
        .filter(Score.hidden.is_(False))
        .group_by(Beatmap.id)
    )
    if search:
        if search.isdigit():
            agg = agg.filter(Beatmap.id == int(search))
        else:
            like = f"%{search}%"
            agg = agg.filter(
                Beatmap.artist.ilike(like)
                | Beatmap.title.ilike(like)
                | Beatmap.difficulty_name.ilike(like)
            )

    map_mods: dict[int, set[str]] = {}
    if want_mods:
        for bid, mods in (
            db.session.query(Score.beatmap_id, Score.mods)
            .filter(Score.hidden.is_(False))
            .all()
        ):
            map_mods.setdefault(bid, set()).update(
                m[:2] for m in (mods or []) if m[:2] != "RX"
            )

    items = []
    for b, plays, maxpp, lastplay in agg.all():
        sr = b.star_rating or b.star_rating_normal or None
        if star_lo is not None and (sr or 0) < star_lo:
            continue
        if star_hi is not None and (sr or 0) > star_hi:
            continue
        if bpm_lo is not None and b.bpm < bpm_lo:
            continue
        if bpm_hi is not None and b.bpm > bpm_hi:
            continue
        if len_lo is not None and b.length < len_lo:
            continue
        if len_hi is not None and b.length > len_hi:
            continue
        if pp_lo is not None and (maxpp or 0) < pp_lo:
            continue
        if pp_hi is not None and (maxpp or 0) > pp_hi:
            continue
        if want_mods and not want_mods.issubset(map_mods.get(b.id, set())):
            continue
        items.append(
            {
                "id": b.id,
                "setId": b.beatmapset_id,
                "artist": b.artist,
                "title": b.title,
                "difficultyName": b.difficulty_name,
                "stars": round(sr, 2) if sr else None,
                "bpm": round(b.bpm),
                "length": b.length,
                "ar": round(b.approach_rate, 1),
                "cs": round(b.circle_size, 1),
                "od": round(b.overall_difficulty, 1),
                "hp": round(b.hp_drain, 1),
                "status": b.status,
                "pp": round(maxpp) if maxpp else None,
                "plays": plays,
                "lastPlay": lastplay.isoformat() if lastplay else None,
            }
        )

    items.sort(key=_MAP_SORTS[sort], reverse=not ascending)
    total = len(items)
    start = (page - 1) * BEATMAP_PAGE
    return jsonify(
        {"beatmaps": items[start:start + BEATMAP_PAGE], "total": total, "page": page}
    )


@api_bp.get("/mod-leaderboard/<combo>")
def api_mod_leaderboard(combo: str):
    take = min(200, request.args.get("take", 100, type=int))
    include_unranked = request.args.get("includeUnranked") in ("1", "true")
    return jsonify(
        [
            {
                "userId": r["user_id"],
                "user": _user_json(r["user"]) if r["user"] else None,
                "pp": round(r["pp"], 2),
                "accuracy": r["accuracy"],
                "plays": r["plays"],
                "score": r["score"],
            }
            for r in mod_leaderboard(combo, limit=take, include_unranked=include_unranked)
        ]
    )
