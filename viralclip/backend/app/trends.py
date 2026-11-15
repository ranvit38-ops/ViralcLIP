"""Trend research: titles and hashtags of similar Shorts that did well, via the official YouTube Data API v3.

Only search.list (100 units) and videos.list (1 unit); no page scraping. The results are style examples for
llm.generate_metadata, never a view guarantee. Best-effort throughout: any problem returns None and metadata is
generated exactly as without research.

Quota: with YOUTUBE_RESEARCH_API_KEY (ideally its own Cloud project) research has its own 10,000 units/day.
Without it, the connected channel's OAuth token is used, which shares the upload project's quota, so research
only runs while today's uploads (done + queued) leave room. Searches are counted per Pacific day in the DB.
"""
import logging
import re
import time
from datetime import timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, llm, youtube
from .db import SessionLocal
from .models import Clip, ResearchUsage, TikTokPost, TrendCache, YouTubeAccount, YouTubeUpload, now

log = logging.getLogger("viralclip.trends")

API = youtube.API
SEARCH_UNITS = 100 + 1  # search.list + one videos.list
UPLOAD_UNITS = 1600
CACHE_TTL = timedelta(hours=24)
HASHTAG = re.compile(r"#(\w+)")
VIDEO_ID = re.compile(r"^[\w-]{6,20}$")
http = httpx.Client(timeout=httpx.Timeout(8, connect=4))
_down_until = 0.0  # ponytail: per-process circuit breaker after a network error; good enough for 1-N workers


class QuotaExceeded(Exception):
    pass


def budget(source: str) -> int:
    return int(config.YOUTUBE_RESEARCH_DAILY_SEARCHES or (90 if source == "api_key" else 20))


def _oauth_account(db: Session, user_id: int) -> YouTubeAccount | None:
    if not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        return None
    return db.scalar(select(YouTubeAccount).where(YouTubeAccount.user_id == user_id, YouTubeAccount.status == "ok")
                     .order_by(YouTubeAccount.id).limit(1))


def source(db: Session, user_id: int) -> str:
    if config.YOUTUBE_RESEARCH_API_KEY:
        return "api_key"
    return "oauth" if _oauth_account(db, user_id) else "none"


def _upload_headroom(db: Session, searches: int) -> bool:
    """Does one more search fit next to today's uploads in the shared OAuth project?

    ponytail: project-wide estimate (uploads started today + every queued upload, all users of this install);
    it ignores other apps using the same Cloud project. A dedicated research key avoids the question entirely.
    """
    today = youtube.pacific_day()
    started = db.scalar(select(func.count()).select_from(YouTubeUpload).where(YouTubeUpload.quota_day == today))
    pending = db.scalar(select(func.count()).select_from(YouTubeUpload).where(
        YouTubeUpload.upload_status.in_(("queued", "uploading")),
        or_(YouTubeUpload.quota_day.is_(None), YouTubeUpload.quota_day != today)))
    return (started + pending) * UPLOAD_UNITS + (searches + 1) * SEARCH_UNITS <= config.YOUTUBE_DAILY_QUOTA


def _today(db: Session) -> ResearchUsage:
    day = youtube.pacific_day()
    if db.get(ResearchUsage, day) is None:
        try:
            db.add(ResearchUsage(day=day, searches=0, quota_exceeded=False))
            db.commit()
        except IntegrityError:  # another worker created it first
            db.rollback()
    return db.get(ResearchUsage, day)


def _reserve(db: Session, src: str) -> bool:
    """Atomically take one search from today's budget (counts even if the call later fails: it may cost quota)."""
    row = _today(db)
    if row.quota_exceeded or (src == "oauth" and not _upload_headroom(db, row.searches)):
        return False
    r = db.execute(update(ResearchUsage).where(
        ResearchUsage.day == row.day, ResearchUsage.searches < budget(src), ResearchUsage.quota_exceeded.is_(False))
        .values(searches=ResearchUsage.searches + 1))
    db.commit()
    return r.rowcount == 1


def status(db: Session, user_id: int) -> dict:
    src = source(db, user_id)
    row = db.get(ResearchUsage, youtube.pacific_day())
    used = row.searches if row else 0
    ok = (src != "none" and not (row and row.quota_exceeded) and used < budget(src)
          and (src == "api_key" or _upload_headroom(db, used)))
    return {"available": ok, "source": src, "searches_today": used, "budget": budget(src)}


