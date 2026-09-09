"""Ingestion + pp/ranking + scheduler.

This is the whole background pipeline in one place (was ingest.py + ranking.py +
jobs.py + scheduler.py). Ported from Relaxation Vault's LeaderboardUpdateService /
PpService / UserUpdateService / CleanupService.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .extensions import db
from .models import Beatmap, Meta, RankSnapshot, Score, User, utcnow
from .mods import has_relax, is_allowed, mods_to_strings
from .osu_api import OsuApiClient

log = logging.getLogger("ctbrx.pipeline")

CATCH_RULESET_ID = 2
KEEP_STATUSES = ("ranked", "approved", "loved")
RANKED_STATUSES = ("ranked", "approved")       # count toward the official ranking
PP_STATUSES = ("ranked", "approved", "loved")  # get pp computed at all
CURSOR_KEY = "firehose_cursor"
WEIGHT = 0.95
TOP_N = 1000


# --------------------------------------------------------------------------- #
#  ingestion
# --------------------------------------------------------------------------- #
def is_catch(score: dict) -> bool:
    return score.get("ruleset_id") == CATCH_RULESET_ID or score.get("mode") == "fruits"


def _parse_date(value):
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return utcnow()


def _catch_statistics(stats: dict) -> dict:
    """osu!lazer catch score statistics -> our columns."""
    return {
        "count_great": int(stats.get("great", 0)),
        "count_large_droplet": int(stats.get("large_tick_hit", 0)),
        "count_small_droplet": int(stats.get("small_tick_hit", 0)),
        "count_small_droplet_miss": int(stats.get("small_tick_miss", 0)),
        "count_miss": int(stats.get("miss", 0)),
    }


def _ensure_beatmap(api, beatmap_id: int, cache_path: str) -> Beatmap | None:
    bm = db.session.get(Beatmap, beatmap_id)
    if bm is not None:
        return bm

    data = api.get_beatmap(beatmap_id)
    if not data:
        return None
    # osu!catch ranks native "fruits" diffs and converted osu!standard maps
    if data.get("mode") not in ("fruits", "osu"):
        return None
    if data.get("status") not in KEEP_STATUSES:
        return None
    bset = data.get("beatmapset") or {}

    os.makedirs(cache_path, exist_ok=True)
    map_path = os.path.join(cache_path, f"{beatmap_id}.osu")
    if not os.path.exists(map_path) and not api.download_map(beatmap_id, map_path):
        log.warning("couldn't download beatmap %s", beatmap_id)
        return None

    bm = Beatmap(
        id=beatmap_id,
        artist=bset.get("artist", ""),
        title=bset.get("title", ""),
        creator_id=bset.get("user_id", 0),
        beatmapset_id=data.get("beatmapset_id", 0),
        difficulty_name=data.get("version", ""),
        approach_rate=data.get("ar", 0) or 0,
        circle_size=data.get("cs", 0) or 0,
        overall_difficulty=data.get("accuracy", 0) or 0,
        hp_drain=data.get("drain", 0) or 0,
        bpm=data.get("bpm", 0) or 0,
        count_circles=data.get("count_circles", 0) or 0,
        count_sliders=data.get("count_sliders", 0) or 0,
        count_spinners=data.get("count_spinners", 0) or 0,
        max_combo=data.get("max_combo", 0) or 0,
        length=data.get("total_length", 0) or 0,
        status=data.get("status", "graveyard"),
        star_rating_normal=data.get("difficulty_rating", 0) or 0,
    )
    db.session.add(bm)

    try:
        from .pp import star_rating

        bm.star_rating = star_rating(cache_path, beatmap_id)  # catch, RX
        if data.get("mode") == "osu":
            # difficulty_rating is the osu!std value for converts; use the
            # catch-converted nomod SR instead
            bm.star_rating_normal = star_rating(cache_path, beatmap_id, mods=[])
    except Exception as exc:  # noqa: BLE001
        log.warning("SR calc failed for %s: %s", beatmap_id, exc)

    db.session.flush()
    return bm


def _ensure_user(api, user_id: int, embedded: dict | None = None) -> User | None:
    user = db.session.get(User, user_id)
    if user is not None:
        return user

    data = embedded or api.get_user(user_id)
    if not data:
        return None
    user = User(
        id=user_id,
        username=data.get("username", ""),
        country_code=data.get("country_code", "XX"),
        join_date=_parse_date(data["join_date"]) if data.get("join_date") else None,
        updated_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    return user


def process_scores(api, scores: list[dict], cache_path: str) -> list[int]:
    """Store relevant passed catch-RX scores. Returns affected user ids."""
    affected: list[int] = []
    new_score_ids: list[int] = []

    for score in scores:
        if not is_catch(score):
            continue
        if not score.get("passed", True) or (score.get("rank") or "").upper() == "F":
            continue
        api_mods = score.get("mods") or []
        if not has_relax(api_mods) or not is_allowed(api_mods):
            continue

        score_id = int(score["id"])
        if db.session.get(Score, score_id) is not None:
            continue

        beatmap_id = int(score["beatmap_id"])
        try:
            if _ensure_beatmap(api, beatmap_id, cache_path) is None:
                continue
            user_id = int(score["user_id"])
            if _ensure_user(api, user_id, score.get("user")) is None:
                continue

            score_obj = Score(
                id=score_id,
                user_id=user_id,
                beatmap_id=beatmap_id,
                grade=(score.get("rank") or "D"),
                accuracy=float(score.get("accuracy", 0) or 0),
                combo=int(score.get("max_combo", 0) or 0),
                mods=mods_to_strings(api_mods),
                date=_parse_date(score.get("ended_at") or score.get("created_at")),
                total_score=int(score.get("total_score", 0) or 0),
                is_best=False,
                **_catch_statistics(score.get("statistics") or {}),
            )
            db.session.add(score_obj)
            new_score_ids.append(score_obj.id)
            if user_id not in affected:
                affected.append(user_id)
        except Exception:  # noqa: BLE001
            log.exception("failed to process score %s", score_id)
            db.session.rollback()

    db.session.commit()
    return affected, new_score_ids


# mod combos to pull per beatmap leaderboard (RX always added). Exotic combos
# still arrive via the firehose.
SCAN_MOD_COMBOS = (
    [], 
    # 1 mod
    ["HD"], ["HR"], ["DT"], ["EZ"], ["FL"], ["HT"],
    # 2 mods
    ["HD", "HR"], ["HD", "DT"], ["HD", "HT"], ["HD", "EZ"], ["HD", "FL"],
    ["HR", "DT"], ["HR", "FL"], ["HR", "HT"],
    ["EZ", "DT"], ["EZ", "FL"], ["EZ", "HT"],
    ["DT", "FL"], ["HT", "FL"],
    # 3 mods
    ["HD", "HR", "DT"], ["HD", "HR", "FL"], ["HD", "HR", "HT"],
    ["HD", "EZ", "DT"], ["HD", "EZ", "FL"], ["HD", "EZ", "HT"],
    ["HD", "DT", "FL"], ["HD", "HT", "FL"],
    ["HR", "DT", "FL"], ["HR", "HT", "FL"],
    ["EZ", "DT", "FL"], ["EZ", "HT", "FL"],
    # 4 mods
    ["HD", "HR", "DT", "FL"], ["HD", "HR", "HT", "FL"],
    ["HD", "EZ", "DT", "FL"], ["HD", "EZ", "HT", "FL"],
)


def scan_beatmap(api, beatmap_id: int, cache_path: str) -> list[int]:
    """Ingest a beatmap's osu!catch RX leaderboards across mod combos."""
    seen: dict[int, dict] = {}
    for combo in SCAN_MOD_COMBOS:
        for s in api.get_beatmap_scores(beatmap_id, mods=["RX"] + combo):
            s.setdefault("ruleset_id", CATCH_RULESET_ID)
            s.setdefault("beatmap_id", beatmap_id)
            seen[int(s["id"])] = s
    affected, _ = process_scores(api, list(seen.values()), cache_path)
    return affected


