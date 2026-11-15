"""YouTube integration: OAuth (multiple channels), resumable uploads, scheduling, auto-publish,
and link import restricted to videos on the user's own connected channels.

Talks to Google's REST endpoints with httpx. Tests swap `http` for a mock transport.
Upload state (upload_status) and schedule state (schedule_status) are tracked separately:
a video can upload fine and still have its schedule rejected.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import config, media, projects
from .auth import current_user
from .db import SessionLocal, get_db
from .models import (AutoPublishRule, Clip, Job, OAuthState, Project, User, YouTubeAccount,
                     YouTubeUpload, now)
from .worker import Cancelled, UserError

log = logging.getLogger("viralclip.youtube")
router = APIRouter(prefix="/api/youtube", tags=["youtube"])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
# Minimum scopes: upload + read (channel name, ownership check, publish status).
# Editing an already-uploaded video's schedule would need the broader `youtube` scope; we don't ask for it.
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
PACIFIC = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight Pacific
CHUNK = 8 * 1024 * 1024  # must be a multiple of 256 KiB
MAX_TRANSIENT_FAILURES = 5
ACTIVE = ("queued", "uploading", "paused", "quota_exceeded", "auth_required")  # not finished, not given up
PRIVACY = ("private", "unlisted", "public")

http = httpx.Client(timeout=httpx.Timeout(60, connect=15))


class AuthRequired(Exception):
    pass


class Transient(Exception):
    pass


class QuotaExceeded(Exception):
    pass


# ---------- config / crypto ----------

def missing_config() -> list[str]:
    return [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI") if not getattr(config, k)]


def _fernet() -> Fernet:
    key = config.TOKEN_ENCRYPTION_KEY
    if not key:
        # ponytail: local default keeps the key in a 0600 file next to the DB. That protects against
        # DB-only leaks, not disk theft. Set TOKEN_ENCRYPTION_KEY from a secret manager when hosting.
        f = config.DATA_DIR / ".token_key"
        if not f.exists():
            f.write_bytes(Fernet.generate_key())
            os.chmod(f, 0o600)
        key = f.read_text().strip()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_tokens(d: dict) -> str:
    return _fernet().encrypt(json.dumps(d).encode()).decode()


def decrypt_tokens(blob: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken:
        raise AuthRequired()  # key changed: tokens unreadable, user must reconnect


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# ---------- Google API helpers ----------

def _google_error(r: httpx.Response) -> tuple[str, str]:
    """(reason, message) from a Google JSON error body."""
    try:
        e = r.json().get("error", {})
        if isinstance(e, str):  # OAuth token endpoint shape
            return e, r.json().get("error_description", e)
        errs = e.get("errors") or [{}]
        return errs[0].get("reason", ""), e.get("message", "") or r.text[:200]
    except ValueError:
        return "", r.text[:200]


def _refresh(db: Session, acc: YouTubeAccount) -> str:
    tok = decrypt_tokens(acc.encrypted_tokens)
    if not tok.get("refresh_token"):
        acc.status = "reauth_required"
        db.commit()
        raise AuthRequired()
    r = http.post(TOKEN_URL, data={"client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
                                   "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if r.status_code in (400, 401):  # invalid_grant: revoked, expired, or password changed
        acc.status = "reauth_required"
        db.commit()
        raise AuthRequired()
    if r.status_code >= 300:
        raise Transient(f"Google token refresh failed ({r.status_code})")
    d = r.json()
    tok["access_token"] = d["access_token"]
    if d.get("refresh_token"):
        tok["refresh_token"] = d["refresh_token"]
    acc.encrypted_tokens = encrypt_tokens(tok)
    acc.token_expiry = now() + timedelta(seconds=int(d.get("expires_in", 3600)))
    acc.status = "ok"
    db.commit()
    return tok["access_token"]


def access_token(db: Session, acc: YouTubeAccount, force_refresh=False) -> str:
    if acc.status == "reauth_required":
        raise AuthRequired()
    exp = _aware(acc.token_expiry)
    if not force_refresh and exp and exp > now() + timedelta(seconds=60):
        return decrypt_tokens(acc.encrypted_tokens)["access_token"]
    return _refresh(db, acc)


def gapi(db: Session, acc: YouTubeAccount, method: str, url: str, **kw) -> httpx.Response:
    """Authorized request. A 401 triggers one forced refresh; a second 401 means reconnect."""
    for attempt in (0, 1):
        tok = access_token(db, acc, force_refresh=attempt == 1)
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {tok}"}
        try:
            r = http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise Transient(f"Network error talking to YouTube: {type(e).__name__}")
        kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
        if r.status_code != 401:
            return r
    acc.status = "reauth_required"
    db.commit()
    raise AuthRequired()


# ---------- small shared helpers ----------

def owned_account(db: Session, user: User, aid: int) -> YouTubeAccount:
    a = db.get(YouTubeAccount, aid)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "Channel not found")
    return a


def owned_upload(db: Session, user: User, uid: int) -> YouTubeUpload:
    u = db.get(YouTubeUpload, uid)
    if not u or u.user_id != user.id:
        raise HTTPException(404, "Upload not found")
    return u


def pacific_day(at: datetime | None = None) -> str:
    return (at or now()).astimezone(PACIFIC).date().isoformat()


def next_quota_reset() -> datetime:
    tomorrow = now().astimezone(PACIFIC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 5), PACIFIC).astimezone(timezone.utc)


def uploads_today(db: Session, account_id: int) -> int:
    return db.scalar(select(func.count()).select_from(YouTubeUpload).where(
        YouTubeUpload.account_id == account_id, YouTubeUpload.quota_day == pacific_day()))


def to_utc(local: str, tz: str) -> datetime:
    """'2026-11-20T10:00' wall-clock in zone `tz` -> aware UTC. No silent conversion: the zone is explicit."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"Unknown time zone '{tz}'")
    try:
        dt = datetime.fromisoformat(local)
    except ValueError:
        raise HTTPException(422, "Invalid date/time")
    if dt.tzinfo is not None:
        raise HTTPException(422, "Send a local date/time without an offset, plus a time zone")
    return dt.replace(tzinfo=zone).astimezone(timezone.utc)


