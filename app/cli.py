from __future__ import annotations

import os

import click
from flask import current_app
from flask.cli import with_appcontext

from .extensions import db
from .models import Beatmap, Score, User
from .mods import has_relax, is_allowed
from .pipeline import (
    KEEP_STATUSES,
    build_api,
    cleanup,
    firehose_tick,
    is_catch,
    process_scores,
    recalc_best_scores,
    recalc_player_pp,
    recalc_score_pp,
    refresh_users,
    scan_beatmap,
)


def _api():
    return build_api(current_app)


def _recalc(cache, affected):
    n = recalc_score_pp(cache)
    if affected:
        recalc_best_scores(list(affected))
        recalc_player_pp(list(affected))
    return n


def register(app):
    @app.cli.command("osu-check")
    @with_appcontext
    def osu_check():
        """Verify osu! credentials and print a sample catch-RX score from the firehose."""
        scores = (_api().get_scores(None) or {}).get("scores", [])
        click.echo(f"token OK, firehose returned {len(scores)} scores")
        for s in scores:
            mods = [m.get("acronym") for m in (s.get("mods") or [])]
            if s.get("ruleset_id") == 2 and "RX" in mods:
                click.echo(f"  catch-RX example: score {s['id']} b/{s['beatmap_id']} mods {mods}")
                break
        else:
            click.echo("  (no catch-RX score in this batch - normal, they're rare)")

    @app.cli.command("ingest")
    @click.option("--loops", default=1, help="number of firehose ticks to run")
    @with_appcontext
    def ingest(loops):
        """Run firehose tick(s)."""
        api = _api()
        for i in range(loops):
            click.echo(f"tick {i + 1}/{loops}: {firehose_tick(app, api)} scores gained pp")

    @app.cli.command("check-user")
    @click.argument("user_id", type=int)
    @with_appcontext
    def check_user(user_id):
        """Show a user's recent/best osu!catch scores and whether each qualifies."""
        api = _api()
        scores = api.get_user_scores(user_id, "recent", limit=50)
        scores += api.get_user_scores(user_id, "best", limit=100)
        seen = set()
        for s in scores:
            sid = int(s["id"])
            if sid in seen:
                continue
            seen.add(sid)
            bm = s.get("beatmap") or {}
            reasons = []
            if not is_catch(s):
                reasons.append("not catch")
            if not s.get("passed", True):
                reasons.append("failed")
            if not has_relax(s.get("mods") or []):
                reasons.append("no RX")
            if not is_allowed(s.get("mods") or []):
                reasons.append("disallowed mod")
            if bm.get("status") not in KEEP_STATUSES:
                reasons.append(f"map {bm.get('status')}")
            if db.session.get(Score, sid) is not None:
                reasons.append("already stored")
            mods = [m.get("acronym") for m in (s.get("mods") or [])]
            click.echo(f"  score {sid}  b/{s['beatmap_id']}  mods={mods or ['NM']}  "
                       f"{'OK -> will ingest' if not reasons else ' / '.join(reasons)}")

    @app.cli.command("backfill")
    @click.argument("target")
    @with_appcontext
    def backfill(target):
        """Backfill players' recent+best catch-RX scores.

        TARGET is a username, a user id, or a path to a text file with one
        username/id per line.
        """
        api = _api()
        cache = current_app.config["BEATMAP_CACHE"]
        if os.path.isfile(target):
            names = [ln.strip() for ln in open(target, encoding="utf-8") if ln.strip()]
        else:
            names = [target]

        for i, name in enumerate(names, 1):
            try:
                if name.isdigit():
                    uid = int(name)
                else:
                    u = api.get_user(name, key="username")
                    if not u:
                        click.echo(f"[{i}/{len(names)}] {name}: NOT FOUND")
                        continue
                    uid = u["id"]
                scores = api.get_user_scores(uid, "recent", limit=50)
                scores += api.get_user_scores(uid, "best", limit=100)
                _recalc(cache, process_scores(api, scores, cache))
                row = db.session.get(User, uid)
                pp = f"{row.total_pp:.0f}pp" if row and row.total_pp else "no pp"
                click.echo(f"[{i}/{len(names)}] {name} -> {uid}: {len(scores)} seen, {pp}")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"[{i}/{len(names)}] {name}: ERROR {exc}")

    @app.cli.command("scan-map")
    @click.argument("beatmap_id", type=int)
    @with_appcontext
    def scan_map(beatmap_id):
        """Ingest a single beatmap's osu!catch RX leaderboard."""
        api = _api()
        cache = current_app.config["BEATMAP_CACHE"]
        affected = scan_beatmap(api, beatmap_id, cache)
        n = _recalc(cache, affected)
        click.echo(f"b/{beatmap_id}: {len(affected)} players affected, {n} scores got pp")

    @app.cli.command("discover-maps")
    @click.option("--pages", default=5, help="beatmapset search pages to walk (50 sets each)")
    @with_appcontext
    def discover_maps(pages):
        """Walk popular ranked osu!catch sets and ingest each difficulty's RX board."""
        api = _api()
        cache = current_app.config["BEATMAP_CACHE"]
        cursor, affected, scanned = None, set(), 0
        for page in range(pages):
            try:
                resp = api.search_beatmapsets(cursor_string=cursor)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"page {page + 1}: search failed ({exc}); stopping")
                break
            if not resp:
                break
            for bset in resp.get("beatmapsets", []):
                for bm in bset.get("beatmaps", []):
                    if bm.get("mode") != "fruits":
                        continue
                    try:
                        got = scan_beatmap(api, bm["id"], cache)
                    except Exception as exc:  # noqa: BLE001
                        click.echo(f"  b/{bm['id']}: skipped ({exc})")
                        continue
                    affected.update(got)
                    scanned += 1
                    if got:
                        click.echo(f"  b/{bm['id']} ({bset.get('title', '?')[:40]}): +{len(got)}")
            recalc_score_pp(cache)
            cursor = resp.get("cursor_string")
            click.echo(f"page {page + 1}/{pages}: {scanned} diffs, {len(affected)} players so far")
            if not cursor:
                break
        recalc_best_scores()
        recalc_player_pp()
        click.echo(f"done: {scanned} difficulties scanned, {len(affected)} players")

    @app.cli.command("backfill-beatmap-meta")
    @with_appcontext
    def backfill_beatmap_meta():
        """Re-fetch length/status for beatmaps missing a length."""
        api = _api()
        maps = db.session.query(Beatmap).filter(Beatmap.length == 0).all()
        click.echo(f"{len(maps)} beatmaps missing length")
        for bm in maps:
            data = api.get_beatmap(bm.id)
            if data:
                bm.length = data.get("total_length", 0) or 0
                bm.status = data.get("status", bm.status)
        db.session.commit()
        click.echo("done")

    @app.cli.command("recalc-pp")
    @click.option("--all", "recalc_all", is_flag=True, help="recompute every score, not just missing")
    @with_appcontext
    def recalc_pp_cmd(recalc_all):
        n = recalc_score_pp(current_app.config["BEATMAP_CACHE"], only_missing=not recalc_all)
        recalc_best_scores()
        recalc_player_pp()
        click.echo(f"{n} scores updated")

    @app.cli.command("refresh-users")
    @with_appcontext
    def refresh_users_cmd():
        """Re-fetch usernames/countries; hide gone players."""
        refresh_users(app, _api())

    @app.cli.command("cleanup")
    @with_appcontext
    def cleanup_cmd():
        """Full maintenance pass: purge fails, recalc pp/best/players, snapshot ranks."""
        cleanup(app)
