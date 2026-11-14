from alembic import context

from app.db import Base, engine
from app import models  # noqa: F401  (register tables)

with engine.connect() as conn:
    # render_as_batch: SQLite can't ALTER most things; batch mode recreates tables safely.
    context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
