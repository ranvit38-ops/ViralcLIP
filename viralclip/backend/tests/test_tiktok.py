import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import config, tiktok, worker
from app.db import SessionLocal
from app.models import Job, TikTokAccount, TikTokPost, now
from tests.conftest import new_client
from tests.test_youtube import future_local, make_clip


class FakeTikTok:
    """Just enough of TikTok's OAuth + Content Posting API to exercise our code paths."""

    def __init__(self):
        self.refreshes = 0
        self.refresh_fails = False
        self.revoked = []
        self.inits = []  # (path, body)
        self.uploads = {}  # upload_url -> {"size", "data", "chunks"}
        self.statuses = ["PROCESSING_DOWNLOAD", "PUBLISH_COMPLETE"]  # served in order, last repeats
        self.init_error = None  # (http status, code)
        self.privacy_options = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == tiktok.TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            assert form["client_key"] == "ck" and form["client_secret"] == "cs"
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "act1", "refresh_token": "rft1", "expires_in": 86400,
                                                 "open_id": "oid-1", "scope": tiktok.SCOPES, "token_type": "Bearer"})
            self.refreshes += 1
            if self.refresh_fails:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token is invalid"})
            return httpx.Response(200, json={"access_token": f"act-r{self.refreshes}", "refresh_token": f"rft-r{self.refreshes}",
                                             "expires_in": 86400, "open_id": "oid-1"})
        if url == tiktok.REVOKE_URL:
            self.revoked.append(req.content.decode())
            return httpx.Response(200, json={})
        ok = {"code": "ok", "message": "", "log_id": "x"}
        if url.startswith(f"{tiktok.API}/user/info/"):
            return httpx.Response(200, json={"data": {"user": {"open_id": "oid-1", "display_name": "Streamer", "avatar_url": "https://a/x.jpg"}}, "error": ok})
        if url.startswith("https://upload.tiktok.example/"):
            u = self.uploads[url]
            m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", req.headers["content-range"])
            assert req.headers["content-type"] == "video/mp4" and int(m[3]) == u["size"]
            assert int(m[1]) == len(u["data"]), "chunk sent at the wrong offset"
            u["data"] += req.content
            u["chunks"] += 1
            return httpx.Response(201 if len(u["data"]) == u["size"] else 206)
        assert req.headers["authorization"].startswith("Bearer act")
        if url == f"{tiktok.API}/post/publish/creator_info/query/":
            return httpx.Response(200, json={"data": {"creator_nickname": "Streamer", "privacy_level_options": self.privacy_options,
                                                      "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False,
                                                      "max_video_post_duration_sec": 600}, "error": ok})
        if url.endswith("/video/init/"):
            if self.init_error:
                st, code = self.init_error
                return httpx.Response(st, json={"data": {}, "error": {"code": code, "message": f"{code} message", "log_id": "x"}})
            body = json.loads(req.content)
            self.inits.append((urlparse(url).path, body))
            n = len(self.inits)
            up = f"https://upload.tiktok.example/{n}"
            self.uploads[up] = {"size": body["source_info"]["video_size"], "data": b"", "chunks": 0}
            return httpx.Response(200, json={"data": {"publish_id": f"v_pub_{n}", "upload_url": up}, "error": ok})
        if url == f"{tiktok.API}/post/publish/status/fetch/":
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"data": {"status": st, "fail_reason": "video_too_short" if st == "FAILED" else ""}, "error": ok})
        raise AssertionError(f"unexpected request {req.method} {url}")


