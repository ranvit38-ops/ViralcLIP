"""Hands-off pipeline: watch sources → import → analyze → (optional) auto-approve → render → auto-publish.

tick() runs once a minute in the worker. It's stateless and idempotent: every decision is derived
from DB rows (SourceVideo, Job, Clip, project.settings["automation"]), so running it twice, or after
a restart, never double-imports or double-enqueues. Publishing is the existing YouTube AutoPublishRule,
which only ever takes approved + rendered clips.
"""
import logging
import re
import shutil
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, projects
from .auth import current_user
from .db import SessionLocal, get_db
from .models import (AutomationEvent, AutoPublishRule, Clip, Job, Project, SourceVideo, TwitchAccount, User,
                     YouTubeAccount, YouTubeUpload, now)

log = logging.getLogger("viralclip.automation")
router = APIRouter(prefix="/api/automation", tags=["automation"])

POLL_EVERY = 30 * 60
IMPORT_JOBS = ("twitch_import", "youtube_import")
ACTIVE = ("queued", "running", "cancel_requested")
# ponytail: per-process memory; a worker restart just polls once early (dedupe makes that harmless).
_last_poll: dict[tuple[str, int], float] = {}


# ---------- settings ----------

class AnalyzeSettings(BaseModel):
    min_len: float = Field(30, ge=5, le=600)
    max_len: float = Field(60, ge=6, le=600)
    count: int = Field(8, ge=1, le=50)
    model: str | None = None


class AutoApprove(BaseModel):
    enabled: bool = False
    top_n: int = Field(3, ge=1, le=20)
    min_score: float = Field(60, ge=0, le=100)


class AutomationSettings(BaseModel):
    enabled: bool = False
    twitch_account_ids: list[int] = []
    youtube_account_ids: list[int] = []
    # >= 5 so the clips we publish (Shorts) can never be re-imported from a watched YouTube channel.
    min_source_minutes: int = Field(20, ge=5, le=1440)
    analyze: AnalyzeSettings = AnalyzeSettings()
    auto_approve: AutoApprove = AutoApprove()
    auto_render: bool = True
    delete_source_after_days: int | None = Field(None, ge=1, le=365)


def settings_of(user: User) -> dict:
    raw = (user.settings or {}).get("automation") or {}
    s = AutomationSettings(**{k: v for k, v in raw.items() if k in AutomationSettings.model_fields}).model_dump()
    s["enabled_at"] = raw.get("enabled_at")
    return s


def _auto(p: Project) -> dict:
    return (p.settings or {}).get("automation") or {}


def _set_auto(p: Project, **kw):
    p.settings = {**(p.settings or {}), "automation": {**_auto(p), **kw}}  # reassign: JSON columns don't track mutation


def event(db: Session, user_id: int, kind: str, msg: str, project_id: int | None = None):
    db.add(AutomationEvent(user_id=user_id, kind=kind, message=msg[:500], project_id=project_id))


# ---------- shared import entry point (used by Twitch + YouTube imports) ----------

def start_import(db: Session, user_id: int, platform: str, ext_id: str, account_id: int | None, title: str,
                 job_type: str, params: dict, est_bytes: int, automated: bool) -> Project:
    sv = db.scalar(select(SourceVideo).where(SourceVideo.user_id == user_id, SourceVideo.platform == platform,
                                             SourceVideo.external_id == ext_id))
    if sv and sv.project_id and (old := db.get(Project, sv.project_id)):
        raise HTTPException(409, f"Already imported as project \"{old.name}\".")
    if shutil.disk_usage(config.DATA_DIR).free - est_bytes < config.MIN_FREE_BYTES * 2:
        need = f" (needs about {est_bytes / 1024**3:.1f} GB)" if est_bytes else ""
        raise HTTPException(507, f"Not enough free disk space to import this video{need}.")
    p = Project(user_id=user_id, name=(title or ext_id)[:200], status="importing", source_filename=f"{ext_id}.mp4",
                settings={"automation": {"auto": True}} if automated else {})
    db.add(p)
    db.flush()
    db.add(Job(project_id=p.id, job_type=job_type, params=params, message="Waiting for a worker"))
    if sv is None:
        sv = SourceVideo(user_id=user_id, platform=platform, external_id=ext_id)
        db.add(sv)
    sv.account_id, sv.project_id, sv.title, sv.automated = account_id, p.id, (title or "")[:300], automated
    db.commit()
    return p


# ---------- polling sources ----------

