import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import config, projects, worker, youtube
from app.db import SessionLocal
from app.models import AutoPublishRule, Clip, Job, Project, YouTubeAccount, YouTubeUpload, now
from tests.conftest import make_video, new_client


class FakeGoogle:
    """Just enough of Google's OAuth + YouTube Data API to exercise our code paths."""

    def __init__(self):
        self.channels = [{"id": "UC_mine", "snippet": {"title": "My Channel", "thumbnails": {"default": {"url": "https://i.example/a.jpg"}}}}]
        self.videos = {}  # video id -> snippet
        self.sessions = {}
        self.created = 0
        self.refresh_fails = False
        self.quota = False
        self.network_fail_on_chunk = None  # raise ConnectError on the Nth data chunk
        self.chunks = 0
        self.revoked = []
        self.refreshes = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith(youtube.TOKEN_URL):
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600, "scope": youtube.SCOPES})
            self.refreshes += 1
            if self.refresh_fails:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": f"at-r{self.refreshes}", "expires_in": 3600})
        if url.startswith(youtube.REVOKE_URL):
            self.revoked.append(req.content.decode())
            return httpx.Response(200)
        if url.startswith(f"{youtube.API}/channels"):
            return httpx.Response(200, json={"items": self.channels})
        if url.startswith(f"{youtube.API}/videos") and req.method == "GET":
            ids = parse_qs(urlparse(url).query)["id"][0].split(",")
            items = []
            for i in ids:
                if i in self.videos:
                    items.append({"id": i, "snippet": self.videos[i], "status": {"privacyStatus": "public"}})
            return httpx.Response(200, json={"items": items})
        if url.startswith("https://www.googleapis.com/upload/youtube/v3/videos") and req.method == "POST":
            if self.quota:
                return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "Quota exceeded"}})
            self.created += 1
            uri = f"https://upload.example/session/{self.created}"
            self.sessions[uri] = {"size": int(req.headers["x-upload-content-length"]), "data": b"", "meta": json.loads(req.content)}
            return httpx.Response(200, headers={"Location": uri})
        if url.startswith("https://upload.example/session/"):
            s = self.sessions[url]
            cr = req.headers["content-range"]
            if not cr.startswith("bytes */"):
                self.chunks += 1
                if self.network_fail_on_chunk == self.chunks:
                    raise httpx.ConnectError("connection reset")
                start = int(re.match(r"bytes (\d+)-", cr).group(1))
                assert start == len(s["data"]), "client sent a chunk at the wrong offset"
                s["data"] += req.content
            if len(s["data"]) == s["size"]:
                st = s["meta"]["status"]
                return httpx.Response(200, json={"id": "vid12345678", "status": {"privacyStatus": st["privacyStatus"], **({"publishAt": st["publishAt"]} if "publishAt" in st else {})}})
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(s['data']) - 1}"} if s["data"] else {})
        raise AssertionError(f"unexpected request {req.method} {url}")


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "GOOGLE_REDIRECT_URI", "http://localhost:3000/api/youtube/callback")
    monkeypatch.setattr(youtube, "http", httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def connect(c) -> int:
    r = c.get("/api/youtube/connect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(youtube.AUTH_URL)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["access_type"] == ["offline"] and "youtube.upload" in q["scope"][0]
    r = c.get(f"/api/youtube/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert r.headers["location"] == "/youtube?connected=1", r.headers["location"]
    return c.get("/api/youtube/accounts").json()[-1]["id"]


def make_clip(c, approved=True, rendered=True, size=600_000, title="A useful clip") -> int:
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        p = Project(user_id=uid, name="p", status="ready")
        db.add(p)
        db.flush()
        clip = Clip(project_id=p.id, start=0, end=10, title=title, description="desc", hashtags=["gta6", "#gaming"],
                    review_status="approved" if approved else "suggested", render_status="done" if rendered else "none")
        db.add(clip)
        db.flush()
        if rendered:
            clip.render_path = f"clips/{clip.id}.mp4"
            f = projects.project_dir(p.id) / clip.render_path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"\x01" * size)
        db.commit()
        return clip.id


def run_upload(upload_id: int):
    with SessionLocal() as db:
        u = db.get(YouTubeUpload, upload_id)
        j = db.get(Job, u.job_id)
        j.status = "running"
        db.commit()
        jid = j.id
    worker.run_job(jid, youtube.JOB_HANDLERS)
    with SessionLocal() as db:
        return db.get(YouTubeUpload, upload_id), db.get(Job, jid)


def future_local(hours=48):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


# ---------- OAuth ----------

def test_oauth_state_validation(google):
    c, other = new_client(), new_client()
    assert "error=" in c.get("/api/youtube/callback?code=x", follow_redirects=False).headers["location"]
    assert "error=" in c.get("/api/youtube/callback?code=x&state=forged", follow_redirects=False).headers["location"]
    r = c.get("/api/youtube/connect", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    # another user can't use my state
    assert "error=" in other.get(f"/api/youtube/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    # and it's burned now (single use), even for me
    assert "error=" in c.get(f"/api/youtube/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    assert c.get("/api/youtube/accounts").json() == []
    connect(c)
    assert c.get("/api/youtube/accounts").json()[0]["channel_title"] == "My Channel"


def test_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    c = new_client()
    assert c.get("/api/youtube/config").json()["configured"] is False
    assert "error=" in c.get("/api/youtube/connect", follow_redirects=False).headers["location"]


def test_tokens_encrypted_refresh_and_revoked(google):
    c = new_client()
    aid = connect(c)
    body = c.get("/api/youtube/accounts").text
    assert "rt1" not in body and "at1" not in body and "token" not in body.lower().replace("uploads_today", "")
    with SessionLocal() as db:
        a = db.get(YouTubeAccount, aid)
        assert "rt1" not in a.encrypted_tokens
        assert youtube.decrypt_tokens(a.encrypted_tokens) == {"access_token": "at1", "refresh_token": "rt1"}
        a.token_expiry = now() - timedelta(minutes=1)
        db.commit()
        assert youtube.access_token(db, a) == "at-r1"  # expired -> refreshed
        assert youtube.decrypt_tokens(a.encrypted_tokens)["refresh_token"] == "rt1"  # kept
        a.token_expiry = now() - timedelta(minutes=1)
        db.commit()
        google.refresh_fails = True
        with pytest.raises(youtube.AuthRequired):
            youtube.access_token(db, a)
        assert a.status == "reauth_required"
    assert c.get("/api/youtube/accounts").json()[0]["status"] == "reauth_required"
    # reconnecting fixes it
    connect(c)
    assert c.get("/api/youtube/accounts").json()[0]["status"] == "ok"


def test_disconnect_revokes(google):
    c = new_client()
    aid = connect(c)
    assert c.delete(f"/api/youtube/accounts/{aid}").status_code == 204
    assert google.revoked and "rt1" in google.revoked[0]
    assert c.get("/api/youtube/accounts").json() == []


# ---------- uploads ----------

def test_upload_success(google):
    c = new_client()
    aid = connect(c)
    cid = make_clip(c)
    r = c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid, "privacy": "public"})
    assert r.status_code == 201, r.text
    u, job = run_upload(r.json()[0]["id"])
    assert (u.upload_status, u.youtube_video_id, u.schedule_status, job.status) == ("uploaded", "vid12345678", "published", "done")
    meta = next(iter(google.sessions.values()))["meta"]
    assert meta["snippet"]["title"] == "A useful clip"
    assert meta["snippet"]["tags"] == ["gta6", "gaming"] and meta["snippet"]["description"].endswith("#gta6 #gaming")
    out = c.get("/api/youtube/uploads").json()[0]
    assert out["youtube_url"] == "https://www.youtube.com/watch?v=vid12345678"
    assert c.get("/api/youtube/stats").json()["published"] == 1


def test_refuses_unapproved_unrendered_and_duplicates(google):
    c = new_client()
    aid = connect(c)
    for kw in ({"approved": False}, {"rendered": False}):
        cid = make_clip(c, **kw)
        assert c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).status_code == 409
    cid = make_clip(c, title="")
    assert c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).status_code == 422  # no title
    cid = make_clip(c)
    assert c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).status_code == 201
    r = c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid})
    assert r.status_code == 409 and r.json()["detail"]["duplicate_clip_id"] == cid
    assert c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid, "force": True}).status_code == 201