@pytest.fixture
def tt(monkeypatch):
    fake = FakeTikTok()
    monkeypatch.setattr(config, "TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setattr(config, "TIKTOK_CLIENT_SECRET", "cs")
    monkeypatch.setattr(config, "TIKTOK_REDIRECT_URI", "http://localhost:3000/api/tiktok/callback")
    monkeypatch.setattr(config, "TIKTOK_AUDITED", True)
    monkeypatch.setattr(tiktok, "http", httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def connect(c) -> int:
    r = c.get("/api/tiktok/connect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(tiktok.AUTH_URL)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_key"] == ["ck"] and q["scope"] == [tiktok.SCOPES] and q["response_type"] == ["code"]
    r = c.get(f"/api/tiktok/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert r.headers["location"] == "/tiktok?connected=1", r.headers["location"]
    return c.get("/api/tiktok/accounts").json()[-1]["id"]


def run_post(post_id: int, max_runs=10):
    """Run the post's job until it stops requeueing itself (polling), ignoring run_after."""
    for _ in range(max_runs):
        with SessionLocal() as db:
            p = db.get(TikTokPost, post_id)
            j = db.get(Job, p.job_id)
            if j.status != "queued":
                break
            j.status = "running"
            db.commit()
            jid = j.id
        worker.run_job(jid, tiktok.JOB_HANDLERS)
        with SessionLocal() as db:
            if db.get(TikTokPost, post_id).status not in ("processing",):
                break
    with SessionLocal() as db:
        p = db.get(TikTokPost, post_id)
        return p, db.get(Job, p.job_id)


DIRECT = {"mode": "direct", "privacy_level": "PUBLIC_TO_EVERYONE"}


# ---------- OAuth ----------

def test_oauth_state_validation(tt):
    c, other = new_client(), new_client()
    assert "error=" in c.get("/api/tiktok/callback?code=x", follow_redirects=False).headers["location"]
    assert "error=" in c.get("/api/tiktok/callback?code=x&state=forged", follow_redirects=False).headers["location"]
    state = parse_qs(urlparse(c.get("/api/tiktok/connect", follow_redirects=False).headers["location"]).query)["state"][0]
    assert "error=" in other.get(f"/api/tiktok/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    assert "error=" in c.get(f"/api/tiktok/callback?code=x&state={state}", follow_redirects=False).headers["location"]  # burned
    # a YouTube state can't be replayed here (shared table, prefixed hash)
    yt = c.get("/api/youtube/connect", follow_redirects=False).headers["location"]
    if "state=" in yt:
        ys = parse_qs(urlparse(yt).query)["state"][0]
        assert "error=" in c.get(f"/api/tiktok/callback?code=x&state={ys}", follow_redirects=False).headers["location"]
    assert c.get("/api/tiktok/accounts").json() == []
    connect(c)
    a = c.get("/api/tiktok/accounts").json()[0]
    assert (a["display_name"], a["open_id"], a["status"], a["posts_today"], a["daily_limit"], a["rule"]) == \
        ("Streamer", "oid-1", "ok", 0, 15, None)


def test_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TIKTOK_CLIENT_KEY", "")
    c = new_client()
    cfg = c.get("/api/tiktok/config").json()
    assert cfg["configured"] is False and "TIKTOK_CLIENT_KEY" in cfg["missing"] and "audited" in cfg
    assert c.get("/api/tiktok/connect", follow_redirects=False).headers["location"].startswith("/tiktok?error=")


def test_tokens_encrypted_refresh_and_revoke(tt):
    c = new_client()
    aid = connect(c)
    body = c.get("/api/tiktok/accounts").text
    assert "act1" not in body and "rft1" not in body
    with SessionLocal() as db:
        a = db.get(TikTokAccount, aid)
        assert "rft1" not in a.encrypted_tokens
        assert tiktok.decrypt_tokens(a.encrypted_tokens) == {"access_token": "act1", "refresh_token": "rft1"}
        a.token_expiry = now() - timedelta(minutes=1)
        db.commit()
        assert tiktok.access_token(db, a) == "act-r1"
        assert tiktok.decrypt_tokens(a.encrypted_tokens)["refresh_token"] == "rft-r1"  # rotated token kept
        a.token_expiry = now() - timedelta(minutes=1)
        db.commit()
        tt.refresh_fails = True
        with pytest.raises(tiktok.AuthRequired):
            tiktok.access_token(db, a)
    assert c.get("/api/tiktok/accounts").json()[0]["status"] == "reauth_required"
    tt.refresh_fails = False
    connect(c)  # reconnecting fixes it
    assert c.get("/api/tiktok/accounts").json()[0]["status"] == "ok"
    assert c.delete(f"/api/tiktok/accounts/{aid}").status_code == 204
    assert tt.revoked and "client_key=ck" in tt.revoked[0] and "token=act1" in tt.revoked[0]
    assert c.get("/api/tiktok/accounts").json() == []


# ---------- posting ----------

def test_direct_post_polls_to_published(tt, monkeypatch):
    monkeypatch.setattr(tiktok, "MAX_SINGLE_CHUNK", 200_000)
    monkeypatch.setattr(tiktok, "CHUNK", 200_000)
    c = new_client()
    aid = connect(c)
    cid = make_clip(c, size=500_000)  # 2 chunks: 200k + 300k (last chunk absorbs the remainder)
    r = c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT, "disable_comment": True})
    assert r.status_code == 201, r.text
    out = r.json()[0]
    assert (out["status"], out["mode"], out["privacy_level"], out["account_name"]) == ("queued", "direct", "PUBLIC_TO_EVERYONE", "Streamer")
    p, job = run_post(out["id"])
    assert (p.status, p.publish_id, job.status) == ("published", "v_pub_1", "done") and p.posted_at
    path, body = tt.inits[0]
    assert path == "/v2/post/publish/video/init/"
    assert body["post_info"] == {"title": "A useful clip #gta6 #gaming", "privacy_level": "PUBLIC_TO_EVERYONE",
                                 "disable_comment": True, "disable_duet": True,  # duet disabled by the creator's settings
                                 "disable_stitch": False, "brand_content_toggle": False, "brand_organic_toggle": False}
    assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 500_000, "chunk_size": 200_000, "total_chunk_count": 2}
    up = tt.uploads["https://upload.tiktok.example/1"]
    assert len(up["data"]) == 500_000 and up["chunks"] == 2
    assert c.get("/api/tiktok/stats").json() == {"scheduled": 0, "posting": 0, "published": 1, "drafts_sent": 0, "failed": 0}
    assert c.get("/api/tiktok/accounts").json()[0]["posts_today"] == 1
    assert c.post(f"/api/tiktok/posts/{p.id}/cancel").status_code == 409  # already on TikTok


def test_draft_mode_sends_to_inbox(tt):
    tt.statuses = ["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"]
    c = new_client()
    aid = connect(c)
    r = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, "mode": "draft"})
    assert r.status_code == 201 and r.json()[0]["privacy_level"] is None
    p, _ = run_post(r.json()[0]["id"])
    assert p.status == "draft_sent"
    path, body = tt.inits[0]
    assert path == "/v2/post/publish/inbox/video/init/" and set(body) == {"source_info"}
    assert c.get("/api/tiktok/stats").json()["drafts_sent"] == 1


