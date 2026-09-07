from datetime import datetime, timezone

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    username = db.Column(db.String(64), nullable=False, default="")
    country_code = db.Column(db.String(4), nullable=False, default="XX")
    total_pp = db.Column(db.Float, nullable=True, index=True)          # ranked/approved maps only
    total_accuracy = db.Column(db.Float, nullable=True)
    total_pp_all = db.Column(db.Float, nullable=True, index=True)      # + loved maps
    total_accuracy_all = db.Column(db.Float, nullable=True)
    join_date = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True, default=utcnow)

    scores = db.relationship("Score", back_populates="user", lazy="dynamic")


class Beatmap(db.Model):
    __tablename__ = "beatmaps"

    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    artist = db.Column(db.String(256), nullable=False, default="")
    title = db.Column(db.String(256), nullable=False, default="")
    creator_id = db.Column(db.Integer, nullable=False, default=0)
    beatmapset_id = db.Column(db.Integer, nullable=False, default=0)
    difficulty_name = db.Column(db.String(256), nullable=False, default="")

    approach_rate = db.Column(db.Float, nullable=False, default=0)
    circle_size = db.Column(db.Float, nullable=False, default=0)
    overall_difficulty = db.Column(db.Float, nullable=False, default=0)
    hp_drain = db.Column(db.Float, nullable=False, default=0)
    bpm = db.Column(db.Float, nullable=False, default=0)

    count_circles = db.Column(db.Integer, nullable=False, default=0)
    count_sliders = db.Column(db.Integer, nullable=False, default=0)
    count_spinners = db.Column(db.Integer, nullable=False, default=0)
    max_combo = db.Column(db.Integer, nullable=False, default=0)
    length = db.Column(db.Integer, nullable=False, default=0)  # drain/total seconds

    status = db.Column(db.String(16), nullable=False, default="graveyard")
    star_rating_normal = db.Column(db.Float, nullable=False, default=0)
    star_rating = db.Column(db.Float, nullable=True)

    scores = db.relationship("Score", back_populates="beatmap", lazy="dynamic")


class Score(db.Model):
    __tablename__ = "scores"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    beatmap_id = db.Column(db.Integer, db.ForeignKey("beatmaps.id"), nullable=False, index=True)

    grade = db.Column(db.String(4), nullable=False, default="D")
    accuracy = db.Column(db.Float, nullable=False, default=0)      # 0..1
    combo = db.Column(db.Integer, nullable=False, default=0)
    mods = db.Column(db.JSON, nullable=False, default=list)         # ["HD", "DTx1.3", "RX"]
    date = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    total_score = db.Column(db.BigInteger, nullable=False, default=0)

    count_great = db.Column(db.Integer, nullable=False, default=0)          # fruits caught
    count_large_droplet = db.Column(db.Integer, nullable=False, default=0)  # droplets
    count_small_droplet = db.Column(db.Integer, nullable=False, default=0)  # tiny droplets
    count_small_droplet_miss = db.Column(db.Integer, nullable=False, default=0)
    count_miss = db.Column(db.Integer, nullable=False, default=0)

    pp = db.Column(db.Float, nullable=True, index=True)
    is_best = db.Column(db.Boolean, nullable=False, default=False)
    hidden = db.Column(db.Boolean, nullable=False, default=False)
    deleted = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", back_populates="scores")
    beatmap = db.relationship("Beatmap", back_populates="scores")

    __table_args__ = (
        db.Index("ix_scores_beatmap_user", "beatmap_id", "user_id"),
    )


class RankSnapshot(db.Model):
    """One row per ranked user per day: their global rank + pp at that time.

    Powers the rank/pp-over-time graph and 'peak rank'. Written by the daily job.
    """

    __tablename__ = "rank_snapshots"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    date = db.Column(db.Date, primary_key=True)
    rank = db.Column(db.Integer, nullable=False)
    pp = db.Column(db.Float, nullable=False)


class Meta(db.Model):
    """Tiny key/value store for ingestion state (e.g. the firehose cursor)."""

    __tablename__ = "meta"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(512), nullable=True)

    @staticmethod
    def get(key: str, default=None):
        row = db.session.get(Meta, key)
        return row.value if row is not None else default

    @staticmethod
    def set(key: str, value) -> None:
        row = db.session.get(Meta, key)
        if row is None:
            row = Meta(key=key)
            db.session.add(row)
        row.value = None if value is None else str(value)
