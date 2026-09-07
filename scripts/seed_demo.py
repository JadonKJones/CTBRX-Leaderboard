"""Insert fake data so the UI can be viewed without live ingestion. NOT for production."""
from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models import Beatmap, Score, User
from app.pipeline import recalc_best_scores, recalc_player_pp

app = create_app()
with app.app_context():
    db.session.query(Score).delete()
    db.session.query(User).delete()
    db.session.query(Beatmap).delete()

    maps = [
        Beatmap(id=100001, artist="Demo Artist", title="Overdrive", creator_id=1, beatmapset_id=1,
                difficulty_name="Platter", approach_rate=9, circle_size=4, overall_difficulty=8,
                hp_drain=5, bpm=220, count_circles=800, count_sliders=120, count_spinners=2,
                max_combo=1400, status="ranked", star_rating_normal=5.1, star_rating=5.4),
        Beatmap(id=100002, artist="Another", title="Rain Dance", creator_id=2, beatmapset_id=2,
                difficulty_name="Rain", approach_rate=9.3, circle_size=3.6, overall_difficulty=9,
                hp_drain=6, bpm=175, count_circles=650, count_sliders=90, count_spinners=1,
                max_combo=1100, status="ranked", star_rating_normal=6.0, star_rating=6.3),
    ]
    db.session.add_all(maps)

    users = [
        User(id=2001, username="fruitgod", country_code="US", updated_at=datetime.now(timezone.utc)),
        User(id=2002, username="catcher9", country_code="JP", updated_at=datetime.now(timezone.utc)),
        User(id=2003, username="dropletking", country_code="DE", updated_at=datetime.now(timezone.utc)),
    ]
    db.session.add_all(users)
    db.session.flush()

    rows = [
        (9001, 2001, 100001, "X", 1.0, 1400, ["RX", "HD"], 800, 120, 300, 0, 0, 612.4),
        (9002, 2002, 100001, "S", 0.985, 1360, ["RX"], 790, 118, 290, 4, 6, 540.1),
        (9003, 2003, 100001, "S", 0.972, 1300, ["RX", "DTx1.4"], 780, 110, 280, 8, 12, 501.9),
        (9004, 2001, 100002, "X", 1.0, 1100, ["RX"], 650, 90, 200, 0, 0, 705.0),
        (9005, 2002, 100002, "A", 0.94, 980, ["RX", "HR"], 610, 82, 180, 20, 30, 410.2),
    ]
    for sid, uid, bid, grade, acc, combo, mods, g, ld, sd, sdm, miss, pp in rows:
        db.session.add(Score(id=sid, user_id=uid, beatmap_id=bid, grade=grade, accuracy=acc,
                             combo=combo, mods=mods, date=datetime.now(timezone.utc) - timedelta(hours=sid % 50),
                             total_score=1_000_000 - sid, count_great=g, count_large_droplet=ld,
                             count_small_droplet=sd, count_small_droplet_miss=sdm, count_miss=miss,
                             pp=pp, is_best=False))
    db.session.commit()
    recalc_best_scores()
    recalc_player_pp()
    print("seeded:", db.session.query(Score).count(), "scores",
          db.session.query(User).count(), "users")