def clip_file(clip: Clip):
    return projects.project_dir(clip.project_id) / clip.render_path if clip.render_path else None


def check_clip_uploadable(db: Session, user: User, cid: int) -> Clip:
    c = db.get(Clip, cid)
    if not c or c.project.user_id != user.id:
        raise HTTPException(404, f"Clip {cid} not found")
    if c.review_status != "approved":
        raise HTTPException(409, f"Clip {cid} isn't approved. Approve it before uploading.")
    f = clip_file(c)
    if c.render_status != "done" or not f or not f.exists():
        raise HTTPException(409, f"Clip {cid} hasn't been rendered yet.")
    return c


def video_metadata(c: Clip) -> tuple[str, str, list[str]]:
    """Title/description/tags as YouTube accepts them. Raises UserError for invalid metadata."""
    title = re.sub(r"[<>]", "", (c.title or "").strip())
    if not title:
        raise UserError("The clip has no title. Add one before uploading.")
    if len(title) > 100:
        raise UserError("YouTube titles are limited to 100 characters.")
    tags = [re.sub(r"[^\w]", "", h.lstrip("#")) for h in (c.hashtags or [])]
    tags = [t for t in tags if t][:15]
    desc = re.sub(r"[<>]", "", (c.description or "").strip())
    if tags:
        desc = (desc + "\n\n" if desc else "") + " ".join("#" + t for t in tags)
    if len(desc.encode()) > 5000:
        raise UserError("The description is longer than YouTube's 5000-byte limit.")
    return title, desc, tags


# ---------- OAuth ----------

@router.get("/config")
def youtube_config(user: User = Depends(current_user)):
    return {"configured": not missing_config(), "missing": missing_config(),
            "redirect_uri": config.GOOGLE_REDIRECT_URI, "daily_limit": config.YOUTUBE_DAILY_UPLOAD_LIMIT}


@router.get("/connect")
def connect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if missing_config():
        return RedirectResponse("/youtube?error=" + _q("YouTube isn't configured on this server yet. See the setup steps below."), 303)
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(user_id=user.id, state_hash=hashlib.sha256(state.encode()).hexdigest(),
                      expires_at=now() + timedelta(minutes=10)))
    db.commit()
    params = {"client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": config.GOOGLE_REDIRECT_URI,
              "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent select_account",
              "include_granted_scopes": "true", "state": state}
    return RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", 303)


