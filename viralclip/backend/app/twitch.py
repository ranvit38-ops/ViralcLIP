"""Twitch: connect accounts (OAuth), list VODs, and import VODs from the user's OWN connected channels.

Downloads go through youtube.run_import (yt-dlp) with a Twitch URL. Tests swap `http` for a mock.
"""
import hashlib
import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, projects
from .auth import current_user
from .db import get_db
from .models import OAuthState, SourceVideo, TwitchAccount, User, now
from .youtube import AuthRequired, Transient, _aware, _q, decrypt_tokens, encrypt_tokens

log = logging.getLogger("viralclip.twitch")
router = APIRouter(prefix="/api/twitch", tags=["twitch"])

AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
REVOKE_URL = "https://id.twitch.tv/oauth2/revoke"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
API = "https://api.twitch.tv/helix"
VOD_ID = re.compile(r"^\d{5,15}$")

http = httpx.Client(timeout=httpx.Timeout(30, connect=10))


def missing_config() -> list[str]:
    return [k for k in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TWITCH_REDIRECT_URI") if not getattr(config, k)]


def _state_hash(state: str) -> str:
    # Prefixed so a YouTube OAuth state can never be replayed here (they share the oauth_states table).
    return hashlib.sha256(f"twitch:{state}".encode()).hexdigest()


# ---------- tokens / API ----------

def _refresh(db: Session, acc: TwitchAccount) -> str:
    tok = decrypt_tokens(acc.encrypted_tokens)
    try:
        r = http.post(TOKEN_URL, data={"client_id": config.TWITCH_CLIENT_ID, "client_secret": config.TWITCH_CLIENT_SECRET,
                                       "grant_type": "refresh_token", "refresh_token": tok.get("refresh_token") or ""})
    except httpx.HTTPError as e:
        raise Transient(f"Network error talking to Twitch: {type(e).__name__}")
    if r.status_code in (400, 401):  # refresh token revoked / invalid
        acc.status = "reauth_required"
        db.commit()
        raise AuthRequired()
    if r.status_code >= 300:
        raise Transient(f"Twitch token refresh failed ({r.status_code})")
    d = r.json()
    acc.encrypted_tokens = encrypt_tokens({"access_token": d["access_token"],
                                           "refresh_token": d.get("refresh_token") or tok.get("refresh_token")})
    acc.token_expiry = now() + timedelta(seconds=int(d.get("expires_in", 3600)))
    acc.status = "ok"
    db.commit()
    return d["access_token"]


def _token(db: Session, acc: TwitchAccount, force=False) -> str:
    if acc.status == "reauth_required":
        raise AuthRequired()
    exp = _aware(acc.token_expiry)
    if not force and exp and exp > now() + timedelta(seconds=60):
        return decrypt_tokens(acc.encrypted_tokens)["access_token"]
    return _refresh(db, acc)


def tapi(db: Session, acc: TwitchAccount, path: str, params: dict) -> dict:
    """Authorized Helix GET. A 401 forces one refresh; a second 401 means reconnect."""
    for attempt in (0, 1):
        tok = _token(db, acc, force=attempt == 1)
        try:
            r = http.get(f"{API}{path}", params=params,
                         headers={"Authorization": f"Bearer {tok}", "Client-Id": config.TWITCH_CLIENT_ID})
        except httpx.HTTPError as e:
            raise Transient(f"Network error talking to Twitch: {type(e).__name__}")
        if r.status_code == 401:
            continue
        if r.status_code >= 300:
            raise Transient(f"Twitch API error {r.status_code}")
        return r.json()
    acc.status = "reauth_required"
    db.commit()
    raise AuthRequired()


def validate(db: Session, acc: TwitchAccount) -> None:
    """Twitch requires apps that keep sessions to validate tokens hourly; automation calls this when polling."""
    try:
        r = http.get(VALIDATE_URL, headers={"Authorization": f"OAuth {_token(db, acc)}"})
    except httpx.HTTPError:
        return
    if r.status_code == 401:
        _token(db, acc, force=True)


def parse_duration(s: str) -> int:
    """Twitch durations look like '3h2m10s'."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s or "")
    return (int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0)) if m else 0


def vod_out(v: dict, imported: dict) -> dict:
    sv = imported.get(v["id"])
    return {"id": v["id"], "title": v.get("title", ""), "created_at": v.get("created_at"), "url": v.get("url"),
            "duration_seconds": parse_duration(v.get("duration", "")), "type": v.get("type"),
            "thumbnail_url": (v.get("thumbnail_url") or "").replace("%{width}", "320").replace("%{height}", "180") or None,
            "imported": sv is not None, "project_id": sv.project_id if sv else None}


def owned_account(db: Session, user: User, aid: int) -> TwitchAccount:
    a = db.get(TwitchAccount, aid)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "Twitch account not found")
    return a


def account_out(a: TwitchAccount) -> dict:
    return {k: getattr(a, k) for k in ("id", "twitch_user_id", "login", "display_name", "profile_image_url", "status", "created_at")}


# ---------- OAuth ----------

@router.get("/config")
def twitch_config(user: User = Depends(current_user)):
    return {"configured": not missing_config(), "missing": missing_config(), "redirect_uri": config.TWITCH_REDIRECT_URI}


@router.get("/connect")
def connect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if missing_config():
        return RedirectResponse("/automation?error=" + _q("Twitch isn't configured on this server yet. See docs/TWITCH_SETUP.md."), 303)
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(user_id=user.id, state_hash=_state_hash(state), expires_at=now() + timedelta(minutes=10)))
    db.commit()
    # No scopes: listing and downloading your public VODs needs none.
    params = {"client_id": config.TWITCH_CLIENT_ID, "redirect_uri": config.TWITCH_REDIRECT_URI,
              "response_type": "code", "scope": "", "state": state, "force_verify": "true"}
    return RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", 303)


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             vc_session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    try:
        user = current_user(vc_session, db)
    except HTTPException:
        return RedirectResponse("/login?next=/automation", 303)
    st = db.scalar(select(OAuthState).where(OAuthState.state_hash == _state_hash(state))) if state else None
    valid = st is not None and st.user_id == user.id and _aware(st.expires_at) > now()
    if st is not None:
        db.delete(st)  # single use, even when invalid
        db.commit()
    if not valid:
        return RedirectResponse("/automation?error=" + _q("The Twitch connection request expired or was invalid. Try again."), 303)
    if error or not code:
        return RedirectResponse("/automation?error=" + _q("Twitch sign-in was cancelled or denied."), 303)
    try:
        r = http.post(TOKEN_URL, data={"client_id": config.TWITCH_CLIENT_ID, "client_secret": config.TWITCH_CLIENT_SECRET,
                                       "code": code, "grant_type": "authorization_code",
                                       "redirect_uri": config.TWITCH_REDIRECT_URI})
        tok = r.json() if r.status_code == 200 else None
        u = http.get(f"{API}/users", headers={"Authorization": f"Bearer {tok['access_token']}",
                                               "Client-Id": config.TWITCH_CLIENT_ID}) if tok else None
    except httpx.HTTPError:
        tok = u = None
    items = u.json().get("data", []) if u is not None and u.status_code == 200 else []
    if not tok or not items:
        log.warning("Twitch OAuth exchange failed (%s)", "token" if tok is None else "users lookup")
        return RedirectResponse("/automation?error=" + _q("Twitch rejected the sign-in. Try again."), 303)
    it = items[0]
    acc = db.scalar(select(TwitchAccount).where(TwitchAccount.user_id == user.id, TwitchAccount.twitch_user_id == it["id"]))
    if acc is None:
        acc = TwitchAccount(user_id=user.id, twitch_user_id=it["id"])
        db.add(acc)
    acc.login, acc.display_name = it.get("login", "")[:64], it.get("display_name", "")[:100]
    acc.profile_image_url = it.get("profile_image_url")
    acc.encrypted_tokens = encrypt_tokens({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token")})
    acc.token_expiry = now() + timedelta(seconds=int(tok.get("expires_in", 3600)))
    acc.status = "ok"
    db.commit()
    return RedirectResponse("/automation?twitch_connected=1", 303)


@router.get("/accounts")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [account_out(a) for a in db.scalars(select(TwitchAccount).where(TwitchAccount.user_id == user.id)
                                                .order_by(TwitchAccount.created_at))]


@router.delete("/accounts/{aid}", status_code=204)
def disconnect(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_account(db, user, aid)
    try:
        tok = decrypt_tokens(a.encrypted_tokens).get("access_token")
        if tok:
            http.post(REVOKE_URL, data={"client_id": config.TWITCH_CLIENT_ID, "token": tok})
    except (AuthRequired, httpx.HTTPError):
        pass  # still forget it locally
    db.delete(a)
    db.commit()


# ---------- VODs ----------

def list_vods(db: Session, acc: TwitchAccount, kind: str = "archive", first: int = 20) -> list[dict]:
    return tapi(db, acc, "/videos", {"user_id": acc.twitch_user_id, "type": kind, "first": first}).get("data", [])


def imported_map(db: Session, user_id: int) -> dict:
    return {s.external_id: s for s in db.scalars(select(SourceVideo).where(
        SourceVideo.user_id == user_id, SourceVideo.platform == "twitch"))}


@router.get("/accounts/{aid}/videos")
def account_videos(aid: int, type: str = "archive", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if type not in ("archive", "highlight", "upload", "all"):
        raise HTTPException(422, "type must be archive, highlight, upload or all")
    a = owned_account(db, user, aid)
    try:
        vods = list_vods(db, a, type)
    except AuthRequired:
        raise HTTPException(409, "Twitch access expired. Reconnect this account.")
    except Transient as e:
        raise HTTPException(502, str(e))
    seen = imported_map(db, user.id)
    return [vod_out(v, seen) for v in vods]


def parse_vod_id(s: str) -> str | None:
    s = s.strip()
    if VOD_ID.match(s):
        return s
    p = urlparse(s if "://" in s else "https://" + s)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host != "twitch.tv":
        return None
    m = re.match(r"^/(?:[^/]+/)?videos?/(\d+)", p.path)  # /videos/123 or /<channel>/video/123
    return m[1] if m and VOD_ID.match(m[1]) else None


def create_import(db: Session, user: User, vod_id: str, automated: bool = False) -> "projects.Project":
    """Verify the VOD belongs to one of the user's connected Twitch accounts, then queue its download."""
    from .automation import start_import
    accounts = list(db.scalars(select(TwitchAccount).where(TwitchAccount.user_id == user.id, TwitchAccount.status == "ok")))
    if not accounts:
        raise HTTPException(409, "Connect the Twitch account that owns this VOD first.")
    mine = {a.twitch_user_id: a for a in accounts}
    vod = None
    for acc in accounts:
        try:
            data = tapi(db, acc, "/videos", {"id": vod_id}).get("data", [])
        except (AuthRequired, Transient):
            continue
        if data:
            vod = data[0]
            break
    if not vod:
        raise HTTPException(404, "VOD not found. It may have expired (Twitch deletes past broadcasts after 7–60 days).")
    if vod.get("user_id") not in mine:
        raise HTTPException(403, f"This VOD belongs to \"{vod.get('user_name', 'another channel')}\", which isn't one of your "
                                 "connected Twitch accounts. Import only works for your own streams; connect that account first.")
    # Source-quality Twitch VODs run ~3-4 GB/hour; reserve that plus headroom.
    secs = parse_duration(vod.get("duration", ""))
    est = int(secs / 3600 * 4 * 1024**3)
    acc = mine[vod["user_id"]]
    p = start_import(db, user.id, "twitch", vod_id, acc.id, vod.get("title") or f"Twitch VOD {vod_id}",
                     "twitch_import", {"video_id": vod_id, "url": f"https://www.twitch.tv/videos/{vod_id}", "source": "Twitch"},
                     est_bytes=est, automated=automated)
    # What chat.vod_spikes needs to line the recorded chat up with this VOD. Reassign: JSON doesn't track mutation.
    p.settings = {**(p.settings or {}), "twitch": {"login": acc.login, "vod_id": vod_id,
                                                   "started_at": vod.get("created_at"), "duration_s": secs}}
    db.commit()
    return p


class ImportIn(BaseModel):
    url: str = Field(min_length=5, max_length=500)


@router.post("/import", status_code=201)
def import_vod(body: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    vid = parse_vod_id(body.url)
    if not vid:
        raise HTTPException(422, "That doesn't look like a Twitch VOD link (twitch.tv/videos/…).")
    from .automation import settings_of
    p = create_import(db, user, vid, automated=settings_of(user)["enabled"])
    return projects.project_out(p, db)


def _run_twitch_import(job_id: int, ctx) -> None:
    from .youtube import run_import
    run_import(job_id, ctx)


JOB_HANDLERS = {"twitch_import": _run_twitch_import}
