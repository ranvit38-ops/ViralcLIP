"""Twitch connect/import + the automation pipeline. Twitch, Google and yt-dlp are faked at the HTTP/library layer."""
import shutil
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import automation, config, projects, twitch, worker, youtube
from app.db import SessionLocal
from app.models import AutomationEvent, Clip, Job, Project, YouTubeUpload, now
from tests.conftest import make_video, new_client
from tests.test_youtube import connect as yt_connect, google  # noqa: F401  (pytest fixture)

ME, OTHER = "111", "999"


class FakeTwitch:
    def __init__(self):
        self.vods = {}  # id -> helix video dict
        self.live = []
        self.revoked = []
        self.refreshes = 0
        self.validated = 0

    def vod(self, vid, user_id=ME, hours=1.0, created=None, stream_id=None, title=None):
        self.vods[vid] = {"id": vid, "user_id": user_id, "user_name": "me" if user_id == ME else "someone",
                          "title": title or f"Stream {vid}", "type": "archive", "stream_id": stream_id,
                          "created_at": (created or now()).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "duration": f"{int(hours)}h{int(hours * 60 % 60)}m0s", "url": f"https://www.twitch.tv/videos/{vid}",
                          "thumbnail_url": "https://t.example/%{width}x%{height}.jpg"}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if url.startswith(twitch.TOKEN_URL):
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "tat1", "refresh_token": "trt1", "expires_in": 14000})
            self.refreshes += 1
            return httpx.Response(200, json={"access_token": f"tat-r{self.refreshes}", "refresh_token": "trt2", "expires_in": 14000})
        if url.startswith(twitch.REVOKE_URL):
            self.revoked.append(req.content.decode())
            return httpx.Response(200)
        if url.startswith(twitch.VALIDATE_URL):
            self.validated += 1
            return httpx.Response(200, json={"user_id": ME})
        assert req.headers.get("client-id") == "tcid"
        if url.startswith(f"{twitch.API}/users"):
            return httpx.Response(200, json={"data": [{"id": ME, "login": "me", "display_name": "Me", "profile_image_url": None}]})
        if url.startswith(f"{twitch.API}/streams"):
            return httpx.Response(200, json={"data": self.live})
        if url.startswith(f"{twitch.API}/videos"):
            if "id" in q:
                return httpx.Response(200, json={"data": [self.vods[q["id"]]] if q["id"] in self.vods else []})
            return httpx.Response(200, json={"data": [v for v in self.vods.values() if v["user_id"] == q["user_id"]]})
        raise AssertionError(f"unexpected request {req.method} {url}")


@pytest.fixture
def tw(monkeypatch):
    fake = FakeTwitch()
    monkeypatch.setattr(config, "TWITCH_CLIENT_ID", "tcid")
    monkeypatch.setattr(config, "TWITCH_CLIENT_SECRET", "tsecret")
    monkeypatch.setattr(twitch, "http", httpx.Client(transport=httpx.MockTransport(fake)))
    automation._last_poll.clear()
    return fake


