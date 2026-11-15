"""Similar-Shorts research (YouTube Data API v3 search.list + videos.list), with Google mocked at the HTTP layer."""
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import delete, func, select

from app import config, llm, pipeline, trends, youtube
from app.db import SessionLocal
from app.models import Clip, ResearchUsage, TrendCache, YouTubeUpload, now
from tests.conftest import make_video, new_client, upload
from tests.test_pipeline import drain
from tests.test_youtube import connect as yt_connect, google  # noqa: F401  (pytest fixture)

HEIST = ("The heist went wrong when the getaway driver crashed the car. The police chased the getaway car "
         "for ten minutes. We escaped the heist with all the money in the end.")
VIDEOS = [  # id, title, views, description, tags
    ("aaaaaaaaaa1", "GTA 6 heist went WRONG #gta6 #shorts", 1_200_000, "Insane #heist #gta6heist", ["gta 6"]),
    ("aaaaaaaaaa2", "Best getaway ever #gta6", 800_000, "#fyp #getaway", []),
    ("aaaaaaaaaa3", "Cooking pasta fast", 50_000, "#cooking #fyp", []),
]


class FakeYT:
    def __init__(self):
        self.searches, self.lists, self.reqs, self.fail = 0, 0, [], None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.reqs.append(req)
        q = parse_qs(urlparse(str(req.url)).query)
        if self.fail:
            return httpx.Response(403, json={"error": {"errors": [{"reason": self.fail}], "message": "Quota"}})
        if req.url.path.endswith("/search"):
            self.searches += 1
            return httpx.Response(200, json={"items": [{"id": {"kind": "youtube#video", "videoId": v[0]}} for v in VIDEOS]})
        if req.url.path.endswith("/videos"):
            self.lists += 1
            ids = q["id"][0].split(",")
            return httpx.Response(200, json={"items": [
                {"id": v[0], "snippet": {"title": v[1], "description": v[3], "tags": v[4]},
                 "statistics": {"viewCount": str(v[2])}} for v in reversed(VIDEOS) if v[0] in ids]})
        return httpx.Response(404)


@pytest.fixture
def yt(monkeypatch):
    fake = FakeYT()
    monkeypatch.setattr(trends, "http", httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(trends, "_down_until", 0.0)
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "")
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_DAILY_SEARCHES", "")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    with SessionLocal() as db:  # research state is install-wide; start each test clean
        db.execute(delete(ResearchUsage))
        db.execute(delete(TrendCache))
        db.commit()
    return fake


def uid(c):
    return c.get("/api/auth/me").json()["id"]


def searches_today():
    with SessionLocal() as db:
        row = db.get(ResearchUsage, youtube.pacific_day())
        return row.searches if row else 0


