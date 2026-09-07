import json
from wsgi import app
from app.extensions import db
from app.models import Score

with app.app_context():
    scores = db.session.query(Score).all()
    changed = 0
    for s in scores:
        if s.mods:
            mods_list = s.mods
            new_mods = [m for m in mods_list if m not in ("MR", "PF", "SD")]
            if len(new_mods) != len(mods_list):
                s.mods = new_mods
                changed += 1
    db.session.commit()
    print(f"Fixed {changed} scores.")
