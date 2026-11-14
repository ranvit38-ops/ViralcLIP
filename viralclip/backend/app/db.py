from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

_sqlite = config.DATABASE_URL.startswith("sqlite")
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if _sqlite else {},
)

if _sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")  # SQLite ignores FKs/cascades without this
        cur.execute("PRAGMA journal_mode=WAL")  # API + worker processes share the file
        cur.close()

SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def alembic_config():
    from pathlib import Path
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return cfg


def init_db():
    """Bring the schema to the latest migration. Safe to call from API and workers at once."""
    import time
    from alembic import command
    from sqlalchemy import inspect
    from . import models  # noqa: F401  (register tables)
    for attempt in range(10):
        try:
            insp = inspect(engine)
            if insp.has_table("users") and not insp.has_table("alembic_version"):
                # DB created before migrations existed: add any tables it lacks, then mark it current.
                Base.metadata.create_all(engine)
                command.stamp(alembic_config(), "head")
            else:
                command.upgrade(alembic_config(), "head")
            return
        except Exception:  # another process is migrating at the same moment; let it finish
            if attempt == 9:
                raise
            time.sleep(1 + attempt)
