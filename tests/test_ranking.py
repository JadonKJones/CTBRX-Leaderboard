import math

from app.models import Beatmap, Score, User
from app.pipeline import recalc_best_scores, recalc_player_pp, _weighted_totals


def test_weighted_totals_matches_formula():
    scores = [
        Score(pp=100.0, accuracy=1.0),
        Score(pp=50.0, accuracy=0.9),
        Score(pp=25.0, accuracy=0.8),
    ]
    total_pp, total_acc = _weighted_totals(scores)
    assert total_pp == 100 + 50 * 0.95 + 25 * 0.95**2
    expect_acc = (1.0 + 0.9 * 0.95 + 0.8 * 0.95**2) * 100 / (20 * (1 - 0.95**3))
    assert math.isclose(total_acc, expect_acc)


def _seed(db):
    db.session.add(Beatmap(id=1, status="ranked"))
    db.session.add(Beatmap(id=2, status="ranked"))
    db.session.add(User(id=1, username="a", country_code="US"))
    db.session.add_all([
        Score(id=1, user_id=1, beatmap_id=1, pp=100.0, accuracy=1.0, total_score=1),
        Score(id=2, user_id=1, beatmap_id=1, pp=120.0, accuracy=0.99, total_score=2),
        Score(id=3, user_id=1, beatmap_id=2, pp=80.0, accuracy=0.95, total_score=3),
    ])
    db.session.commit()


def test_best_and_player_pp(app, db):
    _seed(db)
    recalc_best_scores()
    recalc_player_pp()
    best = {s.id: s.is_best for s in db.session.query(Score).all()}
    assert best == {1: False, 2: True, 3: True}
    user = db.session.get(User, 1)
    assert math.isclose(user.total_pp, 120 + 80 * 0.95)
