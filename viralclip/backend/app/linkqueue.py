"""Link queue: paste many links once; automation imports the next one only when nothing is importing or
analyzing and the post schedule is running low (fewer than buffer_days of posts left on the fullest account).

tick() runs from automation.run_for_user (under the tick lock, only while automation is on). Each link goes
through the platform's own create_import, so the ownership check (your connected accounts only) and the
automated chain (analyze -> auto-approve -> render -> auto-publish) are exactly those of a normal import.
"""
import logging
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import importer, tiktok, twitch, youtube
from .auth import current_user
from .automation import ACTIVE, IMPORT_JOBS, _auto, event, settings_of
from .db import get_db
from .models import (AutoPublishRule, Job, LinkQueueItem, Project, SourceVideo, TikTokAutoPublishRule, TikTokPost,
                     User, YouTubeUpload, now)
from .youtube import _aware

log = logging.getLogger("viralclip.linkqueue")
router = APIRouter(tags=["queue"])  # no prefix: it also serves /api/automation/status

MAX_LINKS = 50
MAX_TRIES = 10
DEFAULT_POSTS_PER_DAY = 6  # YouTube's default quota allows about 6 uploads a day
STATUSES = ("waiting", "started", "done", "skipped", "failed")
# 4xx from create_import: the link can't be imported as it stands (not yours, not found, no account connected,
# already imported). Anything else (TikTok/Google down, disk full) is retried.
SKIP_CODES = (403, 404, 409, 422)
YT_PENDING = ("queued", "paused", "quota_exceeded", "uploading")
TT_PENDING = ("scheduled", "queued", "uploading", "processing")


def item_out(i: LinkQueueItem) -> dict:
    return {k: getattr(i, k) for k in ("id", "url", "platform", "external_id", "status", "note", "project_id", "tries",
                                       "created_at", "updated_at")}


# ---------- runway: how many days of posts are already lined up ----------

def runway(db: Session, user_id: int) -> dict:
    """Posts not yet published (YouTube scheduled/queued/uploading, TikTok scheduled/queued/uploading), per account.
    runway_days is the fullest account's pending / its posts per day (rule's clips_per_day, default 6), None if none."""
    ppd = {("youtube", r.account_id): r.clips_per_day for r in db.scalars(select(AutoPublishRule).where(
        AutoPublishRule.user_id == user_id, AutoPublishRule.enabled.is_(True)))}
    ppd |= {("tiktok", r.account_id): r.clips_per_day for r in db.scalars(select(TikTokAutoPublishRule).where(
        TikTokAutoPublishRule.user_id == user_id, TikTokAutoPublishRule.enabled.is_(True)))}
    yt = db.execute(select(YouTubeUpload.account_id, YouTubeUpload.publish_at).where(
        YouTubeUpload.user_id == user_id, YouTubeUpload.account_id.is_not(None),
        or_(YouTubeUpload.upload_status.in_(YT_PENDING),
            and_(YouTubeUpload.upload_status == "uploaded", YouTubeUpload.schedule_status == "scheduled",
                 YouTubeUpload.publish_at > now()))))
    tt = db.execute(select(TikTokPost.account_id, TikTokPost.publish_at).where(
        TikTokPost.user_id == user_id, TikTokPost.account_id.is_not(None), TikTokPost.status.in_(TT_PENDING)))
    pending, last = Counter(), None
    for platform, rows in (("youtube", yt), ("tiktok", tt)):
        for aid, at in rows:
            pending[(platform, aid)] += 1
            if at and (last is None or _aware(at) > last):
                last = _aware(at)
    days = [n / ppd.get(k, DEFAULT_POSTS_PER_DAY) for k, n in pending.items()]
    return {"scheduled_posts": sum(pending.values()), "last_scheduled_at": last.isoformat() if last else None,
            "posts_per_day": sum(ppd.values()) or DEFAULT_POSTS_PER_DAY,
            "runway_days": round(max(days), 2) if days else None}


# ---------- tick ----------