def test_schedule_validation_and_confirmation(google):
    c = new_client()
    aid = connect(c)
    cid = make_clip(c)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    base = {"clip_ids": [cid], "account_id": aid, "timezone": "UTC"}
    assert c.post("/api/youtube/uploads", json={**base, "publish_at": past}).status_code == 422
    assert c.post("/api/youtube/uploads", json={**base, "publish_at": future_local(), "timezone": "Mars/Base"}).status_code == 422
    assert c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid, "publish_at": future_local()}).status_code == 422
    r = c.post("/api/youtube/uploads", json={**base, "publish_at": future_local(), "privacy": "public"})
    assert r.status_code == 201 and r.json()[0]["privacy"] == "private"  # scheduling requires private + publishAt
    u, _ = run_upload(r.json()[0]["id"])
    assert (u.upload_status, u.schedule_status) == ("uploaded", "scheduled")
    st = next(iter(google.sessions.values()))["meta"]["status"]
    assert st["privacyStatus"] == "private" and st["publishAt"].endswith("Z")


def test_resume_reuses_session_no_duplicate(google, monkeypatch):
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    c = new_client()
    aid = connect(c)
    cid = make_clip(c, size=700_000)  # 3 chunks
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).json()[0]["id"]
    google.network_fail_on_chunk = 2
    u, job = run_upload(uid)
    assert job.status == "queued" and job.run_after is not None  # backoff, not failure
    assert u.session_uri and u.bytes_sent == 256 * 1024
    google.network_fail_on_chunk = None
    u, job = run_upload(uid)
    assert u.upload_status == "uploaded" and google.created == 1
    assert len(next(iter(google.sessions.values()))["data"]) == 700_000


