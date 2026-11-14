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


def init_db():
    from . import models  # noqa: F401  (register tables)
    # ponytail: create_all only adds missing tables; switch to Alembic before the first
    # release that changes an existing table, or user databases won't upgrade.
    Base.metadata.create_all(engine)
