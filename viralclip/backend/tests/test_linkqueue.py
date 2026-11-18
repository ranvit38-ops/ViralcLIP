"""Link queue (POST/GET/DELETE /api/queue, linkqueue.tick) and GET /api/automation/status.

Uses the fakes of test_automation_e2e: Twitch + Google at the HTTP layer, yt-dlp at the library layer, fast
analyze/render stand-ins, and the real worker ticks. A queued link goes through the real chain."""
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import linkqueue, youtube
from app.db import SessionLocal
from app.models import AutomationEvent, LinkQueueItem, Project, SourceVideo, User, YouTubeUpload
from tests.conftest import new_client
from tests.test_automation_e2e import FakeYDL, minute, rows, stack  # noqa: F401  (pytest fixture)
from tests.test_twitch import OTHER, enable, tw  # noqa: F401  (pytest fixture)
from tests.test_youtube import google  # noqa: F401  (pytest fixture)

MINE_A, MINE_B, THEIRS = "AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"


@pytest.fixture(autouse=True)
def clean_queue():
    yield
    with SessionLocal() as db:  # never leave waiting links for later tests' worker ticks (no fakes there)
        db.execute(delete(LinkQueueItem))
        db.commit()


def add(c, *links):
    r = c.post("/api/queue", json={"links": list(links)})
    assert r.status_code == 200, r.text
    return r.json()


def items(c):
    return {i["url"]: i for i in c.get("/api/queue").json()["items"]}


def test_add_rejects_unknown_imported_and_queued_links_and_dedupes():
    c = new_client()
    uid = c.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        p = Project(user_id=uid, name="old", status="ready")
        db.add(p)
        db.flush()
        db.add(SourceVideo(user_id=uid, platform="youtube", external_id="DDDDDDDDDDD", project_id=p.id))
        db.commit()
    r = add(c, " https://youtu.be/AAAAAAAAAAA ", "https://youtu.be/AAAAAAAAAAA", "https://www.twitch.tv/videos/81000001",
            "https://vm.tiktok.com/ZMabc123/", "https://vimeo.com/123", "", "https://youtu.be/DDDDDDDDDDD")
    assert [(a["url"], a["platform"], a["status"]) for a in r["added"]] == [
        ("https://youtu.be/AAAAAAAAAAA", "youtube", "waiting"), ("https://www.twitch.tv/videos/81000001", "twitch", "waiting"),
        ("https://vm.tiktok.com/ZMabc123/", "tiktok", "waiting")]
    assert r["rejected"] == [{"url": "https://vimeo.com/123", "reason": "Paste a Twitch VOD, YouTube video or TikTok link."},
                             {"url": "https://youtu.be/DDDDDDDDDDD", "reason": "Already imported."}]
    # the same video under another URL form is already queued
    assert add(c, "https://www.youtube.com/watch?v=AAAAAAAAAAA")["rejected"][0]["reason"] == "Already in the queue."
    assert c.post("/api/queue", json={"links": ["https://youtu.be/x"] * 51}).status_code == 422
    g = c.get("/api/queue").json()
    assert g["buffer_days"] == 3 and g["automation_enabled"] is False and len(g["items"]) == 3
    assert set(g["items"][0]) == {"id", "url", "platform", "external_id", "status", "note", "project_id", "tries",
                                  "created_at", "updated_at"}
    assert g["items"][0]["external_id"] == MINE_A and g["items"][2]["external_id"] is None  # short link: resolved later


def test_queue_skips_others_videos_runs_one_at_a_time_and_waits_for_the_buffer(stack):
    s, c = stack, stack.c
    assert c.put(f"/api/youtube/accounts/{c.yaid}/rule", json={
        "enabled": True, "time_of_day": "18:00", "timezone": "UTC", "clips_per_day": 2}).status_code == 200
    assert enable(c, auto_approve={"enabled": True, "top_n": 3, "min_score": 0}, queue_buffer_days=1)["queue_buffer_days"] == 1
    s.google.videos[MINE_A] = {"channelId": "UC_mine", "title": "Mine A"}
    s.google.videos[MINE_B] = {"channelId": "UC_mine", "title": "Mine B"}
    s.google.videos[THEIRS] = {"channelId": "UC_someone_else", "channelTitle": "Rockstar Games", "title": "Trailer"}
    s.tw.vod("82000001", user_id=OTHER)
    add(c, f"https://youtu.be/{THEIRS}", "https://www.twitch.tv/videos/82000001", f"https://youtu.be/{MINE_A}",
        f"https://youtu.be/{MINE_B}")
    a, b = f"https://youtu.be/{MINE_A}", f"https://youtu.be/{MINE_B}"

    minute(s)
    q = items(c)
    assert q[f"https://youtu.be/{THEIRS}"]["status"] == "skipped" and "Rockstar Games" in q[f"https://youtu.be/{THEIRS}"]["note"]
    assert q["https://www.twitch.tv/videos/82000001"]["status"] == "skipped"
    assert "someone" in q["https://www.twitch.tv/videos/82000001"]["note"]
    assert q[a]["status"] == "started" and q[a]["project_id"] and q[b]["status"] == "waiting"
    assert FakeYDL.urls == [f"https://www.youtube.com/watch?v={MINE_A}"]  # the others were never downloaded

    seen = []
    for _ in range(12):  # A: download -> analyze -> approve -> render -> 3 scheduled uploads
        minute(s)
        q = items(c)
        seen.append(q[a]["status"])
        assert q[b]["status"] == "waiting"  # one at a time, then held by the buffer (3 posts / 2 a day >= 1 day)
    assert seen[-1] == "done" and "started" in seen
    ups = rows(YouTubeUpload, c.uid)
    assert len(ups) == 3 and all(u.schedule_status == "scheduled" for u in ups)
    st = c.get("/api/automation/status").json()
    assert st["queue"] == {"waiting": 1, "started": 0, "done": 1, "skipped": 2, "failed": 0}
    assert (st["scheduled_posts"], st["posts_per_day"], st["runway_days"], st["buffer_days"]) == (3, 2, 1.5, 1)
    assert st["last_scheduled_at"] == youtube._aware(max(u.publish_at for u in ups)).isoformat()
    assert st["enabled"] is True and st["add_more_links"] is False
    assert {f["name"] for f in st["failures"]} == {f"https://youtu.be/{THEIRS}", "https://www.twitch.tv/videos/82000001"}
    assert all(f["kind"] == "queue" and f["error"] and f["at"] for f in st["failures"])

    with SessionLocal() as db:  # the posts go out: the runway drops and the next link starts
        for u in db.scalars(select(YouTubeUpload).where(YouTubeUpload.user_id == c.uid)):
            u.schedule_status = "published"
        db.commit()
    minute(s)
    assert items(c)[b]["status"] == "started"
    assert len(FakeYDL.urls) == 2
    assert c.delete(f"/api/queue/{items(c)[b]['id']}").status_code == 409  # in flight
    assert c.delete(f"/api/queue/{items(c)[a]['id']}").status_code == 409  # done stays as history
    assert c.delete(f"/api/queue/{q[f'https://youtu.be/{THEIRS}']['id']}").status_code == 204
    assert any("queued link" in e.message for e in rows(AutomationEvent, c.uid) if e.kind == "import")