def connect(c) -> int:
    r = c.get("/api/twitch/connect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(twitch.AUTH_URL)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = c.get(f"/api/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/automation?twitch_connected=1", r.headers["location"]
    return c.get("/api/twitch/accounts").json()[-1]["id"]


class FakeYDL:
    src = None
    urls = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        FakeYDL.urls += urls
        size = self.src.stat().st_size
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": size, "total_bytes": size, "info_dict": {"vcodec": "avc1"}})
        shutil.copy(self.src, self.opts["outtmpl"].replace("%(ext)s", "mp4"))


def run_job(jid):
    with SessionLocal() as db:
        db.get(Job, jid).status = "running"
        db.commit()
    worker.run_job(jid, worker.handlers())


def jobs(pid, job_type=None):
    with SessionLocal() as db:
        q = select(Job).where(Job.project_id == pid)
        if job_type:
            q = q.where(Job.job_type == job_type)
        return list(db.scalars(q))


def enable(c, **kw):
    body = {"enabled": True, "twitch_account_ids": kw.pop("twitch", []), "youtube_account_ids": kw.pop("youtube", []),
            "auto_approve": kw.pop("auto_approve", {"enabled": False}), **kw}
    r = c.put("/api/automation", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tick(c):
    r = c.post("/api/automation/run")
    assert r.status_code == 200, r.text
    return r.json()


# ---------- OAuth / import ----------

def test_oauth_state_validation(tw):
    c = new_client()
    assert "error" in c.get("/api/twitch/callback?code=x", follow_redirects=False).headers["location"]
    assert "error" in c.get("/api/twitch/callback?code=x&state=forged", follow_redirects=False).headers["location"]
    # a YouTube-style state (same table, different prefix) is not accepted here
    r = c.get("/api/twitch/connect", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    other = new_client()
    assert "error" in other.get(f"/api/twitch/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    # ...and the state was burned by that attempt
    assert "error" in c.get(f"/api/twitch/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    aid = connect(c)
    body = c.get("/api/twitch/accounts").text
    assert "tat1" not in body and "trt1" not in body
    assert c.get("/api/twitch/accounts").json()[0]["display_name"] == "Me"
    assert c.delete(f"/api/twitch/accounts/{aid}").status_code == 204 and tw.revoked


def test_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TWITCH_CLIENT_ID", "")
    c = new_client()
    assert "isn't configured" in c.get("/api/twitch/connect", follow_redirects=False).headers["location"].replace("%27", "'").replace("+", " ")


def test_parse_vod_id():
    for u in ["https://www.twitch.tv/videos/2283745012", "twitch.tv/videos/2283745012", "2283745012",
              "https://m.twitch.tv/somechannel/video/2283745012?t=1h"]:
        assert twitch.parse_vod_id(u) == "2283745012", u
    for u in ["https://twitch.tv/somechannel", "https://evil.example/videos/2283745012", "https://youtube.com/watch?v=abc"]:
        assert twitch.parse_vod_id(u) is None, u
    assert twitch.parse_duration("3h2m10s") == 10930 and twitch.parse_duration("45m") == 2700


def test_import_own_vod_only_once(tw, monkeypatch):
    c = new_client()
    assert c.post("/api/twitch/import", json={"url": "https://twitch.tv/videos/10000001"}).status_code == 409  # not connected
    connect(c)
    tw.vod("10000001")
    tw.vod("20000002", user_id=OTHER)
    r = c.post("/api/twitch/import", json={"url": "https://twitch.tv/videos/20000002"})
    assert r.status_code == 403 and "someone" in r.json()["detail"]
    assert c.post("/api/twitch/import", json={"url": "https://twitch.tv/videos/30000003"}).status_code == 404
    r = c.post("/api/twitch/import", json={"url": "https://www.twitch.tv/videos/10000001"})
    assert r.status_code == 201 and r.json()["status"] == "importing"
    pid = r.json()["id"]
    assert c.post("/api/twitch/import", json={"url": "10000001"}).status_code == 409  # duplicate

    FakeYDL.src, FakeYDL.urls = make_video("vod.mp4"), []
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    run_job(jobs(pid, "twitch_import")[0].id)
    assert FakeYDL.urls == ["https://www.twitch.tv/videos/10000001"]
    p = c.get(f"/api/projects/{pid}").json()
    assert p["status"] == "uploaded" and p["has_audio"]
    vods = c.get(f"/api/twitch/accounts/{c.get('/api/twitch/accounts').json()[0]['id']}/videos").json()
    assert vods[0]["imported"] and vods[0]["project_id"] == pid

    # deleting the project allows a deliberate manual re-import
    c.delete(f"/api/projects/{pid}")
    assert c.post("/api/twitch/import", json={"url": "10000001"}).status_code == 201


# ---------- automation ----------

def _fake_analyze(pid, scores):
    with SessionLocal() as db:
        for j in db.scalars(select(Job).where(Job.project_id == pid, Job.job_type == "analyze")):
            j.status = "done"
        db.get(Project, pid).status = "ready"
        for i, s in enumerate(scores):
            db.add(Clip(project_id=pid, start=i * 40, end=i * 40 + 30, score=s, title=f"Clip {i}",
                        transcript_excerpt="x", hashtags=["a"]))
        db.commit()


def _fake_render_all(pid):
    with SessionLocal() as db:
        for j in db.scalars(select(Job).where(Job.project_id == pid, Job.job_type == "render")):
            j.status = "done"
            c = db.get(Clip, j.clip_id)
            c.render_status, c.render_path = "done", f"clips/{c.id}.mp4"
            f = projects.project_dir(pid) / c.render_path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"\x01" * 1000)
        db.commit()


def test_full_chain_import_analyze_approve_render_publish(tw, google, monkeypatch):  # noqa: F811
    c = new_client()
    taid = connect(c)
    yaid = yt_connect(c)
    assert c.put(f"/api/youtube/accounts/{yaid}/rule", json={"enabled": True, "time_of_day": "18:00", "timezone": "UTC",
                                                              "clips_per_day": 1}).status_code == 200
    enable(c, twitch=[taid], auto_approve={"enabled": True, "top_n": 3, "min_score": 50},
           analyze={"min_len": 15, "max_len": 30, "count": 4, "model": "tiny"})
    tw.vod("40000004", created=now() - timedelta(days=3))  # before automation was enabled: never imported
    tw.vod("50000005", hours=2, stream_id="s-live")  # still streaming: wait
    tw.live = [{"id": "s-live"}]
    tw.vod("60000006", hours=0.1)  # 6 minutes: shorter than min_source_minutes (20)
    tw.vod("70000007", hours=3, title="Big stream")

    from collections import namedtuple
    du = namedtuple("du", "total used free")
    monkeypatch.setattr(automation.shutil, "disk_usage", lambda p: du(10 * 1024**3, 0, 5 * 1024**3))
    state = tick(c)  # 3 h VOD ≈ 12 GB: refused, logged, retried later
    assert any("Not enough free disk" in e["message"] for e in state["events"])
    assert c.get("/api/projects").json() == []
    monkeypatch.setattr(automation.shutil, "disk_usage", lambda p: du(10**13, 0, 10**13))
    automation._last_poll.clear()
    state = tick(c)
    imported = [e for e in state["events"] if e["kind"] == "import"]
    assert len(imported) == 1 and "Big stream" in imported[0]["message"], state["events"]
    assert tw.validated >= 1
    pid = imported[0]["project_id"]
    tick(c)  # idempotent: nothing new while the import runs, and never the same VOD twice
    assert len([e for e in c.get("/api/automation").json()["events"] if e["kind"] == "import"]) == 1

    FakeYDL.src = make_video("vod.mp4")
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    run_job(jobs(pid, "twitch_import")[0].id)
    assert c.get(f"/api/projects/{pid}").json()["status"] == "uploaded"

    tick(c)
    tick(c)
    an = jobs(pid, "analyze")
    assert len(an) == 1 and an[0].params["count"] == 4 and an[0].params["model"] == "tiny"

    _fake_analyze(pid, [90, 70, 40, 30])
    tick(c)
    tick(c)
    clips = {x["score"]: x for x in c.get(f"/api/clips?project_id={pid}").json()}
    assert clips[90]["review_status"] == "approved" and clips[70]["review_status"] == "approved"
    assert clips[40]["review_status"] == "suggested"  # in the top 3 but under min_score
    renders = jobs(pid, "render")
    assert sorted(j.clip_id for j in renders) == sorted([clips[90]["id"], clips[70]["id"]])  # no duplicates

    _fake_render_all(pid)
    with SessionLocal() as db:
        assert youtube.autopublish_tick(db) == 2
        ups = list(db.scalars(select(YouTubeUpload).where(YouTubeUpload.clip_id.in_([clips[90]["id"], clips[70]["id"]]))))
        assert len(ups) == 2 and all(u.publish_at for u in ups)
        assert youtube.autopublish_tick(db) == 0

    # the live stream ends -> its VOD is picked up on a later tick
    tw.live = []
    automation._last_poll.clear()
    tick(c)
    assert any("Stream 50000005" in e["message"] for e in c.get("/api/automation").json()["events"])


def test_auto_approve_off_waits_for_review_then_renders_what_you_approve(tw):
    c = new_client()
    taid = connect(c)
    enable(c, twitch=[taid], auto_approve={"enabled": False})
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        p = Project(user_id=uid, name="auto", status="uploaded", has_audio=True, settings={"automation": {"auto": True}})
        db.add(p)
        db.commit()
        pid = p.id
    tick(c)
    _fake_analyze(pid, [95, 80])
    tick(c)
    clips = c.get(f"/api/clips?project_id={pid}").json()
    assert all(x["review_status"] == "suggested" for x in clips)
    assert not jobs(pid, "render")
    assert any(e["kind"] == "review" for e in c.get("/api/automation").json()["events"])
    assert c.get("/api/automation").json()["waiting_review"] == 2
    c.patch(f"/api/clips/{clips[0]['id']}", json={"review_status": "approved"})
    tick(c)
    tick(c)
    assert [j.clip_id for j in jobs(pid, "render")] == [clips[0]["id"]]


def test_disabled_does_nothing_and_manual_projects_untouched(tw):
    c = new_client()
    taid = connect(c)
    tw.vod("80000008", hours=2)
    c.put("/api/automation", json={"enabled": False, "twitch_account_ids": [taid]})
    with SessionLocal() as db:
        automation.run_for_user(db, db.get(automation.User, c.get("/api/auth/me").json()["id"]), force_poll=True)
    assert c.post("/api/automation/run").status_code == 409
    assert c.get("/api/projects").json() == []
    enable(c, twitch=[taid])
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        p = Project(user_id=uid, name="manual", status="uploaded", has_audio=True)
        db.add(p)
        db.commit()
        pid = p.id
    tick(c)
    assert not jobs(pid)  # not an automated project: automation leaves it alone


def test_source_cleanup_keeps_renders(tw):
    c = new_client()
    taid = connect(c)
    enable(c, twitch=[taid], auto_approve={"enabled": True, "top_n": 1, "min_score": 0}, delete_source_after_days=1)
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        p = Project(user_id=uid, name="old", status="uploaded", has_audio=True, source_filename="v.mp4",
                    settings={"automation": {"auto": True}}, created_at=now() - timedelta(days=2))
        db.add(p)
        db.commit()
        pid = p.id
        src = projects.source_path(p)
    src.write_bytes(b"source")
    tick(c)
    _fake_analyze(pid, [70])
    tick(c)
    assert src.exists()  # approved clip not rendered yet: keep the source
    _fake_render_all(pid)
    tick(c)
    assert not src.exists()
    p = c.get(f"/api/projects/{pid}").json()
    assert "deleted by automation" in p["error"]
    clip = c.get(f"/api/clips?project_id={pid}").json()[0]
    assert c.get(f"/api/clips/{clip['id']}/video").status_code == 200


def test_isolation_and_validation(tw):
    a, b = new_client(), new_client()
    aid = connect(a)
    assert b.get(f"/api/twitch/accounts/{aid}/videos").status_code == 404
    assert b.delete(f"/api/twitch/accounts/{aid}").status_code == 404
    assert b.put("/api/automation", json={"enabled": True, "twitch_account_ids": [aid]}).status_code == 404
    assert a.put("/api/automation", json={"enabled": True, "min_source_minutes": 1}).status_code == 422
    assert a.put("/api/automation", json={"enabled": True, "analyze": {"min_len": 60, "max_len": 30}}).status_code == 422
    assert b.get("/api/automation").json()["twitch_accounts"] == []
    with SessionLocal() as db:
        assert db.scalar(select(AutomationEvent).where(AutomationEvent.user_id == b.get("/api/auth/me").json()["id"])) is None
