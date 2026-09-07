from datetime import datetime, timezone

from app.models import Beatmap, Score, User
from app.pipeline import recalc_best_scores, recalc_player_pp


def _seed(db):
    db.session.add(Beatmap(id=10, artist="A", title="T", difficulty_name="Rain",
                           status="ranked", star_rating=5.0, star_rating_normal=4.5))
    db.session.add(User(id=5, username="picker", country_code="US"))
    db.session.add(Score(id=1, user_id=5, beatmap_id=10, grade="S", accuracy=0.98,
                         combo=900, mods=["RX"], date=datetime.now(timezone.utc),
                         total_score=500000, count_great=800, pp=250.0))
    db.session.commit()
    recalc_best_scores()
    recalc_player_pp()


def test_pages_ok(client, db):
    _seed(db)
    for path in ["/", "/leaderboard", "/topscores", "/beatmaps", "/beatmaps/10",
                 "/users/5", "/users/picker", "/faq"]:
        assert client.get(path).status_code == 200, path


def test_api(client, db):
    _seed(db)
    assert client.get("/api/players").get_json()["total"] == 1
    assert client.get("/api/beatmaps").get_json()["beatmaps"][0]["playcount"] == 1
    assert client.get("/api/mod-leaderboard/NM").get_json()[0]["userId"] == 5


def test_missing_404(client, db):
    assert client.get("/users/ghost").status_code == 404
    assert client.get("/beatmaps/9999").status_code == 404