def test_failed_status_reports_tiktoks_reason(tt):
    tt.statuses = ["FAILED"]
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).json()[0]["id"]
    p, job = run_post(pid)
    assert p.status == "failed" and "video_too_short" in p.error and job.status == "failed"


def test_unaudited_only_self_only_and_draft_default(tt, monkeypatch):
    monkeypatch.setattr(config, "TIKTOK_AUDITED", False)
    c = new_client()
    aid = connect(c)
    info = c.get(f"/api/tiktok/accounts/{aid}/creator_info").json()
    assert info["privacy_level_options"] == ["SELF_ONLY"] and info["max_video_post_duration_sec"] == 600
    cid = make_clip(c)
    r = c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT})
    assert r.status_code == 422 and "SELF_ONLY" in r.json()["detail"]
    assert c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, "mode": "direct"}).status_code == 422  # no privacy
    assert c.put(f"/api/tiktok/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "18:00", "timezone": "UTC",
                                                           "clips_per_day": 1, **DIRECT}).status_code == 422
    r = c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid})
    assert r.status_code == 201 and r.json()[0]["mode"] == "draft"  # default when unaudited
    r = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, "mode": "direct", "privacy_level": "SELF_ONLY"})
    assert r.status_code == 201
    # brand content can't be private
    assert c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, "mode": "direct",
                                             "privacy_level": "SELF_ONLY", "brand_content_toggle": True}).status_code == 422


def test_validation_and_duplicates(tt):
    c = new_client()
    aid = connect(c)
    for kw in ({"approved": False}, {"rendered": False}):
        assert c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c, **kw)], "account_id": aid, **DIRECT}).status_code == 409
    assert c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c, title="")], "account_id": aid, **DIRECT}).status_code == 422
    assert c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT,
                                             "publish_at": "2020-01-01T10:00", "timezone": "UTC"}).status_code == 422
    # duration limit from creator_info, once known
    c.get(f"/api/tiktok/accounts/{aid}/creator_info")
    with SessionLocal() as db:
        a = db.get(TikTokAccount, aid)
        a.creator_info = {**a.creator_info, "max_video_post_duration_sec": 5}
        db.commit()
    assert c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).status_code == 422
    with SessionLocal() as db:
        a = db.get(TikTokAccount, aid)
        a.creator_info = {**a.creator_info, "max_video_post_duration_sec": 600}
        db.commit()
    cid = make_clip(c)
    assert c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT}).status_code == 201
    r = c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT})
    assert r.status_code == 409 and r.json()["detail"]["duplicate_clip_id"] == cid
    assert c.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT, "force": True}).status_code == 201