def _get(db: Session, user_id: int, src: str, path: str, params: dict) -> dict:
    if src == "api_key":
        r = http.get(f"{API}/{path}", params={**params, "key": config.YOUTUBE_RESEARCH_API_KEY})
    else:
        tok = youtube.access_token(db, _oauth_account(db, user_id))
        r = http.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {tok}"})
    if r.status_code == 403 and youtube._google_error(r)[0] in ("quotaExceeded", "dailyLimitExceeded"):
        # Back off until the next Pacific midnight: tomorrow has a fresh ResearchUsage row.
        db.execute(update(ResearchUsage).where(ResearchUsage.day == youtube.pacific_day()).values(quota_exceeded=True))
        db.commit()
        raise QuotaExceeded()
    if r.status_code in (400, 401, 403):
        # Bad/restricted key or forbidden: retrying on every clip would burn the budget, so pause for the day.
        db.execute(update(ResearchUsage).where(ResearchUsage.day == youtube.pacific_day()).values(quota_exceeded=True))
        db.commit()
        log.warning("YouTube research disabled until tomorrow: %s %s", r.status_code, youtube._google_error(r)[0])
        raise QuotaExceeded()
    if r.status_code >= 300:
        raise RuntimeError(f"YouTube research {path} failed ({r.status_code}: {youtube._google_error(r)[0]})")
    return r.json()