def test_failed_project_fails_the_link_with_its_error(stack, monkeypatch):
    s, c = stack, stack.c
    enable(c)

    class Broken(FakeYDL):
        def download(self, urls):
            raise RuntimeError("ERROR: [youtube] 403 Forbidden")
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Broken)
    s.google.videos[MINE_A] = {"channelId": "UC_mine", "title": "Doomed"}
    add(c, f"https://youtu.be/{MINE_A}")
    for _ in range(3):
        minute(s)
    (it,) = items(c).values()
    assert it["status"] == "failed" and "download" in it["note"].lower()
    st = c.get("/api/automation/status").json()
    assert st["queue"]["failed"] == 1 and st["add_more_links"] is True  # nothing waiting, nothing scheduled
    assert [f["kind"] for f in st["failures"]] == ["queue"]  # its project isn't listed a second time
    with SessionLocal() as db:  # an automated project that failed on its own (e.g. a watched channel) is listed
        db.add(Project(user_id=c.uid, name="Watched", status="failed", error="disk died",
                       settings={"automation": {"auto": True, "processed": True}}))
        db.commit()
    f = c.get("/api/automation/status").json()["failures"]
    assert {"kind": "project", "name": "Watched", "error": "disk died"}.items() <= f[0].items()


def test_transient_errors_retry_then_fail(monkeypatch):
    c = new_client()
    uid = c.get("/api/auth/me").json()["id"]
    add(c, f"https://youtu.be/{MINE_A}")

    def down(*a, **kw):
        raise HTTPException(502, "Google is down")
    monkeypatch.setattr(youtube, "create_import", down)
    with SessionLocal() as db:
        user = db.get(User, uid)
        settings = {"queue_buffer_days": 3}
        linkqueue.tick(db, user, settings)
        (it,) = items(c).values()
        assert (it["status"], it["tries"], it["note"]) == ("waiting", 1, "Google is down")
        for _ in range(9):
            linkqueue.tick(db, user, settings)
    (it,) = items(c).values()
    assert (it["status"], it["tries"]) == ("failed", 10)
    assert c.delete(f"/api/queue/{it['id']}").status_code == 204
    assert c.get("/api/queue").json()["items"] == []


def test_no_connected_account_skips_without_importing():
    c = new_client()
    uid = c.get("/api/auth/me").json()["id"]
    add(c, "https://www.twitch.tv/videos/83000001", f"https://youtu.be/{MINE_A}")
    with SessionLocal() as db:
        linkqueue.tick(db, db.get(User, uid), {"queue_buffer_days": 3})
    q = items(c)
    assert {i["status"] for i in q.values()} == {"skipped"}
    assert "Connect the Twitch account" in q["https://www.twitch.tv/videos/83000001"]["note"]
    assert rows(Project, uid) == []


def test_isolation_between_users():
    a, b = new_client(), new_client()
    (it,) = add(a, f"https://youtu.be/{MINE_A}")["added"]
    assert b.get("/api/queue").json()["items"] == []
    assert b.delete(f"/api/queue/{it['id']}").status_code == 404
    assert b.delete("/api/queue/999999").status_code == 404
    assert add(b, f"https://youtu.be/{MINE_A}")["added"]  # not a duplicate of someone else's link
    assert b.get("/api/automation/status").json()["queue"] == {"waiting": 1, "started": 0, "done": 0, "skipped": 0, "failed": 0}
    st = new_client().get("/api/automation/status").json()
    assert st == {"enabled": False, "queue": {k: 0 for k in linkqueue.STATUSES}, "scheduled_posts": 0,
                  "last_scheduled_at": None, "posts_per_day": 6, "runway_days": None, "buffer_days": 3,
                  "add_more_links": True, "failures": []}
    assert a.delete(f"/api/queue/{it['id']}").status_code == 204