def project_error(db: Session, p: Project) -> str | None:
    """None when an automated project ended with clips; otherwise why it stopped (as process_projects reports it)."""
    if p.status == "ready":
        return None
    if p.status == "failed":
        return p.error or "The import failed."
    if p.has_audio is False:
        return "The video has no audio track; nothing to clip."
    j = db.scalar(select(Job).where(Job.project_id == p.id, Job.job_type == "analyze").order_by(Job.id.desc()).limit(1))
    return (j and j.error) or "Analysis ended without clips."


def _settle(db: Session, user: User) -> None:
    """started -> done | failed, once automation has processed the item's project."""
    for it in db.scalars(select(LinkQueueItem).where(LinkQueueItem.user_id == user.id, LinkQueueItem.status == "started")):
        p = db.get(Project, it.project_id) if it.project_id else None
        if p is None:
            it.status, it.note = "failed", "The project was deleted."
        elif _auto(p).get("processed"):
            err = project_error(db, p)
            it.status, it.note = ("done", None) if err is None else ("failed", err[:1000])
    db.commit()


def _busy(db: Session, user_id: int) -> bool:
    """A link is in flight, or something is importing, analyzing or rendering (its clips aren't scheduled yet)."""
    if db.scalar(select(LinkQueueItem.id).where(LinkQueueItem.user_id == user_id, LinkQueueItem.status == "started").limit(1)):
        return True
    return db.scalar(select(Job.id).join(Project).where(
        Project.user_id == user_id, Job.job_type.in_(IMPORT_JOBS + ("analyze", "render")),
        Job.status.in_(ACTIVE)).limit(1)) is not None


def _start(db: Session, user: User, it: LinkQueueItem) -> str:
    """Try to import one link. Returns its new status: started, skipped, waiting (retry later) or failed."""
    platform, vid, extra = importer.detect(it.url)
    try:
        if platform == "tiktok" and vid is None:
            got = tiktok.resolve_short(extra)
            if not got:
                raise HTTPException(502, "Couldn't open that TikTok short link right now.")
            extra, vid = got
        if platform == "twitch":
            p = twitch.create_import(db, user, vid, automated=True)
        elif platform == "youtube":
            p = youtube.create_import(db, user, vid, automated=True)
        else:
            p = tiktok.create_import(db, user, extra, vid, automated=True)
    except Exception as e:
        db.rollback()
        http = isinstance(e, HTTPException)
        if not http:
            log.exception("queued link %s failed to start", it.id)
        why = (e.detail if isinstance(e.detail, str) else e.detail.get("message", "")) if http else "Unexpected error."
        it.external_id = it.external_id or vid
        if http and e.status_code in SKIP_CODES:
            it.status, it.note = "skipped", why
            event(db, user.id, "skip", f"Skipped queued link {it.url}: {why}")
        else:
            it.tries += 1
            it.status, it.note = ("failed" if it.tries >= MAX_TRIES else "waiting"), why
            if it.status == "failed":
                event(db, user.id, "error", f"Gave up on queued link {it.url} after {it.tries} tries: {why}")
        db.commit()
        return it.status
    it.status, it.note, it.project_id, it.external_id = "started", None, p.id, vid
    event(db, user.id, "import", f"Importing queued link {it.url}", p.id)
    db.commit()
    return "started"


def tick(db: Session, user: User, s: dict) -> None:
    _settle(db, user)
    if _busy(db, user.id):
        return
    r = runway(db, user.id)["runway_days"]
    if r is not None and r >= s["queue_buffer_days"]:
        return  # enough posts lined up; the next link waits
    for it in list(db.scalars(select(LinkQueueItem).where(LinkQueueItem.user_id == user.id,
                                                          LinkQueueItem.status == "waiting").order_by(LinkQueueItem.id))):
        if _start(db, user, it) != "skipped":
            return  # one at a time; a skipped link doesn't hold up the next


# ---------- API ----------

class LinksIn(BaseModel):
    links: list[str] = Field(min_length=1, max_length=MAX_LINKS)