def test_scheduled_post_waits_then_posts(tt):
    c = new_client()
    aid = connect(c)
    when = future_local(hours=3)
    r = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT,
                                          "publish_at": when, "timezone": "UTC"})
    assert r.status_code == 201 and r.json()[0]["status"] == "scheduled"
    pid = r.json()[0]["id"]
    with SessionLocal() as db:
        p = db.get(TikTokPost, pid)
        job = db.get(Job, p.job_id)
        assert tiktok._aware(job.run_after) == tiktok._aware(p.publish_at) > now() + timedelta(hours=2)
        assert db.scalar(select(Job.id).where(Job.id == job.id, Job.status == "queued", Job.run_after <= now())) is None  # claim() skips it
    # picked up early (should never happen, but must not post): it re-waits for the slot
    p, job = run_post(pid, max_runs=1)
    assert p.status == "scheduled" and job.status == "queued" and not tt.inits
    # the slot arrives
    with SessionLocal() as db:
        db.get(TikTokPost, pid).publish_at = now() - timedelta(seconds=5)
        db.commit()
    p, job = run_post(pid)
    assert p.status == "published" and len(tt.inits) == 1
    # preview + confirm use the shared slot logic
    cids = [make_clip(c) for _ in range(2)]
    body = {"clip_ids": cids, "account_id": aid, "start": "2027-03-13T10:00", "timezone": "America/New_York",
            "interval": "daily", "mode": "draft"}
    slots = c.post("/api/tiktok/schedule/preview", json=body).json()["slots"]
    assert [s["clip_id"] for s in slots] == cids and all("10:00" in s["local"] for s in slots) and slots[0]["clip_title"]
    r = c.post("/api/tiktok/schedule/confirm", json=body)
    assert r.status_code == 201 and [p["status"] for p in r.json()] == ["scheduled", "scheduled"]
    assert c.post("/api/tiktok/schedule/confirm", json=body).status_code == 409
    assert len(c.get("/api/tiktok/posts?status=scheduled").json()) == 2


def test_rate_limit_requeues(tt):
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).json()[0]["id"]
    tt.init_error = (429, "rate_limit_exceeded")
    p, job = run_post(pid, max_runs=1)
    assert p.status == "queued" and job.status == "queued" and p.publish_id is None and "rate limit" in p.error
    assert tiktok._aware(job.run_after) > now() + timedelta(minutes=4)
    tt.init_error = (403, "spam_risk_too_many_posts")
    with SessionLocal() as db:
        db.get(Job, job.id).run_after = None
        db.commit()
    p, job = run_post(pid, max_runs=1)
    assert p.status == "queued" and tiktok._aware(job.run_after) > now() + timedelta(minutes=50)
    tt.init_error = (400, "invalid_params")
    p, job = run_post(pid, max_runs=1)
    assert p.status == "failed" and "invalid_params message" in p.error and job.status == "failed"
    assert c.post(f"/api/tiktok/posts/{pid}/retry").json()["status"] == "queued"
    tt.init_error = None
    assert run_post(pid)[0].status == "published"


def test_daily_limit_requeues_without_calling_tiktok(tt, monkeypatch):
    monkeypatch.setattr(config, "TIKTOK_DAILY_POST_LIMIT", 0)
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).json()[0]["id"]
    p, job = run_post(pid, max_runs=1)
    assert p.status == "queued" and job.status == "queued" and not tt.inits and "Daily limit" in p.error


def test_auth_failure_marks_auth_required(tt):
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).json()[0]["id"]
    with SessionLocal() as db:
        db.get(TikTokAccount, aid).token_expiry = now() - timedelta(minutes=1)
        db.commit()
    tt.refresh_fails = True
    p, job = run_post(pid, max_runs=1)
    assert p.status == "auth_required" and job.status == "failed" and "Reconnect" in job.error
    assert c.get("/api/tiktok/accounts").json()[0]["status"] == "reauth_required"
    assert c.get("/api/tiktok/stats").json()["failed"] == 1
    assert c.post(f"/api/tiktok/posts/{pid}/retry").status_code == 409  # reconnect first


def test_crash_after_upload_does_not_post_twice(tt):
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT}).json()[0]["id"]
    run_post(pid)
    with SessionLocal() as db:  # simulate: the worker died after sending every byte, before recording it
        p = db.get(TikTokPost, pid)
        p.status = "uploading"
        db.get(Job, p.job_id).status = "queued"
        db.commit()
    tt.statuses = ["PROCESSING_DOWNLOAD", "PUBLISH_COMPLETE"]
    p, _ = run_post(pid)
    assert p.status == "published" and len(tt.inits) == 1