def _due(kind: str, aid: int, force: bool) -> bool:
    if not force and _last_poll.get((kind, aid), 0) > time.time() - POLL_EVERY:
        return False
    _last_poll[(kind, aid)] = time.time()
    return True


def _iso(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None


def _import_busy(db: Session, user_id: int) -> bool:
    """One automated download at a time per user: VODs are big, disks are not."""
    return db.scalar(select(Job.id).join(Project).where(Project.user_id == user_id, Job.job_type.in_(IMPORT_JOBS),
                                                        Job.status.in_(ACTIVE)).limit(1)) is not None


def poll_twitch(db: Session, user: User, s: dict, since: datetime, force: bool) -> None:
    from . import twitch
    if _import_busy(db, user.id):
        return  # checked before polling, so a busy tick doesn't use up this account's 30-minute slot
    seen = twitch.imported_map(db, user.id)
    for aid in s["twitch_account_ids"]:
        acc = db.get(TwitchAccount, aid)
        if not acc or acc.user_id != user.id or acc.status != "ok" or not _due("twitch", aid, force):
            continue
        twitch.validate(db, acc)
        live_ids = {x.get("id") for x in twitch.tapi(db, acc, "/streams", {"user_id": acc.twitch_user_id}).get("data", [])}
        for v in sorted(twitch.list_vods(db, acc, "archive"), key=lambda v: v.get("created_at", "")):
            created = _iso(v.get("created_at"))
            if v["id"] in seen or (created and created < since):
                continue
            if v.get("stream_id") in live_ids:
                continue  # still streaming: the VOD is incomplete; pick it up after the stream ends
            if twitch.parse_duration(v.get("duration", "")) < s["min_source_minutes"] * 60:
                continue
            p = twitch.create_import(db, user, v["id"], automated=True)
            event(db, user.id, "import", f"Importing Twitch VOD \"{v.get('title', v['id'])}\"", p.id)
            db.commit()
            _last_poll.pop(("twitch", aid), None)  # more may be waiting: look again once this download is done
            return  # one per tick


def _yt_duration(s: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    return (int(m[1] or 0) * 86400 + int(m[2] or 0) * 3600 + int(m[3] or 0) * 60 + int(m[4] or 0)) if m else 0


def poll_youtube(db: Session, user: User, s: dict, since: datetime, force: bool) -> None:
    from . import youtube
    seen = set(db.scalars(select(SourceVideo.external_id).where(SourceVideo.user_id == user.id, SourceVideo.platform == "youtube")))
    if _import_busy(db, user.id):
        return
    ours = set(db.scalars(select(YouTubeUpload.youtube_video_id).where(YouTubeUpload.user_id == user.id,
                                                                        YouTubeUpload.youtube_video_id.is_not(None))))
    for aid in s["youtube_account_ids"]:
        acc = db.get(YouTubeAccount, aid)
        if not acc or acc.user_id != user.id or acc.status != "ok" or not _due("youtube", aid, force):
            continue
        r = youtube.gapi(db, acc, "GET", f"{youtube.API}/channels", params={"part": "contentDetails", "id": acc.channel_id})
        items = r.json().get("items", []) if r.status_code == 200 else []
        if not items:
            continue
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        r = youtube.gapi(db, acc, "GET", f"{youtube.API}/playlistItems",
                         params={"part": "contentDetails", "playlistId": uploads, "maxResults": 15})
        ids = [i["contentDetails"]["videoId"] for i in (r.json().get("items", []) if r.status_code == 200 else [])
               if (t := _iso(i["contentDetails"].get("videoPublishedAt"))) and t >= since]
        ids = [i for i in ids if i not in seen and i not in ours]
        if not ids:
            continue
        r = youtube.gapi(db, acc, "GET", f"{youtube.API}/videos", params={"part": "snippet,contentDetails", "id": ",".join(ids)})
        for v in (r.json().get("items", []) if r.status_code == 200 else []):
            if v["snippet"].get("liveBroadcastContent", "none") != "none":
                continue
            if _yt_duration(v["contentDetails"].get("duration", "")) < s["min_source_minutes"] * 60:
                continue
            p = youtube.create_import(db, user, v["id"], automated=True)
            event(db, user.id, "import", f"Importing YouTube video \"{v['snippet'].get('title', v['id'])}\"", p.id)
            db.commit()
            _last_poll.pop(("youtube", aid), None)
            return


# ---------- processing automated projects ----------

def _has_job(db: Session, p: Project, job_type: str, statuses=None, clip_id=None) -> Job | None:
    q = select(Job).where(Job.project_id == p.id, Job.job_type == job_type)
    if statuses:
        q = q.where(Job.status.in_(statuses))
    if clip_id:
        q = q.where(Job.clip_id == clip_id)
    return db.scalar(q.order_by(Job.id.desc()).limit(1))


def process_projects(db: Session, user: User, s: dict) -> None:
    auto_projects = [p for p in db.scalars(select(Project).where(Project.user_id == user.id, Project.archived.is_(False)))
                     if _auto(p).get("auto")]
    for p in auto_projects:
        a = _auto(p)
        # a) a failed import, or an analyze that ended without clips: say so once, don't retry forever
        failed = _has_job(db, p, "analyze") if p.status == "uploaded" else None
        if not a.get("processed") and (p.status == "failed" or (failed and failed.status in ("failed", "cancelled"))):
            _set_auto(p, processed=True)
            why = p.error if p.status == "failed" else failed.error or "Analysis was cancelled."
            event(db, user.id, "error", f"\"{p.name}\" stopped: {why}", p.id)
            continue
        # b) uploaded → analyze (once; a failed analyze is shown in the project, not retried forever)
        if p.status == "uploaded" and not a.get("processed") and not _has_job(db, p, "analyze"):
            if not p.has_audio:
                _set_auto(p, processed=True)
                event(db, user.id, "skip", f"\"{p.name}\" has no audio track; nothing to clip.", p.id)
            else:
                db.add(Job(project_id=p.id, job_type="analyze", params=s["analyze"], message="Waiting for a worker"))
                event(db, user.id, "analyze", f"Finding clips in \"{p.name}\"", p.id)
            continue
        # c) analyzed → auto-approve top clips (only if the user turned that on)
        if p.status == "ready" and not a.get("processed") and not _has_job(db, p, "analyze", ACTIVE):
            aa = s["auto_approve"]
            if aa["enabled"]:
                picks = [c for c in sorted(p.clips, key=lambda c: -c.score)
                         if c.review_status == "suggested"][:aa["top_n"]]
                picks = [c for c in picks if c.score >= aa["min_score"]]
                for c in picks:
                    c.review_status = "approved"
                event(db, user.id, "approve", f"Auto-approved {len(picks)} clip(s) in \"{p.name}\" "
                                              f"(top {aa['top_n']}, score ≥ {aa['min_score']:g})", p.id)
            else:
                n = sum(c.review_status == "suggested" for c in p.clips)
                event(db, user.id, "review", f"{n} clip(s) from \"{p.name}\" are waiting for your review.", p.id)
            _set_auto(p, processed=True)
        # c2) approved (by automation or by you) + not rendered → render
        if s["auto_render"] and p.status == "ready" and not a.get("source_deleted"):
            todo = [c for c in p.clips if c.review_status == "approved" and c.render_status == "none"
                    and not _has_job(db, p, "render", ACTIVE, c.id)]
            for c in todo:
                db.add(Job(project_id=p.id, clip_id=c.id, job_type="render", message="Waiting for a worker"))
                c.render_status = "queued"
            if todo:
                event(db, user.id, "render", f"Rendering {len(todo)} clip(s) from \"{p.name}\"", p.id)
        # d) free disk: delete the long source once its approved clips are rendered (rendered clips stay)
        days = s["delete_source_after_days"]
        if days and a.get("processed") and not a.get("source_deleted") and p.status == "ready":
            created = p.created_at.replace(tzinfo=timezone.utc) if p.created_at.tzinfo is None else p.created_at
            pending = any(c.review_status == "approved" and c.render_status != "done" for c in p.clips)
            if created < now() - timedelta(days=days) and not pending and not _has_job(db, p, "render", ACTIVE) \
                    and not _has_job(db, p, "analyze", ACTIVE):
                projects.source_path(p).unlink(missing_ok=True)
                _set_auto(p, source_deleted=True)
                p.error = "The source video was deleted by automation to free disk space. Rendered clips are kept."
                event(db, user.id, "cleanup", f"Deleted the source video of \"{p.name}\" to free disk space.", p.id)
    db.commit()


def run_for_user(db: Session, user: User, force_poll: bool = False) -> None:
    from .youtube import AuthRequired, Transient
    s = settings_of(user)
    if not s["enabled"]:
        return
    # VODs from up to a day before switching on still count: a stream that's live (or just ended) when you
    # enable automation started before enabled_at. Older back-catalog is never auto-imported.
    since = (_iso(s["enabled_at"]) or now()) - timedelta(days=1)
    for poll in (poll_twitch, poll_youtube):
        try:
            poll(db, user, s, since, force_poll)
        except (AuthRequired, Transient) as e:
            db.rollback()
            event(db, user.id, "error", f"Couldn't check for new videos: {'reconnect the account' if isinstance(e, AuthRequired) else e}")
            db.commit()
        except HTTPException as e:  # e.g. disk full: surface it, try again next tick
            db.rollback()
            event(db, user.id, "error", f"Import skipped: {e.detail}")
            db.commit()
    process_projects(db, user, s)


def tick() -> int:
    """Returns how many activity-log entries this tick wrote (for the worker's heartbeat log line)."""
    with SessionLocal() as db:
        before = db.scalar(select(func.max(AutomationEvent.id))) or 0
        for user in db.scalars(select(User)):
            try:
                run_for_user(db, user)
            except Exception:
                db.rollback()
                log.exception("automation failed for user %s", user.id)
        return db.scalar(select(func.count(AutomationEvent.id)).where(AutomationEvent.id > before)) or 0


# ---------- API ----------

@router.get("")
def get_automation(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from . import twitch
    rules = {r.account_id: r.enabled for r in db.scalars(select(AutoPublishRule).where(AutoPublishRule.user_id == user.id))}
    auto_pids = [p.id for p in db.scalars(select(Project).where(Project.user_id == user.id)) if _auto(p).get("auto")]
    waiting = db.scalar(select(func.count()).select_from(Clip).where(
        Clip.project_id.in_(auto_pids or [0]), Clip.review_status == "suggested")) or 0
    return {
        "settings": settings_of(user),
        "twitch_configured": not twitch.missing_config(), "twitch_missing": twitch.missing_config(),
        "twitch_accounts": [twitch.account_out(a) for a in db.scalars(select(TwitchAccount).where(TwitchAccount.user_id == user.id))],
        "youtube_accounts": [{"id": a.id, "channel_title": a.channel_title, "status": a.status,
                              "autopublish_enabled": rules.get(a.id, False)}
                             for a in db.scalars(select(YouTubeAccount).where(YouTubeAccount.user_id == user.id))],
        "waiting_review": waiting,
        "events": [{"id": e.id, "kind": e.kind, "message": e.message, "project_id": e.project_id, "created_at": e.created_at}
                   for e in db.scalars(select(AutomationEvent).where(AutomationEvent.user_id == user.id)
                                       .order_by(AutomationEvent.id.desc()).limit(50))],
    }


@router.put("")
def put_automation(body: AutomationSettings, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.analyze.min_len >= body.analyze.max_len:
        raise HTTPException(422, "Minimum clip length must be shorter than maximum.")
    from .pipeline import WHISPER_MODELS
    if body.analyze.model and body.analyze.model not in WHISPER_MODELS:
        raise HTTPException(422, "Unknown transcription model.")
    for model, ids, label in ((TwitchAccount, body.twitch_account_ids, "Twitch account"),
                              (YouTubeAccount, body.youtube_account_ids, "YouTube channel")):
        for i in ids:
            a = db.get(model, i)
            if not a or a.user_id != user.id:
                raise HTTPException(404, f"{label} not found")
    prev = settings_of(user)
    new = body.model_dump()
    # Only videos published after automation is switched on are imported: no surprise back-catalog downloads.
    new["enabled_at"] = (prev["enabled_at"] if prev["enabled"] and body.enabled else
                         now().isoformat() if body.enabled else None)
    user.settings = {**(user.settings or {}), "automation": new}
    if body.enabled != prev["enabled"]:
        event(db, user.id, "settings", "Automation turned on." if body.enabled else "Automation turned off.")
    db.commit()
    return settings_of(user)


@router.post("/run")
def run_now(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """'Check now': poll sources immediately (ignores the 30-minute interval) and advance projects."""
    if not settings_of(user)["enabled"]:
        raise HTTPException(409, "Turn automation on first.")
    from .worker import tick_lock
    with tick_lock(wait=20) as mine:  # never alongside a worker's tick: both would enqueue the same jobs
        if not mine:
            raise HTTPException(409, "Automation is busy right now. Try again in a minute.")
        run_for_user(db, user, force_poll=True)
    return get_automation(user, db)
