"""Unified link import (POST /api/import) and learning from your own best clips (stats sync + trends.examples_for).
Google, Twitch, TikTok and yt-dlp are faked."""
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import automation, config, importer, llm, tiktok, trends, worker, youtube
from app.db import SessionLocal
from app.models import Clip, Job, TikTokAccount, TikTokPost, YouTubeAccount, YouTubeUpload, now
from tests.conftest import make_video, new_client
from tests.test_tiktok import FakeTikTok, connect as tt_connect
from tests.test_twitch import FakeYDL, OTHER, connect as tw_connect, run_job, tw  # noqa: F401  (fixture)
from tests.test_youtube import connect as yt_connect, google, make_clip  # noqa: F401  (fixture)

TT_MINE = "7300000000000000001"


class FakeTikTokList(FakeTikTok):
    """Adds video/list (paged), video/query and the vm.tiktok.com short-link redirect."""

    def __init__(self):
        super().__init__()
        self.own = [str(7200000000000000000 + i) for i in range(25)] + [TT_MINE]  # newest last: on page 2
        self.lists, self.queries, self.views = [], [], {}

    def __call__(self, req):
        url = str(req.url)
        if url.startswith("https://vm.tiktok.com/"):
            assert req.method == "GET"
            return httpx.Response(301, headers={"location": f"https://www.tiktok.com/@me.real/video/{TT_MINE}?_r=1"})
        ok = {"code": "ok", "message": "", "log_id": "x"}
        if url.startswith(f"{tiktok.API}/video/list/"):
            body = json.loads(req.content)
            self.lists.append(body)
            start = int(body.get("cursor") or 0)
            page = self.own[start:start + body["max_count"]]
            return httpx.Response(200, json={"data": {"videos": [{"id": int(i), "title": f"Post {i}"} for i in page],
                                                      "cursor": start + len(page), "has_more": start + len(page) < len(self.own)},
                                             "error": ok})
        if url.startswith(f"{tiktok.API}/video/query/"):
            ids = json.loads(req.content)["filters"]["video_ids"]
            self.queries.append(ids)
            return httpx.Response(200, json={"data": {"videos": [{"id": int(i), "view_count": self.views.get(i, 0), "like_count": 1}
                                                                 for i in ids]}, "error": ok})
        r = super().__call__(req)
        if url == f"{tiktok.API}/post/publish/status/fetch/" and r.json()["data"]["status"] == "PUBLISH_COMPLETE":
            d = r.json()
            d["data"]["publicaly_available_post_id"] = [int(TT_MINE)]
            return httpx.Response(200, json=d)
        return r


