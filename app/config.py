import os


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    _db_url = os.environ.get("DATABASE_URL", "sqlite:///ctbrx.db")
    # normalise the common postgres:// alias
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    OSU_CLIENT_ID = int(os.environ.get("OSU_CLIENT_ID", "0") or 0)
    OSU_CLIENT_SECRET = os.environ.get("OSU_CLIENT_SECRET", "")

    ENABLE_INGEST = _bool("ENABLE_INGEST")
    OSU_API_INTERVAL = float(os.environ.get("OSU_API_INTERVAL", "0.75"))
    FIREHOSE_INTERVAL = int(os.environ.get("FIREHOSE_INTERVAL", "5"))
    USER_REFRESH_INTERVAL = int(os.environ.get("USER_REFRESH_INTERVAL", "3600"))
    CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "1800"))
    FIREHOSE_BACKFILL = int(os.environ.get("FIREHOSE_BACKFILL", "200000"))
    SCHEDULER_LOCK_PORT = int(os.environ.get("SCHEDULER_LOCK_PORT", "47591"))

    BEATMAP_CACHE = os.environ.get("BEATMAP_CACHE", "beatmapcache")

    SITE_NAME = os.environ.get("SITE_NAME", "CTBRX")
    DISCORD_URL = os.environ.get("DISCORD_URL", "")