def test_cancel_retry_delete(tt):
    c = new_client()
    aid = connect(c)
    pid = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, "mode": "draft",
                                            "publish_at": future_local(), "timezone": "UTC"}).json()[0]["id"]
    assert c.post(f"/api/tiktok/posts/{pid}/cancel").json()["status"] == "cancelled"
    assert c.post(f"/api/tiktok/posts/{pid}/retry").json()["status"] == "scheduled"
    with SessionLocal() as db:
        db.get(TikTokPost, pid).status = "uploading"
        db.commit()
    assert c.delete(f"/api/tiktok/posts/{pid}").status_code == 409
    with SessionLocal() as db:
        db.get(TikTokPost, pid).status = "scheduled"
        db.commit()
    assert c.delete(f"/api/tiktok/posts/{pid}").status_code == 204
    assert c.get("/api/tiktok/posts").json() == []


# ---------- auto-publish rule ----------

def test_rule_posts_only_approved_rendered_once(tt):
    c = new_client()
    aid = connect(c)
    good = make_clip(c)
    make_clip(c, approved=False)
    make_clip(c, rendered=False)
    already = make_clip(c)
    c.post("/api/tiktok/posts", json={"clip_ids": [already], "account_id": aid, "mode": "draft"})
    r = c.put(f"/api/tiktok/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "18:00", "timezone": "Europe/London",
                                                        "clips_per_day": 2, **DIRECT})
    assert r.status_code == 200 and r.json() == {"enabled": True, "time_of_day": "18:00", "timezone": "Europe/London",
                                                 "clips_per_day": 2, "mode": "direct", "privacy_level": "PUBLIC_TO_EVERYONE"}
    assert c.get("/api/tiktok/accounts").json()[0]["rule"]["clips_per_day"] == 2
    with SessionLocal() as db:
        assert tiktok.autopublish_tick(db) == 1
        assert tiktok.autopublish_tick(db) == 0  # never twice
        posts = list(db.scalars(select(TikTokPost).where(TikTokPost.account_id == aid, TikTokPost.clip_id != already)))
    assert [p.clip_id for p in posts] == [good]
    assert posts[0].status == "scheduled" and posts[0].mode == "direct" and posts[0].publish_at is not None
    auto = c.get("/api/automation").json()
    assert auto["tiktok_accounts"] == [{"id": aid, "display_name": "Streamer", "status": "ok", "autopublish_enabled": True}]


def test_rule_caps_per_local_day(tt):
    c = new_client()
    aid = connect(c)
    for _ in range(3):
        make_clip(c)
    c.put(f"/api/tiktok/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "09:00", "timezone": "UTC",
                                                    "clips_per_day": 1, "mode": "draft"})
    with SessionLocal() as db:
        tiktok.autopublish_tick(db)
        days = [tiktok._aware(p.publish_at).date() for p in db.scalars(select(TikTokPost).where(TikTokPost.account_id == aid))]
    assert len(days) == 3 and len(set(days)) == 3  # one per day


# ---------- isolation ----------

def test_isolation_between_users(tt):
    a, b = new_client(), new_client()
    aid = connect(a)
    cid = make_clip(a)
    pid = a.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": aid, **DIRECT}).json()[0]["id"]
    b_aid = connect(b)
    assert b.get("/api/tiktok/posts").json() == [] and len(b.get("/api/tiktok/accounts").json()) == 1
    assert b.post("/api/tiktok/posts", json={"clip_ids": [cid], "account_id": b_aid, **DIRECT}).status_code == 404
    assert b.post("/api/tiktok/posts", json={"clip_ids": [make_clip(b)], "account_id": aid, **DIRECT}).status_code == 404
    for path in (f"/api/tiktok/posts/{pid}/cancel", f"/api/tiktok/posts/{pid}/retry"):
        assert b.post(path).status_code == 404
    assert b.delete(f"/api/tiktok/posts/{pid}").status_code == 404
    assert b.delete(f"/api/tiktok/accounts/{aid}").status_code == 404
    assert b.get(f"/api/tiktok/accounts/{aid}/creator_info").status_code == 404
    assert b.put(f"/api/tiktok/accounts/{aid}/rule", json={"enabled": True, "time_of_day": "10:00", "timezone": "UTC",
                                                           "clips_per_day": 1, "mode": "draft"}).status_code == 404
    assert b.post("/api/tiktok/schedule/preview", json={"clip_ids": [cid], "account_id": b_aid, "start": "2027-01-01T10:00",
                                                        "timezone": "UTC"}).status_code == 404
    # disconnecting keeps A's history with account_id cleared
    assert a.delete(f"/api/tiktok/accounts/{aid}").status_code == 204
    p = a.get("/api/tiktok/posts").json()[0]
    assert p["account_id"] is None and p["status"] == "cancelled" and p["account_name"] == "(disconnected)"