def firehose_tick(app, api) -> int:
    """One iteration of the score firehose. Returns scores that gained pp."""
    cache_path = app.config["BEATMAP_CACHE"]
    with app.app_context():
        cursor = Meta.get(CURSOR_KEY)
        if cursor is None:
            # cold start: jump back so we catch up on missed scores
            head = api.get_scores(None)
            if not head or not head.get("scores"):
                return 0
            max_id = max(int(s["id"]) for s in head["scores"])
            cursor = base64.b64encode(
                json.dumps({"id": max_id - app.config["FIREHOSE_BACKFILL"]}).encode()
            ).decode()

        resp = api.get_scores(cursor)
        if not resp:
            return 0
        batch = resp.get("scores", [])
        next_cursor = resp.get("cursor_string")

        if not batch:
            if next_cursor:
                Meta.set(CURSOR_KEY, next_cursor)
                db.session.commit()
            return 0

        before = db.session.query(Score).count()
        affected, new_score_ids = process_scores(api, batch, cache_path)
        stored = db.session.query(Score).count() - before
        new_pp = recalc_score_pp(cache_path)
        if affected:
            recalc_best_scores(affected)
            recalc_player_pp(affected)

            # Check for new top plays and send discord message
            bot_token = os.environ.get("DISCORD_TOKEN")
            channel_id = os.environ.get("DISCORD_CHANNEL_ID")
            if bot_token and channel_id and new_score_ids:
                new_bests = db.session.query(Score).filter(Score.id.in_(new_score_ids), Score.is_best.is_(True)).all()
                
                best_global = db.session.query(Score.id).filter(Score.hidden.is_(False), Score.pp.isnot(None)).order_by(Score.pp.desc()).first()
                best_global_id = best_global.id if best_global else None
                
                for best in new_bests:
                    if best.pp and best.pp > 0:
                        best_map = db.session.query(Score.id).filter(Score.beatmap_id == best.beatmap_id, Score.hidden.is_(False)).order_by(Score.pp.desc().nulls_last(), Score.total_score.desc()).first()
                        is_beatmap_1 = best_map and best_map.id == best.id
                        is_global_1 = best.id == best_global_id
                        
                        if not is_beatmap_1 and not is_global_1:
                            continue

                        content = "@here" if is_global_1 else ""
                        title_text = "New Server PP Record!" if is_global_1 else "New Beatmap #1!"

                        try:
                            import requests
                            user = db.session.get(User, best.user_id)
                            beatmap = db.session.get(Beatmap, best.beatmap_id)
                            embed = {
                                "title": title_text,
                                "description": f"**{user.username}** just set a new #1 score on **{beatmap.title} [{beatmap.difficulty_name}]**!\n\n**Accuracy:** {best.accuracy * 100:.2f}%\n**PP:** {best.pp:.0f}pp",
                                "color": 15844367, # Gold
                                "thumbnail": {
                                    "url": f"https://a.ppy.sh/{user.id}"
                                },
                                "url": f"https://osu.ppy.sh/beatmaps/{beatmap.id}"
                            }
                            import json
                            payload = {"content": content, "embeds": [embed]}
                            files = {}
                            
                            if is_global_1:
                                try:
                                    replay_data = api.download_replay(best.id)
                                    if replay_data:
                                        files["files[0]"] = (f"{best.id}.osr", replay_data, "application/octet-stream")
                                except Exception as e:
                                    log.warning("Failed to fetch replay for discord: %s", e)

                            if files:
                                files["payload_json"] = (None, json.dumps(payload))
                                requests.post(
                                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                                    headers={"Authorization": f"Bot {bot_token}"},
                                    files=files,
                                    timeout=15
                                )
                            else:
                                requests.post(
                                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                                    headers={"Authorization": f"Bot {bot_token}"},
                                    json=payload,
                                    timeout=5
                                )
                        except Exception as e:
                            log.error("Failed to send Discord message: %s", e)

        log.info(
            "firehose batch=%d newest_id=%d stored=%d pp_calc=%d (total=%d)",
            len(batch), max(int(s["id"]) for s in batch), stored, new_pp, before + stored,
        )
        if next_cursor:
            Meta.set(CURSOR_KEY, next_cursor)
        db.session.commit()
        return new_pp


