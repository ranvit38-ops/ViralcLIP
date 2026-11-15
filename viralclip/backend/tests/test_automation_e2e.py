"""Hands-off automation end to end: a new Twitch VOD becomes scheduled YouTube uploads with no user action.

Fakes: Twitch + Google at the HTTP layer, yt-dlp at the library layer, and fast stand-ins for the analyze
and render handlers (the real ones are covered by test_pipeline). Everything else is the real worker:
worker.periodic() (tick lock, youtube + automation ticks) and worker.run_job() on claimed jobs.
"""
import os
import threading
import time
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app import automation, projects, twitch, worker
from app.db import SessionLocal
from app.models import AutomationEvent, Clip, Job, Project, TwitchAccount, YouTubeUpload, now
from tests.conftest import make_video, new_client
from tests.test_twitch import FakeYDL, connect as tw_connect, enable, tw  # noqa: F401  (pytest fixture)
from tests.test_youtube import connect as yt_connect, google  # noqa: F401  (pytest fixture)


def fake_analyze(job_id, ctx):
    with SessionLocal() as db:
        p = db.get(Project, db.get(Job, job_id).project_id)
        for i, s in enumerate([90, 70, 50, 30]):
            db.add(Clip(project_id=p.id, start=i * 40, end=i * 40 + 30, score=s, title=f"{p.name} clip {i}",
                        transcript_excerpt="x", hashtags=["a"]))
        p.status = "ready"
        db.commit()


def fake_render(job_id, ctx):
    with SessionLocal() as db:
        c = db.get(Clip, db.get(Job, job_id).clip_id)
        f = projects.project_dir(c.project_id) / "clips" / f"{c.id}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x01" * 1000)
        c.render_status, c.render_path = "done", f"clips/{c.id}.mp4"
        db.commit()


@pytest.fixture
def stack(tw, google, monkeypatch):  # noqa: F811
    import yt_dlp
    FakeYDL.src, FakeYDL.urls = make_video("vod.mp4"), []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    table = {**worker.handlers(), "analyze": fake_analyze, "render": fake_render}
    monkeypatch.setattr(worker, "handlers", lambda: table)
    clock = [time.time()]  # automation's poll interval runs on this simulated clock
    monkeypatch.setattr(automation, "time", SimpleNamespace(time=lambda: clock[0]))
    c = new_client()
    c.uid = c.get("/api/auth/me").json()["id"]
    c.taid, c.yaid = tw_connect(c), yt_connect(c)
    return SimpleNamespace(c=c, tw=tw, google=google, clock=clock)


def minute(s):
    """One simulated minute of a worker: the periodic tick, then every job this user can run now."""
    worker.periodic()
    while True:
        with SessionLocal() as db:
            jid = db.scalar(select(Job.id).join(Project).where(
                Project.user_id == s.c.uid, Job.status == "queued",
                (Job.run_after.is_(None)) | (Job.run_after <= now())).order_by(Job.id).limit(1))
            if jid is None:
                break
            db.get(Job, jid).status = "running"
            db.commit()
        worker.run_job(jid)
    s.clock[0] += 60


def rows(model, uid, *where):
    with SessionLocal() as db:
        q = select(model)
        if model is Job:
            q = q.join(Project).where(Project.user_id == uid)
        elif hasattr(model, "user_id"):
            q = q.where(model.user_id == uid)
        return list(db.scalars(q.where(*where)))


def no_duplicate_jobs(uid):
    js = rows(Job, uid)
    dup = {k: n for k, n in Counter((j.job_type, j.project_id, j.clip_id) for j in js).items() if n > 1}
    assert not dup, dup
    assert Counter(u.clip_id for u in rows(YouTubeUpload, uid)).most_common(1)[0][1] == 1