@pytest.fixture
def ttl(monkeypatch):
    fake = FakeTikTokList()
    monkeypatch.setattr(config, "TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setattr(config, "TIKTOK_CLIENT_SECRET", "cs")
    monkeypatch.setattr(config, "TIKTOK_REDIRECT_URI", "http://localhost:3000/api/tiktok/callback")
    monkeypatch.setattr(config, "TIKTOK_AUDITED", True)
    monkeypatch.setattr(tiktok, "http", httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def imp(c, url):
    return c.post("/api/import", json={"url": url})


# ---------- detection ----------

def test_detect_all_url_forms():
    cases = {
        "https://www.twitch.tv/videos/2283745012": ("twitch", "2283745012"),
        "twitch.tv/somechannel/video/2283745012?t=1h": ("twitch", "2283745012"),
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10": ("youtube", "dQw4w9WgXcQ"),
        "https://youtu.be/dQw4w9WgXcQ": ("youtube", "dQw4w9WgXcQ"),
        "youtube.com/shorts/dQw4w9WgXcQ": ("youtube", "dQw4w9WgXcQ"),
        "https://www.youtube.com/live/dQw4w9WgXcQ": ("youtube", "dQw4w9WgXcQ"),
        "https://www.tiktok.com/@me.real/video/7300000000000000001?is_from_webapp=1": ("tiktok", "7300000000000000001"),
        "m.tiktok.com/@me_2/video/7300000000000000001": ("tiktok", "7300000000000000001"),
        "https://vm.tiktok.com/ZMabc123/": ("tiktok", None),
        "https://www.tiktok.com/t/ZTabc123/": ("tiktok", None),
    }
    for url, want in cases.items():
        assert importer.detect(url)[:2] == want, url
    for bad in ["dQw4w9WgXcQ", "2283745012", "https://vimeo.com/123", "https://evil.com/@me/video/7300000000000000001",
                "https://www.tiktok.com/@me", "https://vm.tiktok.com/../etc", "https://twitch.tv/somechannel", ""]:
        assert importer.detect(bad) == (None, None, None), bad
    assert importer.detect("https://vm.tiktok.com/ZMabc123/")[2] == "https://vm.tiktok.com/ZMabc123/"


def test_detect_endpoint_reports_connection_without_network(tw, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("detect must not call out")
    monkeypatch.setattr(tiktok, "http", httpx.Client(transport=httpx.MockTransport(no_network)))
    c = new_client()
    assert c.get("/api/import/detect", params={"url": "https://twitch.tv/videos/10000001"}).json() == \
        {"platform": "twitch", "connected": False, "video_id": "10000001"}
    tw_connect(c)
    assert c.get("/api/import/detect", params={"url": "https://twitch.tv/videos/10000001"}).json()["connected"] is True
    assert c.get("/api/import/detect", params={"url": "https://vm.tiktok.com/ZMabc123/"}).json() == \
        {"platform": "tiktok", "connected": False, "video_id": None}
    assert c.get("/api/import/detect", params={"url": "nope"}).json() == {"platform": None, "connected": False, "video_id": None}
    r = imp(c, "https://vimeo.com/12345")
    assert r.status_code == 422 and r.json()["detail"] == importer.UNKNOWN


# ---------- ownership refusals ----------

def test_twitch_and_youtube_refuse_other_peoples_videos(tw, google):  # noqa: F811
    c = new_client()
    r = imp(c, "https://twitch.tv/videos/40000004")
    assert r.status_code == 409 and "Connect the Twitch account" in r.json()["detail"]
    tw_connect(c)
    tw.vod("40000004", user_id=OTHER)
    r = imp(c, "https://twitch.tv/videos/40000004")
    assert r.status_code == 403 and "someone" in r.json()["detail"]
    assert imp(c, "https://youtu.be/EEEEEEEEEEE").status_code == 409  # no channel connected
    yt_connect(c)
    google.videos["EEEEEEEEEEE"] = {"channelId": "UC_someone_else", "channelTitle": "Rockstar Games", "title": "Trailer"}
    r = imp(c, "https://www.youtube.com/shorts/EEEEEEEEEEE")
    assert r.status_code == 403 and "Rockstar Games" in r.json()["detail"]
    assert c.get("/api/projects").json() == []


def test_tiktok_refuses_videos_not_in_your_list(ttl):
    c = new_client()
    assert imp(c, f"https://www.tiktok.com/@x/video/{TT_MINE}").status_code == 409  # not connected
    tt_connect(c)
    r = imp(c, "https://www.tiktok.com/@someone/video/7399999999999999999")
    assert r.status_code == 403 and "your own videos" in r.json()["detail"]
    assert len(ttl.lists) == 2 and ttl.lists[1]["cursor"] == 20  # paged to the end
    assert c.get("/api/projects").json() == []


def test_tiktok_missing_video_list_scope_is_409(ttl):
    c = new_client()
    aid = tt_connect(c)
    with SessionLocal() as db:
        db.get(TikTokAccount, aid).scopes = None  # connected before video.list was requested
        db.commit()
    r = imp(c, f"https://www.tiktok.com/@me.real/video/{TT_MINE}")
    assert r.status_code == 409 and r.json()["detail"] == "Reconnect TikTok to allow importing your videos."
    assert not ttl.lists
    tt_connect(c)  # reconnecting grants it
    assert imp(c, f"https://www.tiktok.com/@me.real/video/{TT_MINE}").status_code == 201


def test_tiktok_short_link_import_download_and_duplicate(ttl, monkeypatch):
    assert "video.list" in tiktok.SCOPES.split(",")
    c = new_client()
    tt_connect(c)
    r = imp(c, "https://vm.tiktok.com/ZMabc123/")
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "importing" and r.json()["name"] == f"Post {TT_MINE}"
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.project_id == pid))
        assert job.job_type == "tiktok_import"
        assert job.params["url"] == f"https://www.tiktok.com/@me.real/video/{TT_MINE}"

    r = imp(c, f"https://www.tiktok.com/@me.real/video/{TT_MINE}")
    assert r.status_code == 409 and r.json()["project_id"] == pid and "Already imported" in r.json()["detail"]
    other = new_client()  # another user's import of the same id is not a duplicate of yours, and needs their account
    assert imp(other, f"https://www.tiktok.com/@me.real/video/{TT_MINE}").status_code == 409
    assert "project_id" not in imp(other, f"https://www.tiktok.com/@me.real/video/{TT_MINE}").json()

    FakeYDL.src, FakeYDL.urls = make_video("tiktok.mp4"), []
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    run_job(job.id)
    assert FakeYDL.urls == [f"https://www.tiktok.com/@me.real/video/{TT_MINE}"]
    assert c.get(f"/api/projects/{pid}").json()["status"] == "uploaded"


def test_duplicate_twitch_import_returns_project_and_automation_flag(tw):
    c = new_client()
    tw_connect(c)
    tw.vod("50000005")
    automation_on = c.put("/api/automation", json={"enabled": True, "twitch_account_ids": [], "youtube_account_ids": [],
                                                   "auto_approve": {"enabled": False}})
    assert automation_on.status_code == 200
    r = imp(c, "https://www.twitch.tv/videos/50000005")
    assert r.status_code == 201
    pid = r.json()["id"]
    with SessionLocal() as db:
        from app.models import Project
        assert db.get(Project, pid).settings["automation"]["auto"] is True  # automation is on: automated processing
    r = imp(c, "twitch.tv/videos/50000005")
    assert r.status_code == 409 and r.json()["project_id"] == pid


# ---------- stats sync ----------

def _uploads(c, aid, n, age_days=5, views=None):
    """n uploaded YouTube videos on channel aid; returns their video ids."""
    uid = c.get("/api/auth/me").json()["id"]
    ids = []
    with SessionLocal() as db:
        for i in range(n):
            cid = make_clip(c, title=f"Clip {i}")
            vid = f"v{aid:04d}{i:06d}"[:11].ljust(11, "x")
            db.add(YouTubeUpload(user_id=uid, clip_id=cid, account_id=aid, upload_status="uploaded", youtube_video_id=vid,
                                 uploaded_at=now() - timedelta(days=age_days), view_count=views[i] if views else None))
            ids.append(vid)
        db.commit()
    return ids


def _stats_transport(google, seen):  # noqa: F811
    def handler(req):
        q = parse_qs(urlparse(str(req.url)).query)
        if req.url.path.endswith("/videos") and q.get("part") == ["statistics"]:
            ids = q["id"][0].split(",")
            seen.append(ids)
            return httpx.Response(200, json={"items": [{"id": i, "statistics": {"viewCount": str(1000 + k), "likeCount": "7"}}
                                                       for k, i in enumerate(ids)]})
        return google(req)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_youtube_stats_sync_batches_and_throttles(google, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(youtube, "http", _stats_transport(google, seen))
    c = new_client()
    aid = yt_connect(c)
    ids = _uploads(c, aid, 120)
    with SessionLocal() as db:
        youtube.sync_stats(db)
    mine = [b for b in seen if set(b) <= set(ids)]
    assert [len(b) for b in mine] == [50, 50, 20] and sum(mine, []) == ids
    with SessionLocal() as db:
        u = db.scalar(select(YouTubeUpload).where(YouTubeUpload.youtube_video_id == ids[0]))
        assert (u.view_count, u.like_count) == (1000, 7) and u.stats_synced_at
    seen.clear()
    with SessionLocal() as db:
        youtube.sync_stats(db)
        assert not [b for b in seen if set(b) & set(ids)]  # within 24 h: not again
        db.get(YouTubeAccount, aid).stats_synced_at = now() - timedelta(hours=25)
        db.commit()
        youtube.sync_stats(db)
    assert len([b for b in seen if set(b) <= set(ids)]) == 3


def test_stats_sync_runs_only_under_the_tick_lock(google, ttl, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(youtube, "http", _stats_transport(google, seen))
    monkeypatch.setattr(automation, "tick", lambda: 0)
    c = new_client()
    ids = _uploads(c, yt_connect(c), 2)
    with worker.tick_lock() as got:
        assert got
        worker.periodic()  # another process holds the lock: skipped entirely
    assert not [b for b in seen if set(b) & set(ids)]
    worker.periodic()
    assert [b for b in seen if set(b) & set(ids)] == [ids]


def test_tiktok_stats_sync_needs_scope_and_stores_views(ttl):
    c = new_client()
    aid = tt_connect(c)
    cid = make_clip(c)
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        db.add(TikTokPost(user_id=uid, clip_id=cid, account_id=aid, status="published", tiktok_video_id=TT_MINE,
                          posted_at=now() - timedelta(days=4)))
        db.get(TikTokAccount, aid).scopes = "user.info.basic,video.publish,video.upload"
        db.commit()
    ttl.views[TT_MINE] = 4321
    with SessionLocal() as db:
        tiktok.sync_stats(db)
    assert not [q for q in ttl.queries if TT_MINE in q]  # no video.list scope: skipped
    with SessionLocal() as db:
        db.get(TikTokAccount, aid).scopes = tiktok.SCOPES
        db.commit()
        tiktok.sync_stats(db)
        assert [q for q in ttl.queries if TT_MINE in q] == [[TT_MINE]]
        p = db.scalar(select(TikTokPost).where(TikTokPost.account_id == aid))
        assert p.view_count == 4321
        tiktok.sync_stats(db)  # throttled for 24 h
    assert len([q for q in ttl.queries if TT_MINE in q]) == 1


def test_publish_complete_stores_the_public_post_id(ttl):
    from tests.test_tiktok import DIRECT, run_post
    ttl.statuses = ["PUBLISH_COMPLETE"]
    c = new_client()
    aid = tt_connect(c)
    r = c.post("/api/tiktok/posts", json={"clip_ids": [make_clip(c)], "account_id": aid, **DIRECT})
    assert r.status_code == 201, r.text
    p, _ = run_post(r.json()[0]["id"])
    assert p.status == "published" and p.tiktok_video_id == TT_MINE


# ---------- learning from your own clips ----------

def test_examples_for_orders_own_by_views_and_skips_fresh_posts(google, monkeypatch):  # noqa: F811
    c = new_client()
    aid = yt_connect(c)
    _uploads(c, aid, 3, age_days=4, views=[100, 500, 0])
    _uploads(c, aid, 1, age_days=1, views=[10**6])  # too fresh: views haven't settled
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        db.add(TikTokPost(user_id=uid, clip_id=make_clip(c, title="Tok winner"), account_id=None, status="published",
                          tiktok_video_id=TT_MINE, view_count=300, posted_at=now() - timedelta(days=3, hours=1)))
        db.commit()
    similar = {"query": "q", "items": [{"title": "Popular", "tags": [], "hashtags": ["gta6"], "views": 9_000_000,
                                        "url": "https://www.youtube.com/watch?v=aaaaaaaaaa1"}]}
    monkeypatch.setattr(trends, "find_similar", lambda *a: similar)
    got = trends.examples_for(uid, "some clip text", {}, "en")
    assert [(x["source"], x["views"]) for x in got] == [("own", 500), ("own", 300), ("own", 100), ("own", 0), ("similar", 9_000_000)]
    assert got[1]["platform"] == "tiktok" and got[1]["title"] == "Tok winner"
    assert got[0]["platform"] == "youtube" and got[0]["hashtags"] == ["gta6", "gaming"]
    assert got[0]["url"].startswith("https://www.youtube.com/watch?v=")
    assert trends.examples_for(new_client().get("/api/auth/me").json()["id"], "x", {}, "en")[0]["source"] == "similar"

    stats = c.get("/api/youtube/stats").json()["top_clips"]
    assert [s["views"] for s in stats] == [10**6, 500, 100, 0]  # every synced upload, newest included
    assert new_client().get("/api/youtube/stats").json()["top_clips"] == []


def test_own_examples_reach_the_generate_prompt(google, monkeypatch):  # noqa: F811
    c = new_client()
    aid = yt_connect(c)
    _uploads(c, aid, 1, views=[77_777])
    with SessionLocal() as db:
        u = db.scalar(select(YouTubeUpload).where(YouTubeUpload.account_id == aid, YouTubeUpload.view_count == 77_777))
        db.get(Clip, u.clip_id).title = "My heist went sideways"
        db.commit()
    seen = {}

    def fake(system, user, timeout=180):
        seen["user"] = user
        return {"titles": ["A heist that went wrong"], "description": "d", "hashtags": ["heist"]}
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(trends, "find_similar", lambda *a: None)  # no research configured: own winners still steer
    m = trends.generate(c.get("/api/auth/me").json()["id"], "The heist went wrong when the car crashed.", {}, "en")
    assert '"My heist went sideways" (77,777 views)' in seen["user"]
    assert m["sources"]["own"] == [{"title": "My heist went sideways", "views": 77_777, "platform": "youtube",
                                    "url": f"https://www.youtube.com/watch?v={u.youtube_video_id}"}]
    assert m["sources"]["similar"] == []