# --------------------------------------------------------------------------- #
#  pp / ranking
# --------------------------------------------------------------------------- #
def recalc_score_pp(cache_path: str, only_missing: bool = True) -> int:
    """Compute pp for ranked/approved/loved scores. Returns number updated."""
    from .pp import calculate_pp

    q = (
        db.session.query(Score)
        .join(Beatmap, Score.beatmap_id == Beatmap.id)
        .filter(Beatmap.status.in_(PP_STATUSES))
    )
    if only_missing:
        q = q.filter(Score.pp.is_(None))

    updated = 0
    for s in q.all():
        try:
            value = calculate_pp(
                cache_path, s.beatmap_id,
                mods=s.mods or [], accuracy=s.accuracy, combo=s.combo,
                count_great=s.count_great,
                count_large_droplet=s.count_large_droplet,
                count_small_droplet=s.count_small_droplet,
                count_small_droplet_miss=s.count_small_droplet_miss,
                count_miss=s.count_miss,
            )
        except Exception:  # noqa: BLE001  (missing .osu, bad map, calc error)
            continue
        if s.pp != value:
            s.pp = value
            updated += 1
    db.session.commit()
    return updated


def recalc_best_scores(user_ids: list[int] | None = None) -> None:
    """Mark the single best (pp, then total_score) score per (user, beatmap)."""
    q = db.session.query(Score).filter(Score.hidden.is_(False))
    if user_ids:
        q = q.filter(Score.user_id.in_(user_ids))

    groups: dict[tuple[int, int], list[Score]] = {}
    for s in q.all():
        groups.setdefault((s.user_id, s.beatmap_id), []).append(s)

    for members in groups.values():
        members.sort(key=lambda s: (s.pp if s.pp is not None else -1.0, s.total_score), reverse=True)
        for i, s in enumerate(members):
            if s.is_best != (i == 0):
                s.is_best = i == 0
    db.session.commit()