def test_two_vods_go_to_scheduled_uploads_hands_off(stack):
    s, c = stack, stack.c
    assert c.put(f"/api/youtube/accounts/{c.yaid}/rule", json={
        "enabled": True, "time_of_day": "18:00", "timezone": "UTC", "clips_per_day": 2}).status_code == 200
    enable(c, twitch=[c.taid], auto_approve={"enabled": True, "top_n": 3, "min_score": 0})
    s.tw.vod("91000001", title="First")
    s.tw.vod("91000002", title="Second")
    s.tw.vod("91000003", title="Live now", stream_id="live-1")
    s.tw.live = [{"id": "live-1"}]

    # From here on the user does nothing.
    for n in range(1, 30):
        minute(s)
        if len(rows(YouTubeUpload, c.uid, YouTubeUpload.upload_status == "uploaded")) == 6:
            break
    # The second VOD follows as soon as the first download ends, not 30 minutes later.
    assert n <= 10, f"took {n} simulated minutes"
    ups = sorted(rows(YouTubeUpload, c.uid), key=lambda u: u.publish_at)
    assert len(ups) == 6 and all(u.upload_status == "uploaded" and u.schedule_status == "scheduled" for u in ups)
    times = [u.publish_at for u in ups]
    assert all(t.hour in (6, 18) and t.minute == 0 for t in times)
    assert all((b - a).total_seconds() == 12 * 3600 for a, b in zip(times, times[1:]))  # 2 a day, no gaps
    assert {p.name for p in rows(Project, c.uid)} == {"First", "Second"}  # the live one waits
    assert s.google.created == 6

    # 35 more minutes of ticking: the stream ends meanwhile, its VOD is picked up once; nothing else repeats.
    for n in range(35):
        if n == 5:
            s.tw.live = []
        minute(s)
    assert sorted(p.name for p in rows(Project, c.uid)) == ["First", "Live now", "Second"]
    assert sorted(FakeYDL.urls) == [f"https://www.twitch.tv/videos/9100000{i}" for i in (1, 2, 3)]
    no_duplicate_jobs(c.uid)
    assert len(rows(YouTubeUpload, c.uid)) == 9
    assert s.tw.validated <= 6  # polled on a schedule, not every minute


def test_approving_through_the_api_alone_flows_to_render_and_publish(stack):
    s, c = stack, stack.c
    c.put(f"/api/youtube/accounts/{c.yaid}/rule", json={"enabled": True, "time_of_day": "09:00",
                                                        "timezone": "Europe/Berlin", "clips_per_day": 1})
    enable(c, twitch=[c.taid], auto_approve={"enabled": False})
    s.tw.vod("92000001", title="Review me")
    for _ in range(4):
        minute(s)
    clips = c.get("/api/clips").json()
    assert len(clips) == 4 and all(x["review_status"] == "suggested" for x in clips)
    assert not rows(YouTubeUpload, c.uid) and not rows(Job, c.uid, Job.job_type == "render")
    assert any(e["kind"] == "review" for e in c.get("/api/automation").json()["events"])

    best = max(clips, key=lambda x: x["score"])
    assert c.patch(f"/api/clips/{best['id']}", json={"review_status": "approved"}).status_code == 200
    for _ in range(3):
        minute(s)
    (u,) = rows(YouTubeUpload, c.uid)
    assert u.clip_id == best["id"] and u.upload_status == "uploaded" and u.schedule_status == "scheduled"
    no_duplicate_jobs(c.uid)


def test_concurrent_ticks_never_double_act(stack):
    s, c = stack, stack.c
    c.put(f"/api/youtube/accounts/{c.yaid}/rule", json={"enabled": True, "time_of_day": "18:00",
                                                        "timezone": "UTC", "clips_per_day": 3})
    enable(c, twitch=[c.taid], auto_approve={"enabled": True, "top_n": 3, "min_score": 0})
    with SessionLocal() as db:
        p = Project(user_id=c.uid, name="race", status="uploaded", has_audio=True, settings={"automation": {"auto": True}})
        db.add(p)
        db.commit()
        pid = p.id

    def burst():  # several workers (and the API's "check now") ticking at the same instant
        go = threading.Barrier(4)
        ts = [threading.Thread(target=lambda: (go.wait(), worker.periodic())) for _ in range(3)]
        ts.append(threading.Thread(target=lambda: (go.wait(), c.post("/api/automation/run"))))
        [t.start() for t in ts]
        [t.join() for t in ts]

    for step in range(4):  # uploaded -> analyze; ready -> approve + render; rendered -> uploads
        burst()
        for j in rows(Job, c.uid, Job.status == "queued", Job.job_type != "youtube_upload"):
            with SessionLocal() as db:
                db.get(Job, j.id).status = "running"
                db.commit()
            worker.run_job(j.id)
    no_duplicate_jobs(c.uid)
    assert len(rows(Job, c.uid, Job.job_type == "analyze")) == 1
    assert len(rows(Job, c.uid, Job.job_type == "render")) == 3
    assert len(rows(YouTubeUpload, c.uid)) == 3
    assert len([e for e in rows(AutomationEvent, c.uid) if e.kind == "approve"]) == 1
    assert not (worker.config.DATA_DIR / "automation.lock").exists()