@router.post("/api/queue")
def add_links(body: LinksIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    queued = list(db.scalars(select(LinkQueueItem).where(LinkQueueItem.user_id == user.id)))
    urls = {i.url for i in queued}
    ids = {(i.platform, i.external_id) for i in queued if i.external_id}
    imported = {(sv.platform, sv.external_id) for sv in db.scalars(select(SourceVideo).join(
        Project, SourceVideo.project_id == Project.id).where(SourceVideo.user_id == user.id))}
    added, rejected = [], []
    for url in dict.fromkeys(u.strip() for u in body.links if u.strip()):  # trimmed, deduped, in order
        platform, vid, _ = importer.detect(url[:500]) if len(url) <= 500 else (None, None, None)
        if not platform:
            rejected.append({"url": url, "reason": importer.UNKNOWN})
        elif (platform, vid) in imported:
            rejected.append({"url": url, "reason": "Already imported."})
        elif url in urls or (vid and (platform, vid) in ids):
            rejected.append({"url": url, "reason": "Already in the queue."})
        else:
            it = LinkQueueItem(user_id=user.id, url=url, platform=platform, external_id=vid, status="waiting", tries=0)
            db.add(it)
            db.flush()
            urls.add(url)
            if vid:
                ids.add((platform, vid))
            added.append({"id": it.id, "url": url, "platform": platform, "status": it.status})
    db.commit()
    return {"added": added, "rejected": rejected}


@router.get("/api/queue")
def list_queue(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = settings_of(user)
    items = db.scalars(select(LinkQueueItem).where(LinkQueueItem.user_id == user.id).order_by(LinkQueueItem.id))
    return {"items": [item_out(i) for i in items], "buffer_days": s["queue_buffer_days"], "automation_enabled": s["enabled"]}


@router.delete("/api/queue/{item_id}", status_code=204)
def delete_item(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    it = db.get(LinkQueueItem, item_id)
    if not it or it.user_id != user.id:
        raise HTTPException(404, "Queued link not found")
    if it.status not in ("waiting", "skipped", "failed"):
        raise HTTPException(409, f"Can't remove a link that is {it.status}.")
    db.delete(it)
    db.commit()
    return Response(status_code=204)


@router.get("/api/automation/status")
def automation_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The weekly glance: is the queue moving, how many days of posts are lined up, what went wrong."""
    s = settings_of(user)
    counts = dict(db.execute(select(LinkQueueItem.status, func.count()).where(LinkQueueItem.user_id == user.id)
                             .group_by(LinkQueueItem.status)).all())
    queue = {k: counts.get(k, 0) for k in STATUSES}
    r = runway(db, user.id)
    since = now() - timedelta(days=14)
    failures = [{"kind": "queue", "name": i.url, "error": i.note or "", "project_id": i.project_id, "at": _aware(i.updated_at)}
                for i in db.scalars(select(LinkQueueItem).where(
                    LinkQueueItem.user_id == user.id, LinkQueueItem.status.in_(("skipped", "failed")),
                    LinkQueueItem.updated_at >= since))]
    seen = {f["project_id"] for f in failures if f["project_id"]}  # a failed link's project is listed once
    for p in db.scalars(select(Project).where(Project.user_id == user.id, Project.updated_at >= since)):
        if p.id not in seen and _auto(p).get("auto") and _auto(p).get("processed") and (err := project_error(db, p)):
            failures.append({"kind": "project", "name": p.name, "error": err, "project_id": p.id, "at": _aware(p.updated_at)})
    failures = sorted(failures, key=lambda f: f["at"], reverse=True)[:10]
    for f in failures:
        f["at"] = f["at"].isoformat()
    return {"enabled": s["enabled"], "queue": queue, **r, "buffer_days": s["queue_buffer_days"],
            "add_more_links": not (queue["waiting"] or queue["started"]) and (r["runway_days"] is None or r["runway_days"] < 2),
            "failures": failures}