def _weighted_totals(scores: list[Score]) -> tuple[float | None, float | None]:
    scores = sorted(scores, key=lambda s: s.pp or 0.0, reverse=True)[:TOP_N]
    if not scores:
        return None, None
    factor, total_pp, total_acc = 1.0, 0.0, 0.0
    for s in scores:
        total_pp += (s.pp or 0.0) * factor
        total_acc += s.accuracy * factor
        factor *= WEIGHT
    total_acc *= 100.0 / (20.0 * (1.0 - math.pow(WEIGHT, len(scores))))
    return total_pp, max(0.0, min(100.0, total_acc))


def recalc_player_pp(user_ids: list[int] | None = None) -> None:
    users = db.session.query(User)
    if user_ids:
        users = users.filter(User.id.in_(user_ids))

    for user in users.all():
        rows = (
            db.session.query(Score, Beatmap.status)
            .join(Beatmap, Score.beatmap_id == Beatmap.id)
            .filter(Score.user_id == user.id, Score.hidden.is_(False), Score.pp.isnot(None))
            .all()
        )
        best_ranked: dict[int, Score] = {}   # ranked/approved only -> total_pp
        best_all: dict[int, Score] = {}      # + loved             -> total_pp_all
        for s, status in rows:
            buckets = [best_all]
            if status in RANKED_STATUSES:
                buckets.append(best_ranked)
            for b in buckets:
                cur = b.get(s.beatmap_id)
                if cur is None or (s.pp or 0) > (cur.pp or 0):
                    b[s.beatmap_id] = s
        user.total_pp, user.total_accuracy = _weighted_totals(list(best_ranked.values()))
        user.total_pp_all, user.total_accuracy_all = _weighted_totals(list(best_all.values()))
    db.session.commit()


# --------------------------------------------------------------------------- #
#  maintenance jobs
# --------------------------------------------------------------------------- #
def refresh_users(app, api) -> None:
    with app.app_context():
        ids = [u.id for u in db.session.query(User.id).order_by(User.updated_at.asc()).all()]
        log.info("refreshing %d users", len(ids))
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            try:
                fetched = {u["id"]: u for u in api.get_users(chunk)}
            except Exception:  # noqa: BLE001
                log.exception("user lookup failed")
                continue
            for uid in chunk:
                user = db.session.get(User, uid)
                if user is None:
                    continue
                data = fetched.get(uid)
                if data is None:
                    # user gone: hide their scores and recalc
                    hidden = db.session.query(Score).filter(
                        Score.user_id == uid, Score.hidden.is_(False)
                    ).update({Score.hidden: True})
                    db.session.commit()
                    if hidden:
                        recalc_best_scores([uid])
                        recalc_player_pp([uid])
                else:
                    user.username = data.get("username", user.username)
                    user.country_code = data.get("country_code", user.country_code)
                    if data.get("join_date") and user.join_date is None:
                        user.join_date = _parse_date(data["join_date"])
                    user.updated_at = utcnow()
            db.session.commit()
        log.info("user refresh done")


