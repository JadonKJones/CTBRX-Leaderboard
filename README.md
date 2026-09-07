# CTBRX

An osu!lazer **osu!catch + Relax** leaderboard — a Flask reimplementation of
[Relaxation Vault](https://rx.stanr.info/) ([stanriders/rxleaderboard](https://github.com/stanriders/rxleaderboard))
for the catch ruleset.

It reads the global osu!lazer score feed (`GET /api/v2/scores`), keeps every osu!catch score that used the
Relax mod, recomputes star rating and pp locally with [rosu-pp](https://github.com/MaxOhn/rosu-pp-py)
(there is no official Relax pp for catch, so it uses normal catch pp — same approach RV takes for osu!std),
and serves player / beatmap / score leaderboards.

## Stack

- Flask + Jinja templates (Windows-98 retro theme), single process — no Docker, no separate frontend
- SQLAlchemy + Flask-Migrate; **SQLite by default**, `DATABASE_URL` can point at Postgres
- APScheduler for background ingestion (or run it from `cron` / Task Scheduler instead)
- `rosu-pp-py` for difficulty / pp

## Setup

Requires **Python 3.12 or 3.13** (rosu-pp-py wheels lag newer interpreters).

```bash
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Create an osu! OAuth client at <https://osu.ppy.sh/home/account/edit> → *OAuth* → *New OAuth Application*
(callback URL can be anything — the client-credentials grant doesn't use it). Put the id/secret in `.env`:

```
OSU_CLIENT_ID=12345
OSU_CLIENT_SECRET=xxxxxxxx
```

Create the database:

```bash
set FLASK_APP=wsgi.py
.venv\Scripts\flask db upgrade
```

## Running

```bash
# dev
.venv\Scripts\flask run --debug

# prod (Windows-friendly)
.venv\Scripts\waitress-serve --port=8000 wsgi:app
```

### Ingestion

Either let the web process run it:

```
ENABLE_INGEST=1        # in .env; starts the APScheduler jobs inside the app
```

When running under waitress/gunicorn, also set `RUN_SCHEDULER=1` so the worker starts the
in-process jobs (`flask run` starts them automatically). A machine-wide socket lock means a
stray second process just skips.

…or keep `ENABLE_INGEST=0` / `RUN_SCHEDULER` unset and drive it from cron / Task Scheduler:

```bash
.venv\Scripts\flask ingest --loops 200     # run 200 firehose ticks
.venv\Scripts\flask cleanup                 # every ~30 min
```

## CLI

| command | what |
|---|---|
| `flask osu-check` | verify credentials, print a sample catch-RX score from the firehose |
| `flask ingest [--loops N]` | run firehose tick(s) |
| `flask check-user <id>` | show a player's recent catch scores and why each is / isn't kept |
| `flask backfill <name\|id\|file>` | pull recent + best catch-RX scores for a player, or a newline-delimited list (limited — see below) |
| `flask scan-map <beatmap_id>` | ingest a beatmap's catch-RX leaderboards (all common mod combos) |
| `flask discover-maps [--pages N]` | walk popular ranked catch sets, scan every difficulty's RX boards |
| `flask backfill-beatmap-meta` | re-fetch length/status for beatmaps missing a length |
| `flask recalc-pp [--all]` | recompute score pp + best scores + player totals |
| `flask refresh-users` | refresh usernames/countries + join dates, hide wiped players |
| `flask cleanup` | full maintenance pass (purge fails, recalc, snapshot today's ranks) |

**Seeding:** the firehose only sees scores set *after* it starts, and osu's API has no
"a player's Relax history" endpoint (`/scores/best` excludes unranked mods, `/recent` is 24h).
So for an initial population run `flask discover-maps --pages 30` — it pulls per-beatmap RX
leaderboards (across NM/HD/HR/DT/EZ/FL combos), which is how Relaxation Vault bootstraps too.
It's API-heavy (~10 calls/difficulty); run it once, then let the firehose maintain.

## Pages

`/leaderboard` (global) · `/countries` (weighted per-country) · `/mods` (mod-combo **selector**;
toggle EZ/HD/HR/DT/… to rank the best players for that exact combo) · `/topscores` · `/beatmaps`
· `/users/<id|name>`.

**Profile** shows: global/peak/country rank, pp, level, ranked & total score, playcount,
play time (approx), total hits, max combo, accuracy, grade counts (XH/X/SH/S/A), first-place
count; **over-time graphs** (rank, pp, cumulative playcount, with All/90d/30d range); and
top scores / first-place ranks / most-played / recent sections. No followers/badges/medals.
The rank graph needs a few days of daily snapshots (written by `cleanup`) before it shows.

## Dev

```bash
.venv\Scripts\pytest
.venv\Scripts\python scripts\pp_probe.py <beatmap_id> [accuracy] [mods...]
.venv\Scripts\python scripts\seed_demo.py     # fake data for UI work (wipes the DB!)
```

## Notes

- `scores.mods` stores strings like `["RX", "HD", "DTx1.3"]`; rate-changed mods keep their `speed_change`.
- Allowed mods are in `app/mods.py`; a score using anything else is ignored entirely.
- The monthly-playcount aggregation is done in Python, so SQLite and Postgres both work.
- Not affiliated with osu! / ppy.
