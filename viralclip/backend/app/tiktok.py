"""TikTok publishing: Login Kit v2 OAuth (multiple accounts) + Content Posting API (direct post or inbox draft).

Mirrors youtube.py: Fernet-encrypted tokens, single-use OAuth state, a DB-backed job per post, run_after for
waits, a per-account daily cap, and auto-publish rules. Tests swap `http` for a mock transport.

TikTok has no publish-at field. A scheduled post is a job with run_after = the chosen time: our worker posts
it then, so scheduling only works while ViralClip (the worker) is running.

Written from TikTok's public docs without network access to developers.tiktok.com. Points I'm not certain of
are marked `UNVERIFIED:`; check them against the current docs when connecting a real app.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .auth import current_user
from .db import SessionLocal, get_db
from .models import (Clip, Job, OAuthState, Project, TikTokAccount, TikTokAutoPublishRule, TikTokPost, User, now)
from .worker import Cancelled, UserError
from .youtube import (AuthRequired, Transient, _aware, _parse_utc, _q, check_clip_uploadable, clip_file,
                      decrypt_tokens, encrypt_tokens, rule_slots, schedule_slots, to_utc)

log = logging.getLogger("viralclip.tiktok")
router = APIRouter(prefix="/api/tiktok", tags=["tiktok"])

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
API = "https://open.tiktokapis.com/v2"
# video.list: read your own videos, to verify link imports are yours and to read view counts of what we posted.
SCOPES = "user.info.basic,video.publish,video.upload,video.list"
IMPORT_SCOPE = "video.list"
RECONNECT_FOR_IMPORT = "Reconnect TikTok to allow importing your videos."
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
MODES = ("direct", "draft")
MAX_SINGLE_CHUNK = 64 * 1024 * 1024  # a file up to 64 MB goes up as one chunk
CHUNK = 10 * 1024 * 1024  # 5-64 MB allowed; the last chunk absorbs the remainder (TikTok allows it up to 128 MB)
CAPTION_MAX = 2200
POLL_EVERY = timedelta(seconds=10)  # status/fetch is rate-limited (UNVERIFIED: ~30 req/min per token)
MAX_POLLS = 180  # 30 minutes of processing, then we stop guessing and say so
MAX_TRANSIENT_FAILURES = 5
LATE_LIMIT = timedelta(hours=12)  # a scheduled post missed by more than this (worker was off) isn't posted silently
ACTIVE = ("scheduled", "queued", "uploading", "processing", "auth_required")
DONE = ("published", "draft_sent")
IN_PROGRESS = ("uploading", "processing")
SCHEDULING_NOTE = ("TikTok has no scheduled-publish feature, so ViralClip posts at the chosen time itself. "
                   "Scheduled posts only go out while ViralClip (the worker) is running.")

http = httpx.Client(timeout=httpx.Timeout(120, connect=15))


class RateLimited(Exception):
    def __init__(self, delay: timedelta, msg: str):
        super().__init__(msg)
        self.delay = delay


# ---------- config / tokens ----------

def missing_config() -> list[str]:
    return [k for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI") if not getattr(config, k)]


def _state_hash(state: str) -> str:
    # Prefixed so a YouTube/Twitch OAuth state can never be replayed here (they share the oauth_states table).
    return hashlib.sha256(f"tiktok:{state}".encode()).hexdigest()


def _err(r: httpx.Response) -> tuple[str, str]:
    """(code, message). API shape: {"error": {"code", "message", "log_id"}}; OAuth shape: {"error", "error_description"}."""
    try:
        d = r.json()
    except ValueError:
        return "", r.text[:200]
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, dict):
        return e.get("code", "") or "", e.get("message", "") or ""
    if isinstance(e, str) and e:
        return e, d.get("error_description", e)
    return "", ""


def _token_call(data: dict) -> dict:
    """POST to the token endpoint. Returns the JSON body; raises AuthRequired / Transient."""
    try:
        r = http.post(TOKEN_URL, data={"client_key": config.TIKTOK_CLIENT_KEY,
                                       "client_secret": config.TIKTOK_CLIENT_SECRET, **data},
                      headers={"Cache-Control": "no-cache"})
    except httpx.HTTPError as e:
        raise Transient(f"Network error talking to TikTok: {type(e).__name__}")
    if r.status_code >= 500:
        raise Transient(f"TikTok token endpoint returned {r.status_code}")
    code, _ = _err(r)
    # UNVERIFIED: TikTok may answer token errors with HTTP 200 + an "error" field; treat both shapes as a refusal.
    if r.status_code >= 300 or code or "access_token" not in r.json():
        raise AuthRequired()
    return r.json()


def _store(acc: TikTokAccount, tok: dict, old_refresh: str | None = None):
    acc.encrypted_tokens = encrypt_tokens({"access_token": tok["access_token"],
                                           "refresh_token": tok.get("refresh_token") or old_refresh})
    acc.token_expiry = now() + timedelta(seconds=int(tok.get("expires_in", 86400)))  # access tokens last ~24h
    acc.status = "ok"


def access_token(db: Session, acc: TikTokAccount, force_refresh=False) -> str:
    if acc.status == "reauth_required":
        raise AuthRequired()
    tok = decrypt_tokens(acc.encrypted_tokens)
    exp = _aware(acc.token_expiry)
    if not force_refresh and exp and exp > now() + timedelta(seconds=60):
        return tok["access_token"]
    try:
        # Refresh tokens last ~365 days and may rotate: always keep the newest one.
        d = _token_call({"grant_type": "refresh_token", "refresh_token": tok.get("refresh_token") or ""})
    except AuthRequired:
        acc.status = "reauth_required"
        db.commit()
        raise
    _store(acc, d, tok.get("refresh_token"))
    db.commit()
    return d["access_token"]


def api(db: Session, acc: TikTokAccount, path: str, body: dict | None = None, method="POST") -> httpx.Response:
    """Authorized API call. An invalid token triggers one forced refresh; a second failure means reconnect."""
    for attempt in (0, 1):
        tok = access_token(db, acc, force_refresh=attempt == 1)
        try:
            r = http.request(method, f"{API}{path}", json=body,
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"})
        except httpx.HTTPError as e:
            raise Transient(f"Network error talking to TikTok: {type(e).__name__}")
        if r.status_code != 401 and _err(r)[0] != "access_token_invalid":
            return r
    acc.status = "reauth_required"
    db.commit()
    raise AuthRequired()


def _check(r: httpx.Response) -> dict:
    """Map TikTok's answer to data or an exception. Never treats an unknown answer as success."""
    code, msg = _err(r)
    if r.status_code < 300 and code in ("", "ok"):
        return (r.json() or {}).get("data") or {}
    if code in ("access_token_invalid", "scope_not_authorized"):
        raise AuthRequired()
    if code == "rate_limit_exceeded" or r.status_code == 429:
        raise RateLimited(timedelta(minutes=5), "TikTok rate limit reached; trying again in 5 minutes.")
    # spam_risk_too_many_posts / _too_many_pending_share: TikTok's own posting cap. A ban isn't something waiting fixes.
    if code.startswith("spam_risk") and code != "spam_risk_user_banned_from_posting":
        raise RateLimited(timedelta(hours=1), f"TikTok is limiting posts on this account right now ({msg or code}). "
                                              "Trying again in an hour.")
    if r.status_code >= 500 or code == "internal_error":
        raise Transient(f"TikTok returned {r.status_code}.")
    raise UserError(f"TikTok rejected the post: {msg or code or r.status_code}")