def snapshot_ranks() -> int:
    """Record today's global rank + pp for every ranked player (idempotent per day)."""
    today = date.today()
    users = (
        db.session.query(User.id, User.total_pp)
        .filter(User.total_pp.isnot(None))
        .order_by(User.total_pp.desc())
        .all()
    )
    for rank, (uid, pp) in enumerate(users, 1):
        row = db.session.get(RankSnapshot, (uid, today))
        if row is None:
            db.session.add(RankSnapshot(user_id=uid, date=today, rank=rank, pp=pp))
        else:
            row.rank, row.pp = rank, pp
    db.session.commit()
    return len(users)


def cleanup(app) -> None:
    with app.app_context():
        cache_path = app.config["BEATMAP_CACHE"]

        # drop failed scores - never leaderboard-eligible
        db.session.query(Score).filter(Score.grade == "F").delete()
        db.session.commit()

        recalc_score_pp(cache_path)

        # strip pp from scores whose map is no longer ranked/approved/loved
        stale = (
            db.session.query(Score)
            .join(Beatmap, Score.beatmap_id == Beatmap.id)
            .filter(Score.pp.isnot(None), ~Beatmap.status.in_(PP_STATUSES))
            .all()
        )
        for s in stale:
            s.pp = None
        db.session.commit()

        recalc_best_scores()
        recalc_player_pp()
        snapshot_ranks()
        log.info("cleanup done")


# --------------------------------------------------------------------------- #
#  scheduler
# --------------------------------------------------------------------------- #
def build_api(app) -> OsuApiClient:
    return OsuApiClient(
        app.config["OSU_CLIENT_ID"],
        app.config["OSU_CLIENT_SECRET"],
        interval=app.config["OSU_API_INTERVAL"],
    )


def award_best_improved(app) -> None:
    from .leaderboards import best_improved
    from .models import UserBadge
    
    with app.app_context():
        # Get the top 3 most improved players
        winners = best_improved(days=7, limit=3)
        if not winners:
            log.info("No winners for weekly best improved")
            return
            
        week_num = date.today().isocalendar()[1]
        badge_name = f"Best Improved - Week {week_num}"
        
        for winner in winners:
            uid = winner["user"].id
            
            # Avoid duplicate awards if run manually multiple times
            existing = db.session.query(UserBadge).filter_by(user_id=uid, badge_name=badge_name).first()
            if not existing:
                badge = UserBadge(user_id=uid, badge_name=badge_name)
                db.session.add(badge)
                log.info("Awarded %s to user %s (Delta: %s)", badge_name, uid, winner["delta"])
            else:
                log.info("User %s already has %s", uid, badge_name)
                
        db.session.commit()


def start_scheduler(app) -> BackgroundScheduler:
    api = build_api(app)
    sched = BackgroundScheduler(daemon=True, timezone="UTC")

    def _job(fn, *args):
        def run():
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                log.exception("%s failed", fn.__name__)
        return run

    sched.add_job(_job(firehose_tick, app, api), "interval",
                  seconds=app.config["FIREHOSE_INTERVAL"], id="firehose",
                  max_instances=1, coalesce=True)
    sched.add_job(_job(refresh_users, app, api), "interval",
                  seconds=app.config["USER_REFRESH_INTERVAL"], id="refresh",
                  max_instances=1, coalesce=True)
    sched.add_job(_job(cleanup, app), "interval",
                  seconds=app.config["CLEANUP_INTERVAL"], id="cleanup",
                  max_instances=1, coalesce=True)
    
    # Run the weekly best improved award every Saturday at 00:00 UTC
    sched.add_job(_job(award_best_improved, app), "cron",
                  day_of_week="sat", hour=0, minute=0, id="weekly_improver",
                  max_instances=1, coalesce=True)

    sched.start()
    log.info("scheduler started (firehose every %ss)", app.config["FIREHOSE_INTERVAL"])
    return sched
