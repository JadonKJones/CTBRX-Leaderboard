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
    }


# --------------------------------------------------------------------------- #
#  pages
# --------------------------------------------------------------------------- #
@bp.route("/")
def index():
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    stats = {
        "scores_today": db.session.query(Score).filter(Score.date > day_ago).count(),
        "scores": db.session.query(Score).count(),
        "players": db.session.query(User).filter(User.total_pp.isnot(None)).count(),
        "beatmaps": db.session.query(Beatmap).count(),
    }
    return render_template(
        "index.html",
        stats=stats,
        first_places=recent_first_places(),
        high_pp=recent_high_pp(),
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
    pp_col = User.total_pp_all if include_unranked else User.total_pp
    acc_col = User.total_accuracy_all if include_unranked else User.total_accuracy

    score_sum = (
        db.session.query(Score.user_id, func.sum(Score.total_score).label("sc"))
        .filter(Score.is_best.is_(True), Score.hidden.is_(False))
        .group_by(Score.user_id)
        .subquery()
    )
    higher = aliased(User)
    global_rank = (
        db.session.query(func.count(higher.id))
        .filter(getattr(higher, pp_col.key) > pp_col)
        .correlate(User)
        .scalar_subquery()
    )
    q = (
        db.session.query(User, score_sum.c.sc, (global_rank + 1).label("rk"))
        .outerjoin(score_sum, score_sum.c.user_id == User.id)
        .filter(pp_col.isnot(None))
    )
    if country:
        q = q.filter(User.country_code == country)
    if search:
        q = q.filter(User.id == int(search)) if search.isdigit() else q.filter(
            User.username.ilike(f"%{search}%")
        )
    total = q.count()
    rows = q.order_by(pp_col.desc()).offset((page - 1) * PAGE).limit(PAGE).all()
    players = []
    for u, sc, rk in rows:
        d = _user_json(u)
        d["totalPp"] = getattr(u, pp_col.key)
        d["totalAccuracy"] = getattr(u, acc_col.key)
        d["totalScore"] = int(sc or 0)
        d["rank"] = int(rk)
        players.append(d)
    return jsonify({"players": players, "total": total, "page": page})


@api_bp.get("/beatmaps")
def api_beatmaps():
    page = max(1, request.args.get("page", 1, type=int))
    search = request.args.get("search")

    q = (
        db.session.query(Beatmap, func.count(Score.id).label("plays"))
        .join(Score, Score.beatmap_id == Beatmap.id)
        .group_by(Beatmap.id)
    )
    if search:
        if search.isdigit():
            q = q.filter(Beatmap.id == int(search))
        else:
            like = f"%{search}%"
            q = q.filter(
                Beatmap.artist.ilike(like)
                | Beatmap.title.ilike(like)
                | Beatmap.difficulty_name.ilike(like)
            )
    rows = (
        q.order_by(func.count(Score.id).desc())
        .offset((page - 1) * BEATMAP_PAGE)
        .limit(BEATMAP_PAGE)
        .all()
    )
    total = db.session.query(func.count(func.distinct(Score.beatmap_id))).scalar()
    out = [
        {
            "id": b.id,
            "artist": b.artist,
            "title": b.title,
            "difficultyName": b.difficulty_name,
            "starRating": b.star_rating,
            "starRatingNormal": b.star_rating_normal,
            "playcount": plays,
        }
        for b, plays in rows
    ]
    return jsonify({"beatmaps": out, "total": total, "page": page})


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