def test_tick_lock_skips_while_held_and_recovers_a_stale_one():
    f = worker.config.DATA_DIR / "automation.lock"
    with worker.tick_lock() as a:
        assert a
        with worker.tick_lock() as b:
            assert not b
    assert not f.exists()
    f.touch()
    old = time.time() - worker.TICK_LOCK_STALE - 5
    os.utime(f, (old, old))  # left by a killed worker
    with worker.tick_lock() as a:
        assert a
    assert not f.exists()


def test_twitch_token_expired_is_refreshed_and_revoked_is_logged_once(stack, monkeypatch):
    s, c = stack, stack.c
    enable(c, twitch=[c.taid])
    with SessionLocal() as db:
        db.get(TwitchAccount, c.taid).token_expiry = now()
        db.commit()
    s.tw.vod("93000001")
    minute(s)
    assert s.tw.refreshes == 1 and FakeYDL.urls == ["https://www.twitch.tv/videos/93000001"]

    def revoked(req):
        if str(req.url).startswith(twitch.TOKEN_URL):
            return httpx.Response(400, json={"status": 400, "message": "Invalid refresh token"})
        return httpx.Response(401, json={"error": "Unauthorized"}) if "helix" in str(req.url) else s.tw(req)
    monkeypatch.setattr(twitch, "http", httpx.Client(transport=httpx.MockTransport(revoked)))
    s.tw.vod("93000002")
    s.clock[0] += 3600
    for _ in range(40):
        minute(s)
    errors = [e for e in rows(AutomationEvent, c.uid) if e.kind == "error"]
    assert len(errors) == 1 and "reconnect" in errors[0].message
    with SessionLocal() as db:
        assert db.get(TwitchAccount, c.taid).status == "reauth_required"
    assert len(rows(Project, c.uid)) == 1


def test_failed_import_is_reported_once_and_never_retried(stack, monkeypatch):
    s, c = stack, stack.c
    enable(c, twitch=[c.taid])

    class Broken(FakeYDL):
        def download(self, urls):
            FakeYDL.urls += urls
            raise RuntimeError("ERROR: [twitch] 403 Forbidden")
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Broken)
    s.tw.vod("94000001", title="Doomed")
    for _ in range(35):
        minute(s)
    (p,) = rows(Project, c.uid)
    assert p.status == "failed"
    errors = [e for e in rows(AutomationEvent, c.uid) if e.kind == "error"]
    assert len(errors) == 1 and "Doomed" in errors[0].message and "download" in errors[0].message.lower()
    assert FakeYDL.urls == ["https://www.twitch.tv/videos/94000001"]


def test_heartbeat_keeps_long_silent_jobs_alive_and_dead_ones_recover(stack, monkeypatch):
    s, c = stack, stack.c
    monkeypatch.setattr(worker, "BEAT_EVERY", 0.05)
    seen = {}

    def silent(job_id, ctx):  # e.g. a model download: no ctx.progress() for a long time
        with SessionLocal() as db:
            db.get(Job, job_id).heartbeat_at = now() - worker.STALE_AFTER * 2
            db.commit()
        time.sleep(0.4)
        worker.requeue_stale()
        with SessionLocal() as db:
            seen["status"] = db.get(Job, job_id).status

    monkeypatch.setitem(worker.handlers(), "analyze", silent)
    with SessionLocal() as db:
        p = Project(user_id=c.uid, name="slow", status="uploaded", has_audio=True)
        db.add(p)
        db.flush()
        j = Job(project_id=p.id, job_type="analyze", status="running", heartbeat_at=now())
        db.add(j)
        db.commit()
        jid = j.id
    worker.run_job(jid)
    assert seen["status"] == "running"  # never handed to a second worker while still alive

    # A worker killed mid-job: its heartbeat stops, the job is requeued once and finishes exactly once.
    monkeypatch.setitem(worker.handlers(), "analyze", fake_analyze)
    with SessionLocal() as db:
        j = db.get(Job, jid)
        j.status, j.attempts, j.heartbeat_at = "running", 1, now() - worker.STALE_AFTER * 2
        db.commit()
    worker.requeue_stale()
    assert rows(Job, c.uid, Job.id == jid)[0].status == "queued"
    minute(s)
    assert rows(Job, c.uid, Job.id == jid)[0].status == "done"
    assert len(rows(Job, c.uid, Job.job_type == "analyze")) == 1
    with SessionLocal() as db:
        assert len(db.get(Project, rows(Job, c.uid)[0].project_id).clips) == 4
