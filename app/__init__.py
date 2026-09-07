from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

from .config import Config  # noqa: E402
from .extensions import db, migrate  # noqa: E402


_PRAGMAS_REGISTERED = False


def _register_sqlite_pragmas() -> None:
    """WAL + busy timeout so the web app, firehose and CLI don't lock each other out."""
    global _PRAGMAS_REGISTERED
    if _PRAGMAS_REGISTERED:
        return
    _PRAGMAS_REGISTERED = True

    import sqlite3

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
        if not isinstance(dbapi_conn, sqlite3.Connection):
            return
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)

    os.makedirs(app.instance_path, exist_ok=True)
    # keep a relative sqlite path inside the instance folder
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if (
        uri.startswith("sqlite:///")
        and not uri.startswith("sqlite:////")
        and ":memory:" not in uri
    ):
        rel = uri[len("sqlite:///"):]
        if rel and not os.path.isabs(rel):
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
                app.instance_path, rel
            )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db.init_app(app)
    migrate.init_app(app, db)
    _register_sqlite_pragmas()

    from . import models  # noqa: F401  (register models)
    from .cli import register as register_cli
    from .web import api_bp, bp as web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)
    register_cli(app)

    @app.context_processor
    def inject_site():
        return {
            "SITE_NAME": app.config["SITE_NAME"],
            "DISCORD_URL": app.config["DISCORD_URL"],
        }

    _maybe_start_scheduler(app)
    return app


def _maybe_start_scheduler(app: Flask) -> None:
    """Start the in-process firehose/maintenance scheduler.

    Runs only inside `flask run`'s reloaded child, or when RUN_SCHEDULER=1
    (set that for waitress/gunicorn). A socket lock guarantees one holder per
    machine, so a stray extra process just skips.
    """
    if not app.config.get("ENABLE_INGEST"):
        return
    if not app.config.get("OSU_CLIENT_SECRET"):
        app.logger.warning("ENABLE_INGEST set but OSU_CLIENT_SECRET missing - scheduler off")
        return
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true" and os.environ.get("RUN_SCHEDULER") != "1":
        return

    import socket

    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", app.config["SCHEDULER_LOCK_PORT"]))
    except OSError:
        app.logger.warning("scheduler already running elsewhere - not starting another")
        lock.close()
        return
    app.extensions["_scheduler_lock"] = lock  # keep the socket alive for process lifetime

    from .pipeline import start_scheduler

    app.extensions["scheduler"] = start_scheduler(app)