def test_finished_session_is_recorded_not_reuploaded(google):
    c = new_client()
    aid = connect(c)
    cid = make_clip(c)
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).json()[0]["id"]
    run_upload(uid)
    # Simulate: the upload completed at Google but we crashed before saving the result.
    with SessionLocal() as db:
        u = db.get(YouTubeUpload, uid)
        u.upload_status, u.youtube_video_id, u.session_uri = "queued", None, "https://upload.example/session/1"
        db.add(Job(project_id=db.get(Clip, cid).project_id, clip_id=cid, job_type="youtube_upload", params={"upload_id": uid}))
        db.flush()
        u.job_id = db.scalars(select(Job.id).order_by(Job.id.desc())).first()
        db.commit()
    u, job = run_upload(uid)
    assert u.upload_status == "uploaded" and u.youtube_video_id == "vid12345678" and google.created == 1 and job.status == "done"


def test_quota_exceeded_requeues(google, monkeypatch):
    c = new_client()
    aid = connect(c)
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [make_clip(c)], "account_id": aid}).json()[0]["id"]
    google.quota = True
    u, job = run_upload(uid)
    assert u.upload_status == "quota_exceeded" and job.status == "queued"
    assert youtube._aware(job.run_after) > now() + timedelta(minutes=1)
    # our own daily cap: no request to Google at all
    google.quota = False
    monkeypatch.setattr(config, "YOUTUBE_DAILY_UPLOAD_LIMIT", 0)
    uid2 = c.post("/api/youtube/uploads", json={"clip_ids": [make_clip(c)], "account_id": aid}).json()[0]["id"]
    u2, job2 = run_upload(uid2)
    assert u2.upload_status == "quota_exceeded" and job2.status == "queued" and google.created == 0


def test_auth_revoked_during_upload(google):
    c = new_client()
    aid = connect(c)
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [make_clip(c)], "account_id": aid}).json()[0]["id"]
    with SessionLocal() as db:
        db.get(YouTubeAccount, aid).token_expiry = now() - timedelta(minutes=1)
        db.commit()
    google.refresh_fails = True
    u, job = run_upload(uid)
    assert u.upload_status == "auth_required" and job.status == "failed" and "Reconnect" in job.error


def test_pause_cancel_retry(google):
    c = new_client()
    aid = connect(c)
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [make_clip(c)], "account_id": aid}).json()[0]["id"]
    assert c.post(f"/api/youtube/uploads/{uid}/pause").json()["upload_status"] == "paused"
    assert c.post(f"/api/youtube/uploads/{uid}/retry").json()["upload_status"] == "queued"  # resume
    assert c.post(f"/api/youtube/uploads/{uid}/cancel").json()["upload_status"] == "cancelled"
    assert c.post(f"/api/youtube/uploads/{uid}/retry").status_code == 200
    u, _ = run_upload(uid)
    assert u.upload_status == "uploaded"
    assert c.post(f"/api/youtube/uploads/{uid}/cancel").status_code == 409  # already on YouTube


# ---------- scheduling ----------

def test_bulk_preview_is_side_effect_free_and_dst_aware(google):
    c = new_client()
    aid = connect(c)
    cids = [make_clip(c) for _ in range(3)]
    body = {"clip_ids": cids, "account_id": aid, "start": "2027-03-13T10:00", "timezone": "America/New_York", "interval": "daily"}
    r = c.post("/api/youtube/schedule/preview", json=body)
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert all("10:00" in s["publish_at_local"] for s in slots)  # wall clock stays 10:00 across the DST switch
    assert [s["publish_at_utc"][11:13] for s in slots] == ["15", "14", "14"]
    assert c.get("/api/youtube/uploads").json() == []
    r = c.post("/api/youtube/schedule/confirm", json=body)
    assert r.status_code == 201 and len(r.json()) == 3
    assert c.post("/api/youtube/schedule/confirm", json=body).status_code == 409  # double-confirm
    weekly = c.post("/api/youtube/schedule/preview", json={**body, "interval": "weekly"}).json()["slots"]
    assert weekly[1]["publish_at_utc"][:10] == "2027-03-20"