def creator_info(db: Session, acc: TikTokAccount) -> dict:
    """TikTok requires fetching this before each post: privacy options and limits can change per creator."""
    d = _check(api(db, acc, "/post/publish/creator_info/query/"))
    acc.creator_info = {k: d.get(k) for k in ("creator_nickname", "privacy_level_options", "comment_disabled",
                                              "duet_disabled", "stitch_disabled", "max_video_post_duration_sec")}
    db.commit()
    return acc.creator_info


def allowed_privacy(info: dict | None) -> list[str]:
    opts = [o for o in ((info or {}).get("privacy_level_options") or PRIVACY_LEVELS) if o in PRIVACY_LEVELS]
    return [o for o in opts if o == "SELF_ONLY"] if not config.TIKTOK_AUDITED else opts


# ---------- small helpers ----------

def owned_account(db: Session, user: User, aid: int) -> TikTokAccount:
    a = db.get(TikTokAccount, aid)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "TikTok account not found")
    return a


def owned_post(db: Session, user: User, pid: int) -> TikTokPost:
    p = db.get(TikTokPost, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Post not found")
    return p


def utc_day(at: datetime | None = None) -> str:
    # ponytail: TikTok doesn't document when its ~15 posts/day cap resets; we count per UTC day.
    return (at or now()).astimezone(timezone.utc).date().isoformat()


def next_utc_day() -> datetime:
    return datetime.combine(now().astimezone(timezone.utc).date() + timedelta(days=1), time(0, 5), timezone.utc)


def posts_today(db: Session, account_id: int) -> int:
    return db.scalar(select(func.count()).select_from(TikTokPost).where(
        TikTokPost.account_id == account_id, TikTokPost.quota_day == utc_day()))


def clip_duration(c: Clip) -> float:
    return (c.render_settings or {}).get("_rendered_duration") or (c.end - c.start)


def caption(c: Clip) -> str:
    """Title plus #hashtags, as TikTok's post_info.title (the caption)."""
    title = (c.title or "").strip()
    if not title:
        raise UserError("The clip has no title. Add one before posting.")
    tags = [re.sub(r"[^\w]", "", h.lstrip("#")) for h in (c.hashtags or [])]
    cap = " ".join([title] + ["#" + t for t in tags if t])
    if len(cap) > CAPTION_MAX:  # UNVERIFIED: TikTok counts UTF-16 code units; len() is close enough for ASCII/BMP text
        raise UserError(f"The caption (title + hashtags) is longer than TikTok's {CAPTION_MAX} characters.")
    return cap


# ---------- OAuth ----------

@router.get("/config")
def tiktok_config(user: User = Depends(current_user)):
    return {"configured": not missing_config(), "missing": missing_config(), "audited": config.TIKTOK_AUDITED,
            "redirect_uri": config.TIKTOK_REDIRECT_URI, "daily_limit": config.TIKTOK_DAILY_POST_LIMIT,
            "default_mode": "direct" if config.TIKTOK_AUDITED else "draft", "scheduling_note": SCHEDULING_NOTE}


@router.get("/connect")
def connect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if missing_config():
        return RedirectResponse("/tiktok?error=" + _q("TikTok isn't configured on this server yet. See the setup steps."), 303)
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(user_id=user.id, state_hash=_state_hash(state), expires_at=now() + timedelta(minutes=10)))
    db.commit()
    # UNVERIFIED: PKCE is documented as required for desktop/mobile Login Kit, not for web apps with a secret.
    params = {"client_key": config.TIKTOK_CLIENT_KEY, "scope": SCOPES, "response_type": "code",
              "redirect_uri": config.TIKTOK_REDIRECT_URI, "state": state}
    return RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", 303)


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             scopes: str | None = None, vc_session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    try:
        user = current_user(vc_session, db)
    except HTTPException:
        return RedirectResponse("/login?next=/tiktok", 303)
    if not state:
        return RedirectResponse("/tiktok?error=" + _q("Missing OAuth state. Start the connection again."), 303)
    st = db.scalar(select(OAuthState).where(OAuthState.state_hash == _state_hash(state)))
    valid = st is not None and st.user_id == user.id and _aware(st.expires_at) > now()
    if st is not None:
        db.delete(st)  # single use, even when invalid
        db.commit()
    if not valid:
        return RedirectResponse("/tiktok?error=" + _q("The connection request expired or was invalid. Try again."), 303)
    if error or not code:
        return RedirectResponse("/tiktok?error=" + _q("TikTok sign-in was cancelled or denied."), 303)
    try:
        tok = _token_call({"code": code, "grant_type": "authorization_code", "redirect_uri": config.TIKTOK_REDIRECT_URI})
    except (AuthRequired, Transient):
        return RedirectResponse("/tiktok?error=" + _q("TikTok rejected the sign-in. Try again."), 303)
    granted = set(re.split(r"[,\s]+", tok.get("scope") or scopes or SCOPES))
    if not {"video.upload", "video.publish"} <= granted:
        return RedirectResponse("/tiktok?error=" + _q("Posting permission wasn't granted, so ViralClip can't post for you."), 303)
    try:
        r = http.get(f"{API}/user/info/", params={"fields": "open_id,display_name,avatar_url"},
                     headers={"Authorization": f"Bearer {tok['access_token']}"})
        info = _check(r).get("user") or {}
    except (httpx.HTTPError, UserError, Transient, RateLimited, AuthRequired):
        info = {}
    open_id = info.get("open_id") or tok.get("open_id")
    if not open_id:
        return RedirectResponse("/tiktok?error=" + _q("Couldn't read your TikTok profile. Try again."), 303)
    acc = db.scalar(select(TikTokAccount).where(TikTokAccount.user_id == user.id, TikTokAccount.open_id == open_id))
    old_refresh = None
    if acc is None:
        acc = TikTokAccount(user_id=user.id, open_id=open_id)
        db.add(acc)
    else:
        old_refresh = decrypt_tokens(acc.encrypted_tokens).get("refresh_token") if acc.encrypted_tokens else None
    acc.display_name = (info.get("display_name") or acc.display_name or "TikTok account")[:200]
    acc.avatar_url = info.get("avatar_url") or acc.avatar_url
    acc.scopes = ",".join(sorted(granted))
    _store(acc, tok, old_refresh)
    db.commit()
    return RedirectResponse("/tiktok?connected=1", 303)


