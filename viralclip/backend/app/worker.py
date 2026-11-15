"""Background worker: `python -m app.worker`. Run several processes for parallelism.

Jobs live in the `jobs` table. A job is claimed with a conditional UPDATE, so two workers never
run the same job. Handlers report real progress via ctx.progress(), which also heartbeats and
raises Cancelled when the user cancelled. A crashed worker's job is requeued once its heartbeat
goes stale; after MAX_ATTEMPTS it is marked failed instead of looping forever.
"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import or_, select, update

from . import config
from .db import SessionLocal, init_db
from .models import Job, now

log = logging.getLogger("viralclip.worker")
STALE_AFTER = timedelta(minutes=3)  # run_job heartbeats every BEAT_EVERY s, so only a dead worker goes stale
BEAT_EVERY = 30
MAX_ATTEMPTS = 3
TICK_LOCK_STALE = 600  # seconds; a lock older than this was left by a killed process


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
    from . import twitch
    h.update(twitch.JOB_HANDLERS)
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
    stop = threading.Event()
    threading.Thread(target=_beat, args=(jid, stop), daemon=True).start()
    try:
        table[job_type](jid, Ctx(jid))
    except Cancelled:
        status = "cancelled"
    except UserError as e:
        status, error = "failed", str(e)
    except Exception:
        log.exception("Job %s (%s) crashed", jid, job_type)
        status, error = "failed", f"Unexpected error during {job_type}. See worker logs."
    finally:
        stop.set()
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


def _beat(jid: int, stop: threading.Event):
    """Keep a running job's heartbeat fresh even through long silent steps (model download, audio decode,
    LLM calls), so requeue_stale only ever picks up jobs whose worker is really gone."""
    while not stop.wait(BEAT_EVERY):
        try:
            with SessionLocal() as db:
                db.execute(update(Job).where(Job.id == jid, Job.status.in_(("running", "cancel_requested")))
                           .values(heartbeat_at=now()))
                db.commit()
        except Exception:
            log.warning("heartbeat for job %s failed", jid, exc_info=True)


@contextmanager
def tick_lock(wait: float = 0):
    """Only one process at a time runs the periodic tick (every worker calls it, and so does the API's
    "check now"). Two concurrent ticks would both see "no job yet" and enqueue duplicates. A lockfile in
    DATA_DIR works for SQLite and Postgres alike (every process shares DATA_DIR) and on Windows."""
    f = config.DATA_DIR / "automation.lock"
    deadline = time.time() + wait
    while True:
        try:
            os.close(os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - f.stat().st_mtime > TICK_LOCK_STALE:
                    f.unlink(missing_ok=True)  # left behind by a killed process
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                yield False
                return
            time.sleep(0.5)
    try:
        yield True
    finally:
        f.unlink(missing_ok=True)


def periodic():
    """Once a minute: automation (import → analyze → approve → render), YouTube auto-publish rules and
    scheduled-video status sync. Skipped when another process is already running it."""
    with tick_lock() as mine:
        if not mine:
            return
        try:
            from . import youtube
            n = youtube.tick()
            if n:
                log.info("auto-publish: scheduled %s upload(s)", n)
        except Exception:
            log.exception("periodic task failed")
        try:
            from . import automation
            n = automation.tick()  # youtube.tick() then publishes what this rendered, next minute
            if n:
                log.info("automation: %s action(s), see the activity log", n)
        except Exception:
            log.exception("automation tick failed")


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
            periodic()
            last_sweep = time.time()
        if not run_once():
            time.sleep(1.0)


if __name__ == "__main__":
    main()
