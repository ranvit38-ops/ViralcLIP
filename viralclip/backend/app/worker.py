"""Background worker: `python -m app.worker`. Run several processes for parallelism.

Jobs live in the `jobs` table. A job is claimed with a conditional UPDATE, so two workers never
run the same job. Handlers report real progress via ctx.progress(), which also heartbeats and
raises Cancelled when the user cancelled. A crashed worker's job is requeued once its heartbeat
goes stale; after MAX_ATTEMPTS it is marked failed instead of looping forever.
"""
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import or_, select, update

from .db import SessionLocal, init_db
from .models import Job, now

log = logging.getLogger("viralclip.worker")
STALE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 3


class Cancelled(Exception):
    pass


class UserError(Exception):
    """A failure whose message is safe and useful to show the user."""


class Ctx:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def progress(self, frac: float, message: str | None = None):
        with SessionLocal() as db:
            j = db.get(Job, self.job_id)
            if j is None or j.status == "cancel_requested":
                raise Cancelled()
            j.progress = max(0.0, min(1.0, frac))
            if message is not None:
                j.message = message[:300]
            j.heartbeat_at = now()
            db.commit()


def handlers():
    from . import pipeline
    h = {"analyze": pipeline.analyze, "render": pipeline.render, "metadata": pipeline.metadata}
    try:
        from . import youtube
        h.update(youtube.JOB_HANDLERS)
    except ImportError:
        pass
    return h


def requeue_stale():
    with SessionLocal() as db:
        for j in db.scalars(select(Job).where(Job.status.in_(("running", "cancel_requested")),
                                             Job.heartbeat_at < now() - STALE_AFTER)):
            if j.status == "cancel_requested":
                j.status, j.finished_at = "cancelled", now()
            elif j.attempts >= MAX_ATTEMPTS:
                j.status, j.error, j.finished_at = "failed", "Worker stopped repeatedly while running this job.", now()
            else:
                j.status, j.message = "queued", "Requeued after worker restart"
        db.commit()


def claim() -> int | None:
    with SessionLocal() as db:
        for jid in db.scalars(select(Job.id).where(
                Job.status == "queued", or_(Job.run_after.is_(None), Job.run_after <= now())).order_by(Job.id).limit(5)):
            r = db.execute(update(Job).where(Job.id == jid, Job.status == "queued").values(
                status="running", started_at=now(), heartbeat_at=now(), attempts=Job.attempts + 1, error=None))
            db.commit()
            if r.rowcount == 1:
                return jid
    return None


def run_job(jid: int, table=None):
    table = table or handlers()
    with SessionLocal() as db:
        job_type = db.get(Job, jid).job_type
    status, error = "done", None
    try:
        table[job_type](jid, Ctx(jid))
    except Cancelled:
        status = "cancelled"
    except UserError as e:
        status, error = "failed", str(e)
    except Exception:
        log.exception("Job %s (%s) crashed", jid, job_type)
        status, error = "failed", f"Unexpected error during {job_type}. See worker logs."
    with SessionLocal() as db:
        j = db.get(Job, jid)
        if j is None:  # project deleted mid-job
            return
        if j.status == "cancel_requested":
            status = "cancelled"
        if j.status == "queued":  # handler rescheduled itself (e.g. quota wait); leave it queued
            return
        j.status, j.error, j.finished_at = status, error, now()
        if status == "done":
            j.progress = 1.0
        db.commit()


def run_once() -> bool:
    jid = claim()
    if jid is None:
        return False
    run_job(jid)
    return True


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db()
    log.info("worker started (pid %s)", os.getpid())
    last_sweep = 0.0
    while True:
        if time.time() - last_sweep > 60:
            requeue_stale()
            last_sweep = time.time()
        if not run_once():
            time.sleep(1.0)


if __name__ == "__main__":
    main()