def _search(db: Session, user_id: int, src: str, q: str, language: str | None) -> list[dict]:
    params = {"part": "snippet", "type": "video", "videoDuration": "short", "order": "viewCount", "maxResults": 15,
              "publishedAfter": (now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "q": q}
    if language:
        params["relevanceLanguage"] = language
    found = _get(db, user_id, src, "search", params)
    ids = [i["id"]["videoId"] for i in found.get("items", []) if VIDEO_ID.match(str((i.get("id") or {}).get("videoId", "")))]
    if not ids:
        return []
    out = []
    for it in _get(db, user_id, src, "videos", {"part": "snippet,statistics", "id": ",".join(ids)}).get("items", []):
        sn, st = it.get("snippet") or {}, it.get("statistics") or {}
        if not VIDEO_ID.match(str(it.get("id", ""))):
            continue
        title = str(sn.get("title", ""))[:200]
        tags = HASHTAG.findall(f"{title} {sn.get('description', '')}")
        out.append({"title": title, "tags": [str(t)[:60] for t in (sn.get("tags") or [])[:15]],
                    "hashtags": list(dict.fromkeys(t for t in map(llm._clean_tag, tags) if t))[:15],
                    "views": int(st.get("viewCount") or 0),
                    "url": f"https://www.youtube.com/watch?v={quote(it['id'])}"})
    return sorted(out, key=lambda x: x["views"], reverse=True)[:8]


def query_for(clip_text: str, niche: str | None) -> str:
    niche = re.sub(r"\s+", " ", niche or "").strip().lower()
    kws = [k for k in llm._keywords(clip_text or "", 6) if k not in niche.split()][:3]
    return " ".join([niche] * bool(niche) + sorted(kws))


def find_similar(user_id: int, clip_text: str, niche: str | None, language: str | None) -> dict | None:
    """{"query", "items": [{title, tags, hashtags, views, url}, ...]} for the top ~8 by views, or None."""
    global _down_until
    q = query_for(clip_text, niche)
    if not q or time.monotonic() < _down_until:
        return None
    key = f"{language or ''}|{q}"[:300]
    with SessionLocal() as db:
        hit = db.get(TrendCache, key)
        if hit and youtube._aware(hit.fetched_at) > now() - CACHE_TTL:
            return {"query": q, "items": hit.results} if hit.results else None
        src = source(db, user_id)
        if src == "none" or not _reserve(db, src):
            return None
        try:
            items = _search(db, user_id, src, q, language)
        except QuotaExceeded:
            log.warning("YouTube research quota exceeded; skipping research until the next Pacific midnight")
            return None
        except httpx.TransportError as e:
            _down_until = time.monotonic() + 600
            log.warning("YouTube research unreachable (%s); skipping it for 10 minutes", type(e).__name__)
            return None
        try:
            db.merge(TrendCache(key=key, results=items, fetched_at=now()))
            db.commit()
        except IntegrityError:  # a parallel worker cached the same query
            db.rollback()
        return {"query": q, "items": items} if items else None


OWN_MIN_AGE = timedelta(days=3)  # younger posts haven't settled; their view counts mislead


def own_top(user_id: int, limit: int = 5) -> list[dict]:
    """The user's own published clips with the most views (synced daily by youtube/tiktok.sync_stats). No quota."""
    cutoff = now() - OWN_MIN_AGE
    with SessionLocal() as db:
        rows = [("youtube", t, h, v, f"https://www.youtube.com/watch?v={quote(i)}") for t, h, v, i in db.execute(
            select(Clip.title, Clip.hashtags, YouTubeUpload.view_count, YouTubeUpload.youtube_video_id)
            .join(Clip, Clip.id == YouTubeUpload.clip_id)
            .where(YouTubeUpload.user_id == user_id, YouTubeUpload.view_count.is_not(None),
                   YouTubeUpload.uploaded_at <= cutoff).order_by(YouTubeUpload.view_count.desc()).limit(limit))]
        # UNVERIFIED: tiktok.com/@/video/<id> without the username; TikTok redirects it to the post.
        rows += [("tiktok", t, h, v, f"https://www.tiktok.com/@/video/{quote(i)}") for t, h, v, i in db.execute(
            select(Clip.title, Clip.hashtags, TikTokPost.view_count, TikTokPost.tiktok_video_id)
            .join(Clip, Clip.id == TikTokPost.clip_id)
            .where(TikTokPost.user_id == user_id, TikTokPost.view_count.is_not(None), TikTokPost.posted_at <= cutoff)
            .order_by(TikTokPost.view_count.desc()).limit(limit))]
    rows = [r for r in rows if (r[1] or "").strip()]
    rows.sort(key=lambda r: r[3], reverse=True)
    return [{"title": t, "tags": [], "hashtags": list(dict.fromkeys(filter(None, map(llm._clean_tag, h or []))))[:15],
             "views": int(v), "url": u, "platform": p, "source": "own"} for p, t, h, v, u in rows[:limit]]


def _niche(prefs: dict) -> str:
    return prefs.get("niche") or (prefs.get("category") if prefs.get("category") not in ("auto", "auto-detect") else "") or ""


def examples_for(user_id: int, clip_text: str, prefs: dict, language: str | None) -> list[dict]:
    """Style examples for metadata: the user's own top clips (source="own", by views, ≥3 days old) first, then
    similar Shorts from research (source="similar"). Each: {title, hashtags, views, url, platform, source}.
    Never raises; [] when there is nothing."""
    out = []
    try:
        out += own_top(user_id)
    except Exception as e:  # noqa: BLE001 - examples are optional
        log.warning("Own-clip examples failed: %s", e)
    try:
        found = find_similar(user_id, clip_text, _niche(prefs), prefs.get("language") or language) if clip_text else None
    except Exception as e:  # noqa: BLE001 - research is optional
        log.warning("YouTube research failed, generating without it: %s", e)
        found = None
    out += [{**x, "platform": "youtube", "source": "similar"} for x in (found or {}).get("items", [])]
    return out


def sources(items: list[dict], clip_text: str, prefs: dict) -> dict:
    """score_breakdown.metadata_sources for these examples."""
    sim = [x for x in items if x["source"] == "similar"]
    return {"query": query_for(clip_text, _niche(prefs)) if sim else "",
            "similar": [{k: x[k] for k in ("title", "views", "url")} for x in sim[:5]],
            "own": [{k: x[k] for k in ("title", "views", "url", "platform")} for x in items if x["source"] == "own"]}


def generate(user_id: int, text: str, prefs: dict, language: str | None, research: bool = True) -> dict:
    """llm.generate_metadata steered by examples_for (own winners + similar Shorts). Examples never raise."""
    items = examples_for(user_id, text, prefs, language) if research and text else []
    if not items:
        return llm.generate_metadata(text, prefs, language, None)
    src = sources(items, text, prefs)
    m = llm.generate_metadata(text, prefs, language, {"query": src["query"], "items": items})
    if m.get("sources"):
        m["sources"] = src
    return m