def rule_out(r: TikTokAutoPublishRule) -> dict:
    return {k: getattr(r, k) for k in ("enabled", "time_of_day", "timezone", "clips_per_day", "mode", "privacy_level")}


def account_out(db: Session, a: TikTokAccount) -> dict:
    rule = db.scalar(select(TikTokAutoPublishRule).where(TikTokAutoPublishRule.account_id == a.id))
    return {"id": a.id, "open_id": a.open_id, "display_name": a.display_name, "avatar_url": a.avatar_url,
            "status": a.status, "posts_today": posts_today(db, a.id), "daily_limit": config.TIKTOK_DAILY_POST_LIMIT,
            "rule": rule_out(rule) if rule else None}


@router.get("/accounts")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [account_out(db, a) for a in db.scalars(select(TikTokAccount).where(TikTokAccount.user_id == user.id)
                                                   .order_by(TikTokAccount.created_at))]


@router.delete("/accounts/{aid}", status_code=204)
def disconnect(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_account(db, user, aid)
    try:
        tok = decrypt_tokens(a.encrypted_tokens)
        http.post(REVOKE_URL, data={"client_key": config.TIKTOK_CLIENT_KEY, "client_secret": config.TIKTOK_CLIENT_SECRET,
                                    "token": tok.get("access_token")})
    except (AuthRequired, httpx.HTTPError):
        pass  # best effort; our copy of the tokens is deleted regardless
    for p in db.scalars(select(TikTokPost).where(TikTokPost.account_id == a.id, TikTokPost.status.in_(ACTIVE))):
        _stop(db, p, "cancelled", "TikTok account disconnected")
    db.delete(a)  # posts keep their history: account_id SET NULL
    db.commit()


@router.get("/accounts/{aid}/creator_info")
def get_creator_info(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_account(db, user, aid)
    try:
        info = creator_info(db, a)
    except AuthRequired:
        raise HTTPException(409, f"Reconnect {a.display_name or 'this TikTok account'} first; its authorization expired.")
    except RateLimited as e:
        raise HTTPException(429, str(e))
    except Transient as e:
        raise HTTPException(502, str(e))
    except UserError as e:
        raise HTTPException(502, str(e))
    return {**info, "privacy_level_options": allowed_privacy(info)}


# ---------- creating posts ----------

class PostOptions(BaseModel):
    mode: str | None = None  # direct | draft; default: direct when audited, else draft
    privacy_level: str | None = None
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False


class PostRequest(PostOptions):
    clip_ids: list[int] = Field(min_length=1, max_length=200)
    account_id: int
    publish_at: str | None = None  # local wall-clock 'YYYY-MM-DDTHH:MM'
    timezone: str | None = None
    force: bool = False


class ScheduleRequest(PostOptions):
    clip_ids: list[int] = Field(min_length=1, max_length=200)
    account_id: int
    start: str
    timezone: str
    interval: str = "daily"
    custom_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    force: bool = False


def check_mode(mode: str | None, privacy: str | None, info: dict | None, brand_content=False) -> tuple[str, str | None]:
    mode = mode or ("direct" if config.TIKTOK_AUDITED else "draft")
    if mode not in MODES:
        raise HTTPException(422, "mode must be direct or draft")
    if mode == "draft":
        return mode, None  # the user picks privacy in the TikTok app when finishing the draft
    if not privacy:
        raise HTTPException(422, "Pick a privacy level for direct posts")
    if privacy not in PRIVACY_LEVELS:
        raise HTTPException(422, f"privacy_level must be one of {', '.join(PRIVACY_LEVELS)}")
    if privacy not in allowed_privacy(info):
        if not config.TIKTOK_AUDITED:
            raise HTTPException(422, "This TikTok app isn't audited yet, so direct posts can only be private (SELF_ONLY). "
                                     "Use draft mode to finish posting publicly in the TikTok app.")
        raise HTTPException(422, f"This TikTok account doesn't allow privacy level {privacy}.")
    if brand_content and privacy == "SELF_ONLY":
        raise HTTPException(422, "TikTok doesn't allow branded content to be private.")
    return mode, privacy


def post_out(p: TikTokPost, db: Session) -> dict:
    c = db.get(Clip, p.clip_id)
    a = db.get(TikTokAccount, p.account_id) if p.account_id else None
    return {"id": p.id, "clip_id": p.clip_id, "clip_title": c.title if c else "",
            "clip_thumbnail_url": f"/api/clips/{p.clip_id}/thumbnail" if c and c.thumbnail_path else None,
            "account_id": p.account_id, "account_name": a.display_name if a else "(disconnected)",
            "mode": p.mode, "privacy_level": p.privacy_level, "status": p.status, "publish_at": p.publish_at,
            "timezone": p.timezone, "publish_id": p.publish_id, "error": p.error, "created_at": p.created_at,
            "posted_at": p.posted_at}


def _enqueue(db: Session, p: TikTokPost):
    c = db.get(Clip, p.clip_id)
    at = _aware(p.publish_at)
    run_after = at if at and at > now() else None
    j = Job(project_id=c.project_id, clip_id=c.id, job_type="tiktok_post", params={"post_id": p.id},
            run_after=run_after, message="Scheduled" if run_after else "Waiting for a worker")
    db.add(j)
    db.flush()
    p.job_id = j.id
    p.status = "scheduled" if run_after else "queued"


def _active_job(db: Session, p: TikTokPost) -> Job | None:
    j = db.get(Job, p.job_id) if p.job_id else None
    return j if j and j.status in ("queued", "running", "cancel_requested") else None


def _stop(db: Session, p: TikTokPost, status: str, error: str | None = None):
    j = _active_job(db, p)
    if j:
        j.status = "cancelled" if j.status == "queued" else "cancel_requested"
        if j.status == "cancelled":
            j.finished_at = now()
    p.status = status
    if error:
        p.error = error


def _duplicate(db: Session, clip_id: int, account_id: int) -> TikTokPost | None:
    return db.scalar(select(TikTokPost).where(TikTokPost.clip_id == clip_id, TikTokPost.account_id == account_id,
                                              TikTokPost.status.in_(ACTIVE + DONE)).limit(1))


def create_posts(db: Session, user: User, acc: TikTokAccount, o: PostOptions, clip_ids: list[int],
                 slots: list[tuple[datetime | None, str | None]], force: bool) -> list[TikTokPost]:
    if acc.status != "ok":
        raise HTTPException(409, f"Reconnect {acc.display_name or 'this TikTok account'} first; its authorization expired.")
    if len(set(clip_ids)) != len(clip_ids):
        raise HTTPException(422, "The same clip was selected twice")
    mode, privacy = check_mode(o.mode, o.privacy_level, acc.creator_info, o.brand_content_toggle)
    clips = [check_clip_uploadable(db, user, cid) for cid in clip_ids]
    max_dur = (acc.creator_info or {}).get("max_video_post_duration_sec")
    for c in clips:
        if mode == "direct":
            try:
                caption(c)
            except UserError as e:
                raise HTTPException(422, f"Clip {c.id}: {e}")
        if max_dur and clip_duration(c) > max_dur:
            raise HTTPException(422, f"Clip {c.id} is {clip_duration(c):.0f}s; this TikTok account allows up to {max_dur}s.")
        dup = _duplicate(db, c.id, acc.id)
        if dup and not force:
            raise HTTPException(409, detail={"message": f"Clip {c.id} is already {'posted' if dup.status in DONE else 'queued'} "
                                             "for this TikTok account. Confirm to post it again.", "duplicate_clip_id": c.id})
    for at, _ in slots:
        if at is not None and at <= now() + timedelta(minutes=1):
            raise HTTPException(422, "Scheduled times must be in the future.")
    opts = o.model_dump(include={"disable_comment", "disable_duet", "disable_stitch", "brand_content_toggle", "brand_organic_toggle"})
    out = []
    for c, (at, tz) in zip(clips, slots):
        p = TikTokPost(user_id=user.id, clip_id=c.id, account_id=acc.id, mode=mode, privacy_level=privacy,
                       options=opts, publish_at=at, timezone=tz)
        db.add(p)
        db.flush()
        _enqueue(db, p)
        out.append(p)
    db.commit()
    return out


@router.post("/posts", status_code=201)
def post_posts(body: PostRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, body.account_id)
    if body.publish_at and not body.timezone:
        raise HTTPException(422, "Pick a time zone for the scheduled time")
    at = to_utc(body.publish_at, body.timezone) if body.publish_at else None
    ps = create_posts(db, user, acc, body, body.clip_ids, [(at, body.timezone if at else None)] * len(body.clip_ids), body.force)
    return [post_out(p, db) for p in ps]


@router.post("/schedule/preview")
def schedule_preview(body: ScheduleRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_account(db, user, body.account_id)
    slots = schedule_slots(body.start, body.timezone, body.interval, len(body.clip_ids), body.custom_hours)
    clips = [check_clip_uploadable(db, user, cid) for cid in body.clip_ids]
    zone = ZoneInfo(body.timezone)
    return {"slots": [{"clip_id": c.id, "clip_title": c.title, "publish_at_utc": s,
                       "local": s.astimezone(zone).strftime("%a %d %b %Y, %H:%M")} for c, s in zip(clips, slots)]}


@router.post("/schedule/confirm", status_code=201)
def schedule_confirm(body: ScheduleRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, body.account_id)
    slots = schedule_slots(body.start, body.timezone, body.interval, len(body.clip_ids), body.custom_hours)
    ps = create_posts(db, user, acc, body, body.clip_ids, [(s, body.timezone) for s in slots], body.force)
    return [post_out(p, db) for p in ps]


@router.get("/posts")
def list_posts(status: str | None = None, account_id: int | None = None, start: str | None = None, end: str | None = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(TikTokPost).where(TikTokPost.user_id == user.id)
    if status:
        q = q.where(TikTokPost.status.in_(status.split(",")))
    if account_id:
        q = q.where(TikTokPost.account_id == account_id)
    when = func.coalesce(TikTokPost.publish_at, TikTokPost.posted_at, TikTokPost.created_at)
    if start:
        q = q.where(when >= _parse_utc(start))
    if end:
        q = q.where(when < _parse_utc(end))
    return [post_out(p, db) for p in db.scalars(q.order_by(TikTokPost.created_at.desc()).limit(500))]


@router.post("/posts/{pid}/cancel")
def cancel_post(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_post(db, user, pid)
    if p.status == "processing" or p.status in DONE:
        raise HTTPException(409, "This video was already handed to TikTok. Delete it in the TikTok app if needed.")
    if p.status not in ACTIVE:
        raise HTTPException(409, f"Can't cancel a post that is {p.status}")
    _stop(db, p, "cancelled")  # an upload in flight stops at its next chunk; TikTok discards incomplete uploads
    db.commit()
    return post_out(p, db)


@router.post("/posts/{pid}/retry")
def retry_post(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_post(db, user, pid)
    if p.status not in ("failed", "cancelled", "auth_required"):
        raise HTTPException(409, f"Can't retry a post that is {p.status}")
    if _active_job(db, p):
        raise HTTPException(409, "This post is already queued")
    acc = db.get(TikTokAccount, p.account_id) if p.account_id else None
    if not acc:
        raise HTTPException(409, "The TikTok account for this post was disconnected. Post the clip again.")
    if acc.status != "ok":
        raise HTTPException(409, f"Reconnect {acc.display_name} first.")
    if p.publish_at and _aware(p.publish_at) <= now():
        p.publish_at = None  # its time passed: retrying means "post now"
    p.error, p.transient_failures, p.publish_id = None, 0, None
    _enqueue(db, p)
    db.commit()
    return post_out(p, db)


@router.delete("/posts/{pid}", status_code=204)
def remove_post(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Removes the item from ViralClip. A video already on TikTok stays there."""
    p = owned_post(db, user, pid)
    if p.status in IN_PROGRESS:
        raise HTTPException(409, "This post is being uploaded or processed. Wait for it to finish, or cancel it first.")
    j = _active_job(db, p)
    if j and j.status == "running":
        raise HTTPException(409, "Cancel the post first")
    if j:
        j.status, j.finished_at = "cancelled", now()
    db.delete(p)
    db.commit()


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    def n(*statuses):
        return db.scalar(select(func.count()).select_from(TikTokPost).where(TikTokPost.user_id == user.id,
                                                                            TikTokPost.status.in_(statuses)))
    return {"scheduled": n("scheduled"), "posting": n("queued", "uploading", "processing"), "published": n("published"),
            "drafts_sent": n("draft_sent"), "failed": n("failed", "auth_required")}


# ---------- post job ----------

def _requeue(db: Session, job: Job, p: TikTokPost, when: datetime, msg: str | None, status="queued", **params):
    p.status = status
    if msg is not None:
        p.error = msg
    job.status, job.run_after, job.message = "queued", when, (msg or "Waiting for TikTok to process the video")[:300]
    if params:
        job.params = {**job.params, **params}  # reassign: JSON columns don't track mutation
    db.commit()


def run_post(job_id: int, ctx) -> None:
    db = SessionLocal()
    try:
        _run_post(db, job_id, ctx)
    finally:
        db.close()


def _fail(db: Session, p: TikTokPost, msg: str):
    p.status, p.error = "failed", msg
    db.commit()
    raise UserError(msg)


def _run_post(db: Session, job_id: int, ctx) -> None:
    job = db.get(Job, job_id)
    p = db.get(TikTokPost, job.params.get("post_id"))
    if p is None or p.status in ("cancelled",) + DONE:
        raise Cancelled()
    acc = db.get(TikTokAccount, p.account_id) if p.account_id else None
    if acc is None:
        _fail(db, p, "The TikTok account was disconnected.")
    try:
        if p.publish_id and p.status == "uploading":
            # A worker died mid-post. Only start over if TikTok is still waiting for bytes; otherwise the video
            # was fully sent and starting over would post it twice.
            st = _check(api(db, acc, "/post/publish/status/fetch/", {"publish_id": p.publish_id})).get("status")
            if st in ("PROCESSING_UPLOAD", "FAILED"):
                p.publish_id = None
            else:
                p.status = "processing"
        if p.status == "processing" and p.publish_id:
            return _poll(db, job, p)
        _start(db, job, p, acc, ctx)
    except RateLimited as e:
        db.rollback()
        if p.status != "processing":
            p.publish_id = None  # an unfinished upload is discarded by TikTok; the next attempt starts fresh
        _requeue(db, job, p, now() + e.delay, str(e), status="processing" if p.status == "processing" else "queued")
    except AuthRequired:
        db.rollback()
        acc.status = "reauth_required"  # also covers scope_not_authorized: reconnecting re-grants scopes
        if p.status != "processing":
            p.publish_id = None
        p.status, p.error = "auth_required", "TikTok authorization expired or was revoked. Reconnect the account, then retry."
        db.commit()
        raise UserError(p.error)
    except Transient as e:
        db.rollback()
        p.transient_failures += 1
        if p.status != "processing":
            p.publish_id = None
        if p.transient_failures > MAX_TRANSIENT_FAILURES:
            _fail(db, p, f"{e} Gave up after {MAX_TRANSIENT_FAILURES} retries; retry manually.")
        _requeue(db, job, p, now() + timedelta(seconds=30 * 2 ** p.transient_failures), f"{e} Retrying shortly.",
                 status="processing" if p.status == "processing" else "queued")
    except Cancelled:
        raise  # the cancel endpoint already set the status
    except UserError as e:
        db.rollback()
        p.publish_id = p.publish_id if p.status == "processing" else None
        _fail(db, p, str(e))
    except Exception:
        db.rollback()
        p.status, p.error = "failed", "Unexpected error while posting to TikTok. See worker logs."
        db.commit()
        raise


def _start(db: Session, job: Job, p: TikTokPost, acc: TikTokAccount, ctx):
    at = _aware(p.publish_at)
    if at and at > now() + timedelta(minutes=1):  # picked up early (e.g. rescheduled): wait for the slot
        return _requeue(db, job, p, at, None, status="scheduled")
    if at and at < now() - LATE_LIMIT:
        _fail(db, p, "ViralClip wasn't running at the scheduled time, so this wasn't posted. Retry to post it now.")
    clip = db.get(Clip, p.clip_id)
    f = clip_file(clip)
    if clip.review_status != "approved" or clip.render_status != "done" or not f or not f.exists():
        _fail(db, p, "The clip is no longer approved or its rendered file is missing.")
    if posts_today(db, acc.id) >= config.TIKTOK_DAILY_POST_LIMIT and p.quota_day != utc_day():
        return _requeue(db, job, p, next_utc_day(),
                        f"Daily limit of {config.TIKTOK_DAILY_POST_LIMIT} TikTok posts for this account reached; "
                        "posting after midnight UTC.")
    size = f.stat().st_size
    chunk, count = (size, 1) if size <= MAX_SINGLE_CHUNK else (CHUNK, size // CHUNK)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    if p.mode == "direct":
        info = creator_info(db, acc)
        if p.privacy_level not in allowed_privacy(info):
            raise UserError(f"TikTok no longer offers privacy level {p.privacy_level} for this account. "
                            "Pick another one and post again.")
        if info.get("max_video_post_duration_sec") and clip_duration(clip) > info["max_video_post_duration_sec"]:
            raise UserError(f"The clip is longer than this account's {info['max_video_post_duration_sec']}s limit.")
        o = p.options or {}
        post_info = {"title": caption(clip), "privacy_level": p.privacy_level,
                     "disable_comment": bool(o.get("disable_comment") or info.get("comment_disabled")),
                     "disable_duet": bool(o.get("disable_duet") or info.get("duet_disabled")),
                     "disable_stitch": bool(o.get("disable_stitch") or info.get("stitch_disabled")),
                     "brand_content_toggle": bool(o.get("brand_content_toggle")),
                     "brand_organic_toggle": bool(o.get("brand_organic_toggle"))}
        d = _check(api(db, acc, "/post/publish/video/init/", {"post_info": post_info, "source_info": source}))
    else:
        d = _check(api(db, acc, "/post/publish/inbox/video/init/", {"source_info": source}))
    if not d.get("publish_id") or not d.get("upload_url"):
        raise Transient("TikTok didn't return an upload address.")
    p.publish_id, p.quota_day, p.status, p.error = d["publish_id"], utc_day(), "uploading", None
    db.commit()
    ctx.progress(0, "Uploading to TikTok")
    with open(f, "rb") as fh:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            try:
                r = http.put(d["upload_url"], content=data,
                             headers={"Content-Range": f"bytes {start}-{end}/{size}", "Content-Type": "video/mp4"})
            except httpx.HTTPError as e:
                raise Transient(f"Network error uploading to TikTok: {type(e).__name__}")
            if r.status_code not in (200, 201, 206):
                _check(r)
                raise Transient(f"TikTok upload returned {r.status_code}.")
            ctx.progress((end + 1) / size, f"Uploading to TikTok ({(end + 1) * 100 // size}%)")
    p.status = "processing"
    db.commit()
    _requeue(db, job, p, now() + POLL_EVERY, None, status="processing", polls=0)


def _poll(db: Session, job: Job, p: TikTokPost):
    d = _check(api(db, acc_of(db, p), "/post/publish/status/fetch/", {"publish_id": p.publish_id}))
    st = d.get("status")
    if st == "PUBLISH_COMPLETE":
        p.status, p.posted_at, p.error = "published", now(), None
        # UNVERIFIED: TikTok's docs spell this field "publicaly_available_post_id" (a list of ids).
        ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
        p.tiktok_video_id = str(ids[0])[:32] if ids else None
        db.commit()
    elif st == "SEND_TO_USER_INBOX":
        # Draft mode's final state: the video waits in the user's TikTok inbox to be finished in the app.
        p.status, p.posted_at, p.error = "draft_sent", now(), None
        db.commit()
    elif st == "FAILED":
        _fail(db, p, f"TikTok couldn't publish the video: {d.get('fail_reason') or 'no reason given'}.")
    else:  # PROCESSING_UPLOAD / PROCESSING_DOWNLOAD / anything new: keep asking, but not forever
        polls = int(job.params.get("polls", 0)) + 1
        if polls > MAX_POLLS:
            _fail(db, p, "TikTok is still processing the video after 30 minutes. Check the TikTok app before retrying, "
                         "so it isn't posted twice.")
        _requeue(db, job, p, now() + POLL_EVERY, None, status="processing", polls=polls)


def acc_of(db: Session, p: TikTokPost) -> TikTokAccount:
    return db.get(TikTokAccount, p.account_id)


def _run_import(job_id: int, ctx) -> None:
    from .youtube import run_import
    run_import(job_id, ctx)


JOB_HANDLERS = {"tiktok_post": run_post, "tiktok_import": _run_import}


# ---------- link import (own videos only) + view counts ----------

VIDEO_PATH = re.compile(r"^/@([\w.-]+)/video/(\d{8,25})/?$")
SHORT_CODE = re.compile(r"^[A-Za-z0-9]{5,20}$")
LIST_PAGES = 10  # x 20 per page: the newest ~200 posts of each account are checked


def parse_video_url(url: str) -> tuple[str, str, str | None] | None:
    """("video", user, id) for tiktok.com/@user/video/<id>; ("short", short_url, None) for vm.tiktok.com/<code>
    or tiktok.com/t/<code>, which need resolve_short(); None when it isn't a TikTok video link."""
    url = url.strip()
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = p.path.strip("/").split("/")
    if host in ("vm.tiktok.com", "vt.tiktok.com") and len(parts) == 1 and SHORT_CODE.match(parts[0]):
        return "short", f"https://{host}/{parts[0]}/", None
    if host != "tiktok.com":
        return None
    if len(parts) == 2 and parts[0] == "t" and SHORT_CODE.match(parts[1]):
        return "short", f"https://www.tiktok.com/t/{parts[1]}/", None
    m = VIDEO_PATH.match(p.path)
    return ("video", m[1], m[2]) if m else None


def resolve_short(short_url: str) -> tuple[str, str] | None:
    """(user, id) behind a short link: follows ONE redirect, 5 s timeout. short_url comes from parse_video_url
    (fixed TikTok host + validated code), never straight from the user."""
    try:
        r = http.get(short_url, follow_redirects=False, timeout=5)
    except httpx.HTTPError:
        return None
    got = parse_video_url(r.headers.get("location", "")) if r.is_redirect else None
    return (got[1], got[2]) if got and got[0] == "video" else None


def has_list_scope(acc: TikTokAccount) -> bool:
    return IMPORT_SCOPE in re.split(r"[,\s]+", acc.scopes or "")


def find_own_video(db: Session, acc: TikTokAccount, vid: str) -> dict | None:
    """Page through the account's own posts (video/list) looking for vid."""
    cursor = None
    for _ in range(LIST_PAGES):
        body = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
        d = _check(api(db, acc, "/video/list/?fields=id,title,duration,create_time", body))
        for v in d.get("videos") or []:
            if str(v.get("id")) == vid:
                return v
        cursor = d.get("cursor")
        if not d.get("has_more") or not cursor:
            break
    return None


def create_import(db: Session, user: User, user_name: str, vid: str, automated: bool = False) -> Project:
    """Verify the video is one of the connected accounts' own posts (video.list), then queue its download."""
    from .automation import start_import
    accounts = list(db.scalars(select(TikTokAccount).where(TikTokAccount.user_id == user.id, TikTokAccount.status == "ok")))
    if not accounts:
        raise HTTPException(409, "Connect the TikTok account that owns this video first.")
    scoped = [a for a in accounts if has_list_scope(a)]
    for acc in scoped:
        try:
            v = find_own_video(db, acc, vid)
        except AuthRequired:  # expired, or video.list withdrawn in TikTok's settings
            raise HTTPException(409, RECONNECT_FOR_IMPORT)
        except (Transient, RateLimited, UserError) as e:
            raise HTTPException(502, f"TikTok didn't answer the ownership check: {e}")
        if v:
            return start_import(db, user.id, "tiktok", vid, acc.id, v.get("title") or f"TikTok {vid}", "tiktok_import",
                                {"video_id": vid, "url": f"https://www.tiktok.com/@{user_name}/video/{vid}", "source": "TikTok"},
                                est_bytes=0, automated=automated)
    if len(scoped) < len(accounts):  # it may be on an account that hasn't granted video.list
        raise HTTPException(409, RECONNECT_FOR_IMPORT)
    raise HTTPException(403, "This TikTok video isn't among your connected accounts' videos. Import only works for "
                             "your own videos; connect the account that posted it first.")


def sync_stats(db: Session) -> int:
    """View/like counts of posts we published, at most once per 24 h per account; skipped without video.list.
    Runs from the worker's periodic tick (under tick_lock). Returns how many video/query calls were made."""
    calls, cutoff = 0, now() - timedelta(hours=24)
    for acc in list(db.scalars(select(TikTokAccount).where(TikTokAccount.status == "ok"))):
        if not has_list_scope(acc) or (acc.stats_synced_at and _aware(acc.stats_synced_at) > cutoff):
            continue
        by_id = {p.tiktok_video_id: p for p in db.scalars(select(TikTokPost).where(
            TikTokPost.account_id == acc.id, TikTokPost.status == "published", TikTokPost.tiktok_video_id.is_not(None)))}
        if not by_id:
            continue
        acc.stats_synced_at = now()  # stamped first: a failing call is retried tomorrow, not every minute
        db.commit()
        ids = list(by_id)
        for i in range(0, len(ids), 20):  # UNVERIFIED: video/query takes up to 20 ids
            calls += 1
            try:
                d = _check(api(db, acc, "/video/query/?fields=id,view_count,like_count",
                               {"filters": {"video_ids": ids[i:i + 20]}}))
            except (AuthRequired, Transient, RateLimited, UserError) as e:
                db.rollback()
                log.warning("TikTok stats sync for account %s stopped: %s", acc.id, e)
                break
            for v in d.get("videos") or []:
                if p := by_id.get(str(v.get("id"))):
                    p.view_count, p.like_count, p.stats_synced_at = v.get("view_count"), v.get("like_count"), now()
            db.commit()
    return calls


# ---------- auto-publish rule ----------

class RuleBody(BaseModel):
    enabled: bool
    time_of_day: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str
    clips_per_day: int = Field(ge=1, le=15)
    mode: str | None = None
    privacy_level: str | None = None


@router.put("/accounts/{aid}/rule")
def put_rule(aid: int, body: RuleBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = owned_account(db, user, aid)
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"Unknown time zone '{body.timezone}'")
    mode, privacy = check_mode(body.mode, body.privacy_level, acc.creator_info)
    r = (db.scalar(select(TikTokAutoPublishRule).where(TikTokAutoPublishRule.account_id == acc.id))
         or TikTokAutoPublishRule(user_id=user.id, account_id=acc.id))
    r.enabled, r.time_of_day, r.timezone, r.clips_per_day = body.enabled, body.time_of_day, body.timezone, body.clips_per_day
    r.mode, r.privacy_level = mode, privacy
    db.add(r)
    db.commit()
    return rule_out(r)


def autopublish_tick(db: Session) -> int:
    """Give approved, rendered clips not yet posted to the account the rule's next free daily slots."""
    from .automation import event
    created = 0
    for rule in db.scalars(select(TikTokAutoPublishRule).where(TikTokAutoPublishRule.enabled.is_(True))):
        acc = db.get(TikTokAccount, rule.account_id)
        if not acc or acc.status != "ok":
            continue
        q = (select(Clip).join(Project).where(
            Project.user_id == rule.user_id, Clip.review_status == "approved", Clip.render_status == "done",
            ~select(TikTokPost.id).where(TikTokPost.clip_id == Clip.id, TikTokPost.account_id == acc.id).exists())
             .order_by(Clip.created_at))
        clips = [c for c in db.scalars(q.limit(50)) if (f := clip_file(c)) and f.exists()]
        if rule.mode == "direct":
            clips = [c for c in clips if _has_caption(c)]  # skip rather than post junk
        max_dur = (acc.creator_info or {}).get("max_video_post_duration_sec")
        clips = [c for c in clips if not max_dur or clip_duration(c) <= max_dur]
        if not clips:
            continue
        taken = {_aware(t) for t in db.scalars(select(TikTokPost.publish_at).where(
            TikTokPost.account_id == acc.id, TikTokPost.publish_at.is_not(None),
            TikTokPost.status.not_in(("failed", "cancelled"))))}
        slots = (s for s in rule_slots(rule, now() + timedelta(minutes=5), days=60) if s not in taken)
        n = 0
        for c, s in zip(clips, slots):
            p = TikTokPost(user_id=rule.user_id, clip_id=c.id, account_id=acc.id, mode=rule.mode,
                           privacy_level=rule.privacy_level, options={}, publish_at=s, timezone=rule.timezone)
            db.add(p)
            db.flush()
            _enqueue(db, p)
            n += 1
        if n:
            event(db, rule.user_id, "publish", f"Scheduled {n} clip(s) for TikTok ({acc.display_name}).")
        created += n
    db.commit()
    return created


def _has_caption(c: Clip) -> bool:
    try:
        caption(c)
        return True
    except UserError:
        return False
