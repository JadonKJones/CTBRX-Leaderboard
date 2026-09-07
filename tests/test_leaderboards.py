from datetime import datetime, timedelta, timezone

from app.leaderboards import (
    available_combos,
    country_leaderboard,
    first_places,
    mod_combo,
    mod_leaderboard,
    osu_level,
    player_profile_stats,
    player_timelines,
)
from app.models import Beatmap, Score, User
from app.pipeline import recalc_best_scores, recalc_player_pp


def test_mod_combo_normalisation():
    assert mod_combo(["RX", "HD", "DTx1.5"]) == "DTHD"
    assert mod_combo(["RX"]) == "NM"
    assert mod_combo(["HD", "EZ", "RX"]) == "EZHD"


def _seed(db):
    db.session.add_all([Beatmap(id=i, status="ranked") for i in (1, 2, 3)])
    db.session.add_all([
        User(id=1, username="a", country_code="US"),
        User(id=2, username="b", country_code="US"),
        User(id=3, username="c", country_code="JP"),
    ])
    base = datetime.now(timezone.utc) - timedelta(days=10)
    rows = [
        (1, 1, 1, ["RX", "DT"], 100.0), (2, 1, 2, ["RX", "DT"], 80.0),
        (3, 2, 1, ["RX", "DT"], 120.0),
        (4, 3, 1, ["RX"], 50.0), (5, 1, 3, ["RX"], 40.0),
    ]
    for i, (sid, uid, bid, mods, pp) in enumerate(rows):
        db.session.add(Score(id=sid, user_id=uid, beatmap_id=bid, mods=mods, pp=pp,
                             accuracy=0.97, combo=100, total_score=1, grade="S",
                             date=base + timedelta(hours=i), is_best=False))
    db.session.commit()
    recalc_best_scores()
    recalc_player_pp()


def test_mod_leaderboard_and_combos(app, db):
    _seed(db)
    combos = dict(available_combos(min_scores=1))
    assert combos["DT"] == 3 and combos["NM"] == 2

    dt = mod_leaderboard("DT")
    assert [r["user_id"] for r in dt] == [1, 2]  # user 1 has two DT maps -> more weighted pp
    assert dt[0]["plays"] == 2


def test_country_leaderboard(app, db):
    _seed(db)
    rows = country_leaderboard()
    assert rows[0]["country"] == "US"
    assert rows[0]["players"] == 2


def test_player_timelines(app, db):
    _seed(db)
    tl = player_timelines(1)
    assert tl["playcount"][-1][1] == 3           # user 1 set 3 scores
    assert tl["pp"][-1][1] >= tl["pp"][0][1]     # pp is monotonic non-decreasing
    assert tl["peak_rank"] is None               # no snapshots seeded


def test_osu_level_curve():
    assert osu_level(0)[0] == 1
    assert osu_level(1_000_000)[0] == 5
    assert osu_level(175_000_000)[0] == 29       # matches osu! for ~173M


def test_profile_stats_and_first_places(app, db):
    _seed(db)
    stats = player_profile_stats(1)
    assert stats["total_score"] == 3             # seeded total_score values 1+2 (best) ... sum of all
    assert stats["max_combo"] == 100
    assert set(stats["grades"]) == {"XH", "X", "SH", "S", "A"}

    n, scores = first_places(1)
    assert n == 2                                # user 1 tops beatmap 1 (120pp) and 2 (80pp)


def test_homepage_feeds(app, db):
    from app.leaderboards import recent_first_places, top_pp_scores, trending_beatmaps

    _seed(db)
    assert [s.pp for s in top_pp_scores()][:2] == [120.0, 100.0]
    fp = recent_first_places()
    assert all(s.pp is not None for s in fp)
    assert {t["beatmap"].id for t in trending_beatmaps(days=365)} <= {1, 2, 3}