def test_api_key_research_and_cache(yt, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "research-key")
    c = new_client()
    got = trends.find_similar(uid(c), HEIST, "GTA 6", "en")
    assert got["query"].startswith("gta 6 ") and len(got["query"].split()) == 5  # niche + 3 clip keywords
    assert [x["views"] for x in got["items"]] == [1_200_000, 800_000, 50_000]  # sorted by views
    top = got["items"][0]
    assert top["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaa1" and top["tags"] == ["gta 6"]
    assert top["hashtags"] == ["gta6", "shorts", "heist", "gta6heist"]
    s = parse_qs(urlparse(str(yt.reqs[0].url)).query)
    assert {k: v[0] for k, v in s.items() if k != "publishedAfter"} == {
        "part": "snippet", "type": "video", "videoDuration": "short", "order": "viewCount", "maxResults": "15",
        "q": got["query"], "relevanceLanguage": "en", "key": "research-key"}
    after = (now() - timedelta(days=30)).strftime("%Y-%m-%d")
    assert s["publishedAfter"][0].startswith(after)
    assert "Authorization" not in yt.reqs[0].headers
    assert parse_qs(urlparse(str(yt.reqs[1].url)).query)["part"] == ["snippet,statistics"]
    assert searches_today() == 1

    # Same clip again (any user): served from the 24 h cache, no second search.
    assert trends.find_similar(uid(new_client()), HEIST, "gta  6", "en") == got
    assert yt.searches == 1 and searches_today() == 1
    # Cache expires after 24 h.
    with SessionLocal() as db:
        db.get(TrendCache, f"en|{got['query']}").fetched_at = now() - timedelta(hours=25)
        db.commit()
    trends.find_similar(uid(c), HEIST, "GTA 6", "en")
    assert yt.searches == 2
    assert c.get("/api/system").json()["research"] == {"available": True, "source": "api_key", "searches_today": 2, "budget": 90}


def test_budget_exhaustion_skips_research(yt, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "k")
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_DAILY_SEARCHES", "2")
    u = uid(new_client())
    assert trends.find_similar(u, HEIST, "gta 6", "en") and trends.find_similar(u, HEIST, "valorant", "en")
    assert trends.find_similar(u, HEIST, "minecraft", "en") is None
    assert yt.searches == 2 and searches_today() == 2
    with SessionLocal() as db:
        assert trends.status(db, u)["available"] is False


def test_quota_exceeded_backs_off_until_pacific_midnight(yt, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "k")
    u = uid(new_client())
    yt.fail = "quotaExceeded"
    assert trends.find_similar(u, HEIST, "gta 6", "en") is None
    yt.fail, calls = None, len(yt.reqs)
    assert trends.find_similar(u, HEIST, "valorant", "en") is None
    assert len(yt.reqs) == calls  # backed off: no request at all
    with SessionLocal() as db:
        assert trends.status(db, u)["available"] is False and db.get(ResearchUsage, youtube.pacific_day()).quota_exceeded
    # The next Pacific day has a fresh quota.
    monkeypatch.setattr(youtube, "pacific_day", lambda at=None: "2099-01-01")
    assert trends.find_similar(u, HEIST, "valorant", "en")


def test_oauth_fallback_respects_upload_headroom(yt, google, monkeypatch):  # noqa: F811
    c = new_client()
    assert trends.find_similar(uid(c), HEIST, "gta 6", "en") is None  # no key, no channel: silently skipped
    assert c.get("/api/system").json()["research"]["source"] == "none" and not yt.reqs
    yt_connect(c)
    with SessionLocal() as db:  # uploads from other tests share "today": measure what's already used
        today = youtube.pacific_day()
        used = db.scalar(select(func.count()).select_from(YouTubeUpload).where(
            (YouTubeUpload.quota_day == today) | YouTubeUpload.upload_status.in_(("queued", "uploading"))))
    monkeypatch.setattr(config, "YOUTUBE_DAILY_QUOTA", used * 1600 + 101 + 50)  # room for exactly one search
    assert trends.find_similar(uid(c), HEIST, "gta 6", "en")
    assert yt.reqs[0].headers["Authorization"] == "Bearer at1" and "key" not in str(yt.reqs[0].url)
    assert c.get("/api/system").json()["research"] == {"available": False, "source": "oauth", "searches_today": 1, "budget": 20}
    assert trends.find_similar(uid(c), HEIST, "valorant", "en") is None  # would eat into upload quota
    assert yt.searches == 1
    monkeypatch.setattr(config, "YOUTUBE_DAILY_QUOTA", 10**9)
    assert trends.find_similar(uid(c), HEIST, "valorant", "en") and yt.searches == 2


def examples():
    return {"query": "gta 6 heist", "items": [
        {"title": t, "tags": [], "hashtags": [llm._clean_tag(h) for h in re.findall(r"#(\w+)", t + " " + d)],
         "views": v, "url": f"https://www.youtube.com/watch?v={i}"} for i, t, v, d, _ in VIDEOS]}


def test_examples_reach_the_llm_prompt(monkeypatch):
    seen = {}

    def fake(system, user, *a, **k):
        seen.update(system=system, user=user)
        return {"titles": ["GTA 6 heist went WRONG #gta6 #shorts", "The getaway crash that ruined our heist"],
                "description": "A heist goes wrong.", "hashtags": ["gta6", "heist", "shorts"]}

    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "chat_json", fake)
    m = llm.generate_metadata(HEIST, {"niche": "GTA 6", "hashtag_count": 5}, "en", examples())
    assert '"GTA 6 heist went WRONG #gta6 #shorts" (1,200,000 views)' in seen["user"]
    assert "Hashtags they use, most frequent first: gta6, fyp," in seen["user"]
    assert "Never copy a title verbatim" in seen["system"] and "Niche / game: GTA 6" in seen["system"]
    assert m["titles"][0] == "The getaway crash that ruined our heist"  # the copied example title is dropped
    assert m["method"] == "llm+trends" and m["sources"]["query"] == "gta 6 heist"
    assert m["sources"]["similar"][0] == {"title": VIDEOS[0][1], "views": 1_200_000, "url": "https://www.youtube.com/watch?v=aaaaaaaaaa1"}


def test_basic_path_adds_only_relevant_hashtags():
    prefs = {"niche": "GTA 6", "hashtag_count": 6}
    m = llm.basic_metadata(HEIST, prefs, examples())
    assert m["hashtags"][0] == "gta6" and m["hashtags"][-1] == "shorts" and len(m["hashtags"]) == 6
    assert "heist" in m["hashtags"] or "gta6heist" in m["hashtags"]
    assert not {"fyp", "cooking"} & set(m["hashtags"])
    assert m["titles"] == llm.basic_metadata(HEIST, prefs)["titles"]  # titles stay the clip's own lines
    assert m["method"] == "basic+trends" and len(m["sources"]["similar"]) == 3