def _q(msg: str) -> str:
    return urlencode({"": msg})[1:]


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             vc_session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    try:
        user = current_user(vc_session, db)
    except HTTPException:
        return RedirectResponse("/login?next=/youtube", 303)
    if not state:
        return RedirectResponse("/youtube?error=" + _q("Missing OAuth state. Start the connection again."), 303)
    st = db.scalar(select(OAuthState).where(OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    valid = st is not None and st.user_id == user.id and _aware(st.expires_at) > now()
    if st is not None:
        db.delete(st)  # single use, even when invalid
        db.commit()
    if not valid:
        return RedirectResponse("/youtube?error=" + _q("The connection request expired or was invalid. Try again."), 303)
    if error or not code:
        return RedirectResponse("/youtube?error=" + _q("Google sign-in was cancelled or denied."), 303)

    r = http.post(TOKEN_URL, data={"code": code, "client_id": config.GOOGLE_CLIENT_ID,
                                   "client_secret": config.GOOGLE_CLIENT_SECRET,
                                   "redirect_uri": config.GOOGLE_REDIRECT_URI, "grant_type": "authorization_code"})
    if r.status_code != 200:
        log.warning("OAuth code exchange failed: %s", _google_error(r)[0])
        return RedirectResponse("/youtube?error=" + _q("Google rejected the sign-in. Try again."), 303)
    tok = r.json()
    granted = set(tok.get("scope", SCOPES).split())
    if "https://www.googleapis.com/auth/youtube.upload" not in granted:
        return RedirectResponse("/youtube?error=" + _q("Upload permission wasn't granted, so ViralClip can't upload for you."), 303)
    ch = http.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                  headers={"Authorization": f"Bearer {tok['access_token']}"})
    items = ch.json().get("items", []) if ch.status_code == 200 else []
    if not items:
        return RedirectResponse("/youtube?error=" + _q("That Google account has no YouTube channel. Create one first, or pick a different account."), 303)
    for it in items:
        acc = db.scalar(select(YouTubeAccount).where(YouTubeAccount.user_id == user.id, YouTubeAccount.channel_id == it["id"]))
        stored = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token")}
        if acc is None:
            acc = YouTubeAccount(user_id=user.id, channel_id=it["id"])
            db.add(acc)
        elif not stored["refresh_token"]:
            stored["refresh_token"] = decrypt_tokens(acc.encrypted_tokens).get("refresh_token")
        sn = it.get("snippet", {})
        acc.channel_title = sn.get("title", "")[:200]
        acc.thumbnail_url = (sn.get("thumbnails", {}).get("default") or {}).get("url")
        acc.encrypted_tokens = encrypt_tokens(stored)
        acc.token_expiry = now() + timedelta(seconds=int(tok.get("expires_in", 3600)))
        acc.status = "ok"
    db.commit()
    return RedirectResponse(f"/youtube?connected={len(items)}", 303)


def account_out(db: Session, a: YouTubeAccount) -> dict:
    rule = db.scalar(select(AutoPublishRule).where(AutoPublishRule.account_id == a.id))
    return {"id": a.id, "channel_id": a.channel_id, "channel_title": a.channel_title, "thumbnail_url": a.thumbnail_url,
            "status": a.status, "created_at": a.created_at, "uploads_today": uploads_today(db, a.id),
            "daily_limit": config.YOUTUBE_DAILY_UPLOAD_LIMIT, "rule": rule_out(rule) if rule else None}


@router.get("/accounts")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [account_out(db, a) for a in db.scalars(select(YouTubeAccount).where(YouTubeAccount.user_id == user.id)
                                                   .order_by(YouTubeAccount.created_at))]


@router.delete("/accounts/{aid}", status_code=204)
def disconnect(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_account(db, user, aid)
    try:
        tok = decrypt_tokens(a.encrypted_tokens)
        http.post(REVOKE_URL, data={"token": tok.get("refresh_token") or tok.get("access_token")})
    except (AuthRequired, httpx.HTTPError):
        pass  # best effort; the row (and our copy of the tokens) is deleted regardless
    for u in db.scalars(select(YouTubeUpload).where(YouTubeUpload.account_id == a.id, YouTubeUpload.upload_status.in_(ACTIVE))):
        _stop(db, u, "cancelled", "Channel disconnected")
    db.delete(a)
    db.commit()


# ---------- uploads ----------

class UploadRequest(BaseModel):
    clip_ids: list[int] = Field(min_length=1, max_length=200)
    account_id: int
    privacy: str = "private"
    category_id: str = Field(default="22", pattern=r"^\d{1,3}$")
    publish_at: str | None = None  # local wall-clock 'YYYY-MM-DDTHH:MM'
    timezone: str | None = None
    force: bool = False


def upload_out(u: YouTubeUpload, db: Session) -> dict:
    c = db.get(Clip, u.clip_id)
    a = db.get(YouTubeAccount, u.account_id) if u.account_id else None
    job = db.get(Job, u.job_id) if u.job_id else None
    return {
        "id": u.id, "clip_id": u.clip_id, "project_id": c.project_id if c else None,
        "clip_title": c.title if c else "", "clip_thumbnail_url": f"/api/clips/{u.clip_id}/thumbnail" if c and c.thumbnail_path else None,
        "account_id": u.account_id, "channel_title": a.channel_title if a else "(disconnected)",
        "upload_status": u.upload_status, "privacy": u.privacy, "schedule_status": u.schedule_status,
        "publish_at": u.publish_at, "timezone": u.timezone, "error": u.error,
        "progress": (u.bytes_sent / u.total_bytes) if u.total_bytes else 0,
        "run_after": job.run_after if job and job.status == "queued" else None,
        "youtube_video_id": u.youtube_video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={u.youtube_video_id}" if u.youtube_video_id else None,
        "created_at": u.created_at, "uploaded_at": u.uploaded_at,
    }


def _enqueue(db: Session, u: YouTubeUpload, run_after: datetime | None = None):
    c = db.get(Clip, u.clip_id)
    j = Job(project_id=c.project_id, clip_id=c.id, job_type="youtube_upload", params={"upload_id": u.id}, run_after=run_after)
    db.add(j)
    db.flush()
    u.job_id = j.id


def _active_job(db: Session, u: YouTubeUpload) -> Job | None:
    j = db.get(Job, u.job_id) if u.job_id else None
    return j if j and j.status in ("queued", "running", "cancel_requested") else None


def _stop(db: Session, u: YouTubeUpload, status: str, error: str | None = None):
    j = _active_job(db, u)
    if j:
        j.status = "cancelled" if j.status == "queued" else "cancel_requested"
        if j.status == "cancelled":
            j.finished_at = now()
    u.upload_status = status
    if error:
        u.error = error


def create_uploads(db: Session, user: User, clip_ids: list[int], acc: YouTubeAccount, privacy: str,
                   category_id: str, slots: list[tuple[datetime | None, str | None]], force: bool) -> list[YouTubeUpload]:
    if privacy not in PRIVACY:
        raise HTTPException(422, "privacy must be private, unlisted or public")
    if acc.status != "ok":
        raise HTTPException(409, f"Reconnect {acc.channel_title or 'this channel'} first; its authorization expired.")
    if len(set(clip_ids)) != len(clip_ids):
        raise HTTPException(422, "The same clip was selected twice")
    clips = [check_clip_uploadable(db, user, cid) for cid in clip_ids]
    for c in clips:
        try:
            video_metadata(c)
        except UserError as e:
            raise HTTPException(422, f"Clip {c.id}: {e}")
        dup = db.scalar(select(YouTubeUpload).where(
            YouTubeUpload.clip_id == c.id, YouTubeUpload.account_id == acc.id,
            YouTubeUpload.upload_status.in_(ACTIVE + ("uploaded",))))
        if dup and not force:
            raise HTTPException(409, detail={"message": f"Clip {c.id} is already {'uploaded' if dup.upload_status == 'uploaded' else 'queued'} "
                                             f"for this channel. Confirm to upload it again.", "duplicate_clip_id": c.id})
    for at, _ in slots:
        if at is not None and at <= now() + timedelta(minutes=5):
            raise HTTPException(422, "Scheduled times must be at least 5 minutes in the future.")
    out = []
    for c, (at, tz) in zip(clips, slots):
        u = YouTubeUpload(user_id=user.id, clip_id=c.id, account_id=acc.id, category_id=category_id,
                          privacy="private" if at else privacy,  # YouTube requires private + publishAt for scheduling
                          publish_at=at, timezone=tz)
        db.add(u)
        db.flush()
        _enqueue(db, u)
        out.append(u)
    db.commit()
    return out


@router.post("/uploads", status_code=201)
def post_uploads(body: UploadRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, body.account_id)
    if body.publish_at and not body.timezone:
        raise HTTPException(422, "Pick a time zone for the scheduled time")
    at = to_utc(body.publish_at, body.timezone) if body.publish_at else None
    us = create_uploads(db, user, body.clip_ids, acc, body.privacy, body.category_id,
                        [(at, body.timezone if at else None)] * len(body.clip_ids), body.force)
    return [upload_out(u, db) for u in us]


class ScheduleRequest(BaseModel):
    clip_ids: list[int] = Field(min_length=1, max_length=200)
    account_id: int
    start: str  # local 'YYYY-MM-DDTHH:MM'
    timezone: str
    interval: str = "daily"  # daily | every_other_day | weekly | custom_hours
    custom_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    category_id: str = Field(default="22", pattern=r"^\d{1,3}$")
    force: bool = False


def schedule_slots(start: str, tz: str, interval: str, n: int, custom_hours: int | None = None) -> list[datetime]:
    """Slots in UTC. Day-based intervals step the local wall clock, so 10:00 stays 10:00 across DST."""
    first = to_utc(start, tz)
    local = datetime.fromisoformat(start)
    days = {"daily": 1, "every_other_day": 2, "weekly": 7}.get(interval)
    if days:
        zone = ZoneInfo(tz)
        return [(local + timedelta(days=days * i)).replace(tzinfo=zone).astimezone(timezone.utc) for i in range(n)]
    if interval == "custom_hours" and custom_hours:
        return [first + timedelta(hours=custom_hours * i) for i in range(n)]
    raise HTTPException(422, "interval must be daily, every_other_day, weekly or custom_hours (with custom_hours)")


@router.post("/schedule/preview")
def schedule_preview(body: ScheduleRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, body.account_id)
    slots = schedule_slots(body.start, body.timezone, body.interval, len(body.clip_ids), body.custom_hours)
    clips = [check_clip_uploadable(db, user, cid) for cid in body.clip_ids]
    warnings = []
    if slots[0] <= now() + timedelta(minutes=5):
        warnings.append("The first slot is in the past or less than 5 minutes away. Pick a later start.")
    per_day: dict[str, int] = {}
    for s in slots:
        per_day[pacific_day(s)] = per_day.get(pacific_day(s), 0) + 1
    if max(per_day.values()) > config.YOUTUBE_DAILY_UPLOAD_LIMIT:
        warnings.append(f"More than {config.YOUTUBE_DAILY_UPLOAD_LIMIT} clips land on one day. Uploads beyond the daily "
                        "limit wait for the next quota reset, which may be after their scheduled time.")
    for c in clips:
        if db.scalar(select(YouTubeUpload.id).where(YouTubeUpload.clip_id == c.id, YouTubeUpload.account_id == acc.id,
                                                    YouTubeUpload.upload_status.in_(ACTIVE + ("uploaded",)))):
            warnings.append(f"\"{c.title or c.id}\" is already uploaded or queued for {acc.channel_title}.")
    zone = ZoneInfo(body.timezone)
    return {"timezone": body.timezone, "channel_title": acc.channel_title, "warnings": warnings, "slots": [
        {"clip_id": c.id, "title": c.title, "publish_at_utc": s, "publish_at_local": s.astimezone(zone).strftime("%a %d %b %Y, %H:%M")}
        for c, s in zip(clips, slots)]}


@router.post("/schedule/confirm", status_code=201)
def schedule_confirm(body: ScheduleRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, body.account_id)
    slots = schedule_slots(body.start, body.timezone, body.interval, len(body.clip_ids), body.custom_hours)
    us = create_uploads(db, user, body.clip_ids, acc, "private", body.category_id,
                        [(s, body.timezone) for s in slots], body.force)
    return [upload_out(u, db) for u in us]


@router.get("/uploads")
def list_uploads(status: str | None = None, account_id: int | None = None, start: str | None = None, end: str | None = None,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(YouTubeUpload).where(YouTubeUpload.user_id == user.id)
    if status:
        q = q.where(YouTubeUpload.upload_status.in_(status.split(",")))
    if account_id:
        q = q.where(YouTubeUpload.account_id == account_id)
    when = func.coalesce(YouTubeUpload.publish_at, YouTubeUpload.uploaded_at, YouTubeUpload.created_at)
    if start:
        q = q.where(when >= _parse_utc(start))
    if end:
        q = q.where(when < _parse_utc(end))
    return [upload_out(u, db) for u in db.scalars(q.order_by(YouTubeUpload.created_at.desc()).limit(500))]


def _parse_utc(v: str) -> datetime:
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "Invalid date")
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.post("/uploads/{uid}/pause")
def pause_upload(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    u = owned_upload(db, user, uid)
    if u.upload_status not in ("queued", "uploading", "quota_exceeded"):
        raise HTTPException(409, f"Can't pause an upload that is {u.upload_status}")
    _stop(db, u, "paused")
    db.commit()
    return upload_out(u, db)


@router.post("/uploads/{uid}/cancel")
def cancel_upload(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    u = owned_upload(db, user, uid)
    if u.upload_status == "uploaded":
        raise HTTPException(409, "This video is already on YouTube. Delete or unschedule it in YouTube Studio.")
    if u.upload_status not in ACTIVE:
        raise HTTPException(409, f"Can't cancel an upload that is {u.upload_status}")
    _stop(db, u, "cancelled")
    u.session_uri = None  # an abandoned resumable session expires on its own; nothing was published
    db.commit()
    return upload_out(u, db)


@router.post("/uploads/{uid}/retry")
def retry_upload(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Also used for resume. A persisted session continues where it stopped, so no duplicate video."""
    u = owned_upload(db, user, uid)
    if u.upload_status not in ("failed", "cancelled", "paused", "auth_required", "quota_exceeded"):
        raise HTTPException(409, f"Can't retry an upload that is {u.upload_status}")
    if _active_job(db, u):
        raise HTTPException(409, "This upload is already queued")
    acc = db.get(YouTubeAccount, u.account_id) if u.account_id else None
    if not acc:
        raise HTTPException(409, "The channel for this upload was disconnected. Upload the clip again to another channel.")
    if acc.status != "ok":
        raise HTTPException(409, f"Reconnect {acc.channel_title} first.")
    if u.publish_at and _aware(u.publish_at) <= now() + timedelta(minutes=5) and not u.youtube_video_id:
        raise HTTPException(409, "The scheduled time has passed. Change the time first.")
    u.upload_status, u.error, u.transient_failures = "queued", None, 0
    _enqueue(db, u)
    db.commit()
    return upload_out(u, db)


class ScheduleEdit(BaseModel):
    publish_at: str
    timezone: str


@router.patch("/uploads/{uid}")
def edit_schedule(uid: int, body: ScheduleEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    u = owned_upload(db, user, uid)
    if u.youtube_video_id:
        # ponytail: changing publishAt after upload needs the broader `youtube` scope, which we don't request.
        raise HTTPException(409, "This video is already on YouTube. Change its schedule in YouTube Studio.")
    if u.upload_status not in ACTIVE + ("failed", "cancelled"):
        raise HTTPException(409, f"Can't reschedule an upload that is {u.upload_status}")
    at = to_utc(body.publish_at, body.timezone)
    if at <= now() + timedelta(minutes=5):
        raise HTTPException(422, "Scheduled times must be at least 5 minutes in the future.")
    u.publish_at, u.timezone, u.privacy = at, body.timezone, "private"
    db.commit()
    return upload_out(u, db)


@router.delete("/uploads/{uid}", status_code=204)
def remove_upload(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Removes the item from the queue. An already-uploaded video stays on YouTube."""
    u = owned_upload(db, user, uid)
    j = _active_job(db, u)
    if j and j.status == "running":
        raise HTTPException(409, "Pause or cancel the upload first")
    if j:
        j.status, j.finished_at = "cancelled", now()
    db.delete(u)
    db.commit()


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    def n(*conds):
        return db.scalar(select(func.count()).select_from(YouTubeUpload).where(YouTubeUpload.user_id == user.id, *conds))
    recent = db.scalars(select(YouTubeUpload).where(YouTubeUpload.user_id == user.id, YouTubeUpload.schedule_status == "published")
                        .order_by(func.coalesce(YouTubeUpload.publish_at, YouTubeUpload.uploaded_at).desc()).limit(5))
    return {
        "channels": db.scalar(select(func.count()).select_from(YouTubeAccount).where(YouTubeAccount.user_id == user.id)),
        "queued": n(YouTubeUpload.upload_status.in_(("queued", "paused", "quota_exceeded"))),
        "uploading": n(YouTubeUpload.upload_status == "uploading"),
        "scheduled": n(YouTubeUpload.schedule_status == "scheduled"),
        "published": n(YouTubeUpload.schedule_status == "published"),
        "failed": n(YouTubeUpload.upload_status.in_(("failed", "auth_required"))),
        "recent_published": [upload_out(u, db) for u in recent],
    }


# ---------- upload job ----------

def _requeue(db: Session, job: Job, u: YouTubeUpload, status: str, when: datetime, msg: str):
    u.upload_status, u.error = status, msg
    job.status, job.run_after, job.message = "queued", when, msg[:300]
    db.commit()


def run_upload(job_id: int, ctx) -> None:
    db = SessionLocal()
    try:
        _run_upload(db, job_id, ctx)
    finally:
        db.close()


def _run_upload(db: Session, job_id: int, ctx) -> None:
    job = db.get(Job, job_id)
    u = db.get(YouTubeUpload, job.params.get("upload_id"))
    if u is None or u.upload_status in ("paused", "cancelled", "uploaded"):
        raise Cancelled()
    acc = db.get(YouTubeAccount, u.account_id) if u.account_id else None
    if acc is None:
        u.upload_status, u.error = "failed", "The channel was disconnected."
        db.commit()
        raise UserError(u.error)
    clip = db.get(Clip, u.clip_id)
    f = clip_file(clip)
    if clip.review_status != "approved" or not f or not f.exists():
        u.upload_status, u.error = "failed", "The clip is no longer approved or its rendered file is missing."
        db.commit()
        raise UserError(u.error)
    try:
        title, desc, tags = video_metadata(clip)
    except UserError as e:
        u.upload_status, u.error = "failed", str(e)
        db.commit()
        raise
    size = f.stat().st_size
    try:
        if u.session_uri and u.total_bytes != size:
            u.session_uri = None  # file was re-rendered; the old session can't be resumed
        offset = _resume_offset(db, acc, u, size) if u.session_uri else None
        if offset == FINISHED:
            return
        if offset is None:  # need a new session: this is the step that costs quota
            if uploads_today(db, acc.id) >= config.YOUTUBE_DAILY_UPLOAD_LIMIT and u.quota_day != pacific_day():
                when = next_quota_reset()
                _requeue(db, job, u, "quota_exceeded", when,
                         f"Daily limit of {config.YOUTUBE_DAILY_UPLOAD_LIMIT} uploads for this channel reached; waiting for the quota reset.")
                return
            publish = None
            if u.publish_at:
                if _aware(u.publish_at) <= now() + timedelta(minutes=2):
                    # The slot passed while waiting (quota, outage): publish an hour after upload instead of never.
                    u.publish_at = now() + timedelta(hours=1)
                    ctx.progress(0, "Scheduled time passed while waiting; publishing 1 hour after upload")
                publish = _aware(u.publish_at).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            status = {"privacyStatus": u.privacy, "selfDeclaredMadeForKids": False}
            if publish:
                status["publishAt"] = publish
            r = gapi(db, acc, "POST", UPLOAD_URL, json={
                "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": u.category_id},
                "status": status}, headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
            _raise_for(r)
            u.session_uri, u.total_bytes, u.bytes_sent, u.quota_day = r.headers["location"], size, 0, pacific_day()
            offset = 0
        u.upload_status = "uploading"
        u.error = None if u.schedule_status != "failed" else u.error
        db.commit()
        ctx.progress(offset / size, "Uploading to YouTube")
        result = None
        with open(f, "rb") as fh:
            while result is None:
                fh.seek(offset)
                data = fh.read(CHUNK)
                end = offset + len(data) - 1
                r = gapi(db, acc, "PUT", u.session_uri, content=data,
                         headers={"Content-Range": f"bytes {offset}-{end}/{size}" if data else f"bytes */{size}"})
                if r.status_code in (200, 201):
                    result = r.json()
                elif r.status_code == 308:
                    offset = _range_next(r)
                    db.execute(update(YouTubeUpload).where(YouTubeUpload.id == u.id).values(bytes_sent=offset))
                    db.commit()
                    ctx.progress(offset / size, f"Uploading to YouTube ({offset * 100 // size}%)")
                elif r.status_code in (404, 410):
                    u.session_uri = None
                    db.commit()
                    raise Transient("The upload session expired; starting a new one.")
                else:
                    _raise_for(r)
        _finish(db, u, result, size)
    except QuotaExceeded:
        db.rollback()
        _requeue(db, job, u, "quota_exceeded", next_quota_reset(), "YouTube API quota exceeded; waiting for the quota reset.")
    except AuthRequired:
        db.rollback()
        u.upload_status, u.error = "auth_required", "YouTube authorization expired. Reconnect the channel, then retry."
        db.commit()
        raise UserError(u.error)
    except Transient as e:
        db.rollback()
        u.transient_failures += 1
        if u.transient_failures > MAX_TRANSIENT_FAILURES:
            u.upload_status, u.error = "failed", f"{e} Gave up after {MAX_TRANSIENT_FAILURES} retries; retry manually."
            db.commit()
            raise UserError(u.error)
        _requeue(db, job, u, "queued", now() + timedelta(seconds=30 * 2 ** u.transient_failures), f"{e} Retrying shortly.")
    except Cancelled:
        raise  # pause/cancel: the endpoint already set upload_status
    except Exception as e:
        db.rollback()
        u.upload_status = "failed"
        u.error = str(e) if isinstance(e, UserError) else "Unexpected error during upload. See worker logs."
        db.commit()
        raise


FINISHED = -1


def _range_next(r: httpx.Response) -> int:
    rng = r.headers.get("range")  # 'bytes=0-12345'
    return int(rng.rsplit("-", 1)[1]) + 1 if rng else 0


def _resume_offset(db: Session, acc: YouTubeAccount, u: YouTubeUpload, size: int) -> int | None:
    """Ask Google how much of the persisted session it has. None = session gone, start a new one."""
    r = gapi(db, acc, "PUT", u.session_uri, content=b"", headers={"Content-Range": f"bytes */{size}"})
    if r.status_code == 308:
        return _range_next(r)
    if r.status_code in (200, 201):  # it actually finished before we crashed: record it, never re-upload
        _finish(db, u, r.json(), size)
        return FINISHED
    if r.status_code in (404, 410):
        u.session_uri = None
        return None
    _raise_for(r)


def _raise_for(r: httpx.Response):
    if r.status_code < 300:
        return
    reason, msg = _google_error(r)
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded", "uploadLimitExceeded"):
        raise QuotaExceeded()
    if r.status_code >= 500 or r.status_code == 429:
        raise Transient(f"YouTube returned {r.status_code}.")
    if r.status_code == 403 and reason in ("forbidden", "insufficientPermissions"):
        raise AuthRequired()
    raise UserError(f"YouTube rejected the upload: {msg or reason or r.status_code}")


def _finish(db: Session, u: YouTubeUpload, video: dict, size: int):
    u.youtube_video_id = video.get("id")
    u.upload_status, u.bytes_sent, u.session_uri, u.uploaded_at = "uploaded", size, None, now()
    st = video.get("status", {})
    if u.publish_at and u.schedule_status != "failed":
        if st.get("publishAt"):
            u.schedule_status = "scheduled"
        else:
            u.schedule_status = "failed"
            u.error = "Uploaded as private, but YouTube did not confirm the schedule. Set the publish time in YouTube Studio."
    elif st.get("privacyStatus") == "public":
        u.schedule_status = "published"
    db.commit()


# ---------- link import (own channels only) ----------

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(url: str) -> str | None:
    url = url.strip()
    if VIDEO_ID.match(url):
        return url
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        cand = p.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if p.path == "/watch":
            cand = parse_qs(p.query).get("v", [""])[0]
        else:
            parts = p.path.strip("/").split("/")
            cand = parts[1] if len(parts) > 1 and parts[0] in ("shorts", "live", "embed", "v") else ""
    else:
        return None
    return cand if VIDEO_ID.match(cand) else None


class ImportRequest(BaseModel):
    url: str = Field(min_length=5, max_length=500)


@router.post("/import", status_code=201)
def import_link(body: ImportRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    vid = parse_video_id(body.url)
    if not vid:
        raise HTTPException(422, "That doesn't look like a YouTube video link.")
    return projects.project_out(create_import(db, user, vid), db)


def create_import(db: Session, user: User, vid: str, automated: bool = False) -> Project:
    """Verify the video is on one of the user's connected channels, then queue its download."""
    from .automation import start_import
    accounts = list(db.scalars(select(YouTubeAccount).where(YouTubeAccount.user_id == user.id, YouTubeAccount.status == "ok")))
    if not accounts:
        raise HTTPException(409, "Connect the YouTube channel that owns this video first.")
    mine = {a.channel_id for a in accounts}
    item = None
    for acc in accounts:  # private/unlisted videos are only visible to their owner's token
        try:
            r = gapi(db, acc, "GET", f"{API}/videos", params={"part": "snippet,contentDetails", "id": vid})
        except (AuthRequired, Transient):
            continue
        items = r.json().get("items", []) if r.status_code == 200 else []
        if items:
            item = items[0]
            if item["snippet"]["channelId"] in mine:
                break
    if not item:
        raise HTTPException(404, "Video not found, or not visible to any of your connected channels.")
    if item["snippet"]["channelId"] not in mine:
        raise HTTPException(403, f"This video belongs to \"{item['snippet'].get('channelTitle', 'another channel')}\", which isn't one "
                                 "of your connected channels. Link import only works for your own videos; connect that channel first.")
    acc_id = next((a.id for a in accounts if a.channel_id == item["snippet"]["channelId"]), None)
    return start_import(db, user.id, "youtube", vid, acc_id, item["snippet"].get("title", vid),
                        "youtube_import", {"video_id": vid}, est_bytes=0, automated=automated)


def run_import(job_id: int, ctx) -> None:
    import yt_dlp  # imported lazily: heavy, and only needed here

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        p = db.get(Project, job.project_id)
        vid, pid = job.params["video_id"], p.id
        url = job.params.get("url") or f"https://www.youtube.com/watch?v={vid}"
        source = job.params.get("source", "YouTube")
        p.status, p.error = "importing", None
        db.commit()
    out_dir = projects.project_dir(pid)
    state = {"cancelled": False}

    def hook(d):
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        info = d.get("info_dict") or {}
        is_audio_only = info.get("vcodec") == "none"
        lo, hi = (0.85, 0.95) if is_audio_only else (0.0, 0.85)
        try:
            if total:
                ctx.progress(lo + (hi - lo) * d.get("downloaded_bytes", 0) / total,
                             "Downloading audio" if is_audio_only else "Downloading video")
        except Cancelled:
            state["cancelled"] = True
            raise

    opts = {
        # Highest available quality up to 4K: a 9:16 crop of 1080p is only ~600 px wide, of 4K ~1200 px.
        # VP9/AV1 are fine; the renderer re-encodes to H.264 anyway.
        "format": "bv*[height<=2160]+ba/b[height<=2160]/bv*+ba/b",
        "merge_output_format": "mp4", "outtmpl": str(out_dir / "download.%(ext)s"), "noplaylist": True,
        "quiet": True, "no_warnings": True, "noprogress": True, "progress_hooks": [hook],
    }
    if "/" in config.FFMPEG:
        opts["ffmpeg_location"] = config.FFMPEG
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # yt_dlp.utils.DownloadError and friends
        if state["cancelled"] or isinstance(e, Cancelled):
            _fail_import(pid, "Import cancelled.")
            raise Cancelled()
        _fail_import(pid, f"Couldn't download the video from {source}. yt-dlp may need updating "
                          f"(pip install -U yt-dlp), or {source} is blocking this server.")
        log.warning("yt-dlp failed for %s: %s", url, str(e)[:300])
        raise UserError(f"Download from {source} failed. Try `pip install -U yt-dlp`, or download the file "
                        "yourself and upload it.")
    downloaded = out_dir / "download.mp4"
    if not downloaded.exists():
        _fail_import(pid, "The download finished but produced no MP4 file.")
        raise UserError("The download finished but produced no MP4 file.")
    ctx.progress(0.97, "Checking video")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        downloaded.rename(projects.source_path(p))
        for leftover in out_dir.glob("download.*"):
            leftover.unlink(missing_ok=True)
        p.upload_size = projects.source_path(p).stat().st_size
        try:
            projects.finalize_source(db, p)
        except media.MediaError as e:
            raise UserError(str(e))


def _fail_import(pid: int, msg: str):
    with SessionLocal() as db:
        p = db.get(Project, pid)
        if p:
            p.status, p.error = "failed", msg
            db.commit()


JOB_HANDLERS = {"youtube_upload": run_upload, "youtube_import": run_import}


# ---------- auto-publish + status sync (called periodically by the worker) ----------

class RuleBody(BaseModel):
    enabled: bool
    time_of_day: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str
    clips_per_day: int = Field(ge=1, le=6)
    project_ids: list[int] = []


def rule_out(r: AutoPublishRule) -> dict:
    return {k: getattr(r, k) for k in ("id", "account_id", "enabled", "time_of_day", "timezone", "clips_per_day", "project_ids")}


@router.put("/accounts/{aid}/rule")
def put_rule(aid: int, body: RuleBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, aid)
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"Unknown time zone '{body.timezone}'")
    owned = set(db.scalars(select(Project.id).where(Project.user_id == user.id, Project.id.in_(body.project_ids or [0]))))
    if set(body.project_ids) - owned:
        raise HTTPException(404, "Project not found")
    r = db.scalar(select(AutoPublishRule).where(AutoPublishRule.account_id == acc.id)) or AutoPublishRule(user_id=user.id, account_id=acc.id)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.add(r)
    db.commit()
    return rule_out(r)


def rule_slots(rule: AutoPublishRule, after: datetime, days: int = 30):
    """Daily slots: clips_per_day evenly spaced through 24h starting at time_of_day (local)."""
    zone = ZoneInfo(rule.timezone)
    hh, mm = map(int, rule.time_of_day.split(":"))
    start_day = after.astimezone(zone).date()
    step = timedelta(hours=24 / rule.clips_per_day)
    for d in range(days + 1):
        base = datetime.combine(start_day + timedelta(days=d), time(hh, mm))
        for k in range(rule.clips_per_day):
            s = (base + step * k).replace(tzinfo=zone).astimezone(timezone.utc)
            if s > after:
                yield s


def autopublish_tick(db: Session) -> int:
    """Give approved, rendered, never-uploaded clips the next free slots. Never touches unapproved clips."""
    created = 0
    for rule in db.scalars(select(AutoPublishRule).where(AutoPublishRule.enabled.is_(True))):
        acc = db.get(YouTubeAccount, rule.account_id)
        if not acc or acc.status != "ok":
            continue
        q = (select(Clip).join(Project).where(Project.user_id == rule.user_id, Clip.review_status == "approved",
                                              Clip.render_status == "done",
                                              ~select(YouTubeUpload.id).where(YouTubeUpload.clip_id == Clip.id).exists())
             .order_by(Clip.created_at))
        if rule.project_ids:
            q = q.where(Clip.project_id.in_(rule.project_ids))
        clips = [c for c in db.scalars(q.limit(50)) if (f := clip_file(c)) and f.exists()]
        if not clips:
            continue
        taken = {_aware(t) for t in db.scalars(select(YouTubeUpload.publish_at).where(
            YouTubeUpload.account_id == acc.id, YouTubeUpload.publish_at.is_not(None),
            YouTubeUpload.upload_status.not_in(("failed", "cancelled"))))}
        slots = (s for s in rule_slots(rule, now() + timedelta(minutes=30), days=60) if s not in taken)
        for c, s in zip(clips, slots):
            try:
                video_metadata(c)
            except UserError:
                continue  # e.g. no title; skip rather than upload junk
            # Uploaded now as private + publishAt; YouTube makes it public at the slot.
            u = YouTubeUpload(user_id=rule.user_id, clip_id=c.id, account_id=acc.id, privacy="private",
                              publish_at=s, timezone=rule.timezone)
            db.add(u)
            db.flush()
            _enqueue(db, u)
            created += 1
    db.commit()
    return created


def sync_published(db: Session) -> None:
    """Scheduled videos whose time has passed: ask YouTube whether they're public now (1 quota unit per 50)."""
    due = list(db.scalars(select(YouTubeUpload).where(YouTubeUpload.schedule_status == "scheduled",
                                                      YouTubeUpload.publish_at <= now()).limit(50)))
    by_acc: dict[int, list[YouTubeUpload]] = {}
    for u in due:
        if u.account_id:
            by_acc.setdefault(u.account_id, []).append(u)
    for aid, us in by_acc.items():
        acc = db.get(YouTubeAccount, aid)
        try:
            r = gapi(db, acc, "GET", f"{API}/videos", params={"part": "status", "id": ",".join(u.youtube_video_id for u in us)})
        except (AuthRequired, Transient):
            continue
        if r.status_code != 200:
            continue
        st = {i["id"]: i.get("status", {}) for i in r.json().get("items", [])}
        for u in us:
            s = st.get(u.youtube_video_id)
            if s and s.get("privacyStatus") == "public":
                u.schedule_status = "published"
            elif s is None:
                u.schedule_status, u.error = "failed", "The video is no longer on YouTube."
    db.commit()


def tick() -> int:
    with SessionLocal() as db:
        n = autopublish_tick(db)
        sync_published(db)
        return n
