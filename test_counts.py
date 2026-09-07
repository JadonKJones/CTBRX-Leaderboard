from app import create_app
from app.extensions import db
from app.models import Score, User, Beatmap
from sqlalchemy import func
from datetime import datetime, timezone, timedelta

app = create_app()
with app.app_context():
    days = 30
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    scores_counts = db.session.query(func.date(Score.date).label('d'), func.count().label('c')).filter(Score.date > start_date).group_by('d').all()
    print("scores:", scores_counts)
    
    users_counts = db.session.query(func.date(User.join_date).label('d'), func.count().label('c')).filter(User.join_date > start_date).group_by('d').all()
    print("users:", users_counts)