def test_no_research_configured_is_the_old_behavior(yt, monkeypatch):
    c = new_client()
    prefs = {"hashtag_count": 4}
    assert trends.generate(uid(c), HEIST, prefs, "en") == llm.basic_metadata(HEIST, prefs) == llm.generate_metadata(HEIST, prefs, "en")
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "chat_json", lambda s, u, *a, **k: {"titles": ["A heist"], "description": "d", "hashtags": ["x"]})
    assert trends.generate(uid(c), HEIST, prefs, "en")["method"] == "llm"
    assert not yt.reqs


def test_research_errors_never_break_metadata(yt, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "k")
    u = uid(new_client())
    yt.fail = "keyInvalid"
    assert trends.generate(u, HEIST, {}, "en")["method"] == "basic"
    # a bad key pauses research for the day instead of burning a search on every clip
    from app.db import SessionLocal
    from app.models import ResearchUsage
    with SessionLocal() as db:
        assert trends.status(db, u)["available"] is False
        db.query(ResearchUsage).delete()
        db.commit()
    yt.fail = None

    def down(req):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(trends, "http", httpx.Client(transport=httpx.MockTransport(down)))
    assert trends.generate(u, HEIST, {"niche": "valorant"}, "en")["method"] == "basic"
    assert trends._down_until > 0  # circuit open: later clips don't wait on timeouts


def test_settings_niche_validation(client):
    assert client.put("/api/settings", json={"settings": {"metadata": {"niche": "  GTA   6 "}}}).json()["metadata"]["niche"] == "GTA 6"
    for bad in ["x" * 61, 5, "<script>"]:
        assert client.put("/api/settings", json={"settings": {"metadata": {"niche": bad}}}).status_code == 422


def test_analyze_metadata_job_and_regenerate_use_research(yt, monkeypatch):
    """Real analyze job (Whisper stubbed), then the project metadata job and the per-clip endpoint."""
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_API_KEY", "k")
    sents = [s for s in re.split(r"(?<=[.])\s+", HEIST * 3) if s]

    def fake_transcribe(chunks, model, language, ctx, lo, hi, duration=None):
        for _ in chunks:  # drain the stream like the real transcriber
            pass
        segs, t = [], 1.0
        for s in sents:
            words = [{"start": round(t + i * 0.4, 2), "end": round(t + i * 0.4 + 0.3, 2), "word": " " + w, "probability": 0.9}
                     for i, w in enumerate(s.split())]
            segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": s, "words": words})
            t = words[-1]["end"] + 0.5
        return segs, "en"

    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    c = new_client()
    assert c.put("/api/settings", json={"settings": {"metadata": {"niche": "GTA 6", "hashtag_count": 5}}}).status_code == 200
    pid, r = upload(c, make_video("heist.mp4", seconds=40), chunk=1_000_000)
    assert r.status_code == 200
    assert c.post(f"/api/projects/{pid}/analyze", json={"min_len": 10, "max_len": 20, "count": 2, "model": "tiny"}).status_code == 202
    drain()
    clips = c.get(f"/api/clips?project_id={pid}").json()
    assert clips and all(x["score_breakdown"]["metadata_method"] == "basic+trends" for x in clips)
    assert all(x["hashtags"][0] == "gta6" and x["score_breakdown"]["metadata_sources"]["similar"] for x in clips)

    with SessionLocal() as db:  # wipe provenance, then the metadata job must restore it from the cache
        for cl in db.scalars(select(Clip).where(Clip.project_id == pid)):
            cl.score_breakdown = {**cl.score_breakdown, "metadata_sources": None}
        db.commit()
    n = yt.searches
    assert c.post(f"/api/projects/{pid}/metadata", json={"fields": ["hashtags"]}).status_code == 202
    drain()
    assert all(x["score_breakdown"]["metadata_sources"] for x in c.get(f"/api/clips?project_id={pid}").json())
    assert yt.searches == n  # same clips, same queries: all cache hits

    one = c.post(f"/api/clips/{clips[0]['id']}/metadata", json={"fields": ["titles", "hashtags"]}).json()
    assert one["score_breakdown"]["metadata_method"] == "basic+trends"
    # description-only regeneration does no research and keeps the existing provenance
    one = c.post(f"/api/clips/{clips[0]['id']}/metadata", json={"fields": ["description"]}).json()
    assert one["score_breakdown"]["metadata_sources"]
    assert new_client().post(f"/api/clips/{clips[0]['id']}/metadata", json={"fields": ["titles"]}).status_code == 404