def test_autopublish_only_approved_and_no_double_booking(google):
    c = new_client()
    aid = connect(c)
    approved = make_clip(c)
    make_clip(c, approved=False)
    r = c.put(f"/api/youtube/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "18:00", "timezone": "Europe/London", "clips_per_day": 1})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        youtube.autopublish_tick(db)
        youtube.autopublish_tick(db)  # second tick must not schedule the same clip again
        ups = list(db.scalars(select(YouTubeUpload).where(YouTubeUpload.account_id == aid)))
    assert [u.clip_id for u in ups] == [approved]
    assert ups[0].publish_at is not None and ups[0].privacy == "private"


# ---------- isolation ----------

def test_isolation_between_users(google):
    a, b = new_client(), new_client()
    aid = connect(a)
    cid = make_clip(a)
    uid = a.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid}).json()[0]["id"]
    b_aid = connect(b)
    assert b.get("/api/youtube/uploads").json() == []
    assert b.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": b_aid}).status_code == 404
    assert b.post("/api/youtube/uploads", json={"clip_ids": [make_clip(b)], "account_id": aid}).status_code == 404
    for path in (f"/api/youtube/uploads/{uid}/pause", f"/api/youtube/uploads/{uid}/cancel", f"/api/youtube/uploads/{uid}/retry"):
        assert b.post(path).status_code == 404
    assert b.delete(f"/api/youtube/accounts/{aid}").status_code == 404
    assert b.put(f"/api/youtube/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "10:00", "timezone": "UTC", "clips_per_day": 1}).status_code == 404


# ---------- link import ----------

def test_parse_video_id():
    for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "https://youtu.be/dQw4w9WgXcQ", "youtube.com/shorts/dQw4w9WgXcQ",
                "https://m.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"):
        assert youtube.parse_video_id(url) == "dQw4w9WgXcQ", url
    for url in ("https://vimeo.com/123", "https://evil.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/watch?v=short"):
        assert youtube.parse_video_id(url) is None, url


def test_import_requires_own_channel(google):
    c = new_client()
    assert c.post("/api/youtube/import", json={"url": "https://youtu.be/AAAAAAAAAAA"}).status_code == 409  # nothing connected
    connect(c)
    google.videos["BBBBBBBBBBB"] = {"channelId": "UC_someone_else", "channelTitle": "Rockstar Games", "title": "Trailer"}
    r = c.post("/api/youtube/import", json={"url": "https://youtu.be/BBBBBBBBBBB"})
    assert r.status_code == 403 and "Rockstar Games" in r.json()["detail"]
    assert c.post("/api/youtube/import", json={"url": "https://youtu.be/CCCCCCCCCCC"}).status_code == 404
    assert c.get("/api/projects").json() == []


def test_import_own_video_downloads_and_probes(google, monkeypatch):
    c = new_client()
    connect(c)
    google.videos["DDDDDDDDDDD"] = {"channelId": "UC_mine", "channelTitle": "My Channel", "title": "My stream"}
    r = c.post("/api/youtube/import", json={"url": "https://www.youtube.com/watch?v=DDDDDDDDDDD"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "importing" and r.json()["name"] == "My stream"
    src = make_video("import.mp4")

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            assert urls == ["https://www.youtube.com/watch?v=DDDDDDDDDDD"]
            out = self.opts["outtmpl"].replace("%(ext)s", "mp4")
            size = src.stat().st_size
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": size // 2, "total_bytes": size, "info_dict": {"vcodec": "avc1"}})
            shutil.copy(src, out)

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with SessionLocal() as db:
        jid = db.scalar(select(Job.id).where(Job.project_id == pid))
        db.get(Job, jid).status = "running"
        db.commit()
    worker.run_job(jid, youtube.JOB_HANDLERS)
    p = c.get(f"/api/projects/{pid}").json()
    assert p["status"] == "uploaded" and abs(p["duration"] - 3) < 0.2 and p["has_audio"]
    assert c.get(f"/api/projects/{pid}/source").status_code == 200
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "done"


def test_missed_slot_is_rescheduled_not_dropped(google):
    """If a quota wait or outage outlasts the slot, publish an hour after upload instead of staying private forever."""
    c = new_client()
    aid = connect(c)
    cid = make_clip(c)
    uid = c.post("/api/youtube/uploads", json={"clip_ids": [cid], "account_id": aid, "timezone": "UTC",
                                               "publish_at": future_local()}).json()[0]["id"]
    with SessionLocal() as db:  # simulate the slot passing while the upload waited
        db.get(YouTubeUpload, uid).publish_at = now() - timedelta(hours=3)
        db.commit()
    u, _ = run_upload(uid)
    assert (u.upload_status, u.schedule_status) == ("uploaded", "scheduled")
    publish_at = next(iter(google.sessions.values()))["meta"]["status"]["publishAt"]
    assert datetime.fromisoformat(publish_at.replace("Z", "+00:00")) > datetime.now(timezone.utc)
