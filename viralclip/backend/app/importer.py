"""One "paste a link" import for Twitch VODs, YouTube videos and TikTok posts.

Each platform's own create_import does the ownership check (your connected accounts only) and queues the
yt-dlp download through automation.start_import. This module only detects the platform and maps errors.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import projects, tiktok, twitch, youtube
from .auth import current_user
from .db import get_db
from .models import Project, SourceVideo, TikTokAccount, TwitchAccount, User, YouTubeAccount

router = APIRouter(prefix="/api/import", tags=["import"])
UNKNOWN = "Paste a Twitch VOD, YouTube video or TikTok link."
ACCOUNTS = {"twitch": TwitchAccount, "youtube": YouTubeAccount, "tiktok": TikTokAccount}


def detect(url: str) -> tuple[str | None, str | None, str | None]:
    """(platform, video_id, extra) without any network call. Bare IDs aren't accepted: an 11-digit number
    would be both a Twitch VOD and a YouTube ID. For TikTok, extra is the @user, or the short link to resolve."""
    url = (url or "").strip()
    if "/" not in url:
        return None, None, None
    if vid := twitch.parse_vod_id(url):
        return "twitch", vid, None
    if vid := youtube.parse_video_id(url):
        return "youtube", vid, None
    if t := tiktok.parse_video_url(url):
        return ("tiktok", t[2], t[1]) if t[0] == "video" else ("tiktok", None, t[1])
    return None, None, None


def _connected(db: Session, user: User, platform: str) -> bool:
    model = ACCOUNTS[platform]
    return db.scalar(select(model.id).where(model.user_id == user.id, model.status == "ok").limit(1)) is not None


@router.get("/detect")
def detect_link(url: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    platform, vid, _ = detect(url[:500])
    return {"platform": platform, "connected": bool(platform) and _connected(db, user, platform), "video_id": vid}


class ImportIn(BaseModel):
    url: str = Field(min_length=5, max_length=500)


@router.post("", status_code=201)
def import_link(body: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .automation import settings_of
    platform, vid, extra = detect(body.url)
    if not platform:
        raise HTTPException(422, UNKNOWN)
    if platform == "tiktok" and vid is None:
        got = tiktok.resolve_short(extra)
        if not got:
            raise HTTPException(422, "Couldn't open that TikTok short link. Paste the full tiktok.com/@you/video/… link instead.")
        extra, vid = got
    # Checked first so the answer carries the existing project (and costs no API call).
    sv = db.scalar(select(SourceVideo).where(SourceVideo.user_id == user.id, SourceVideo.platform == platform,
                                             SourceVideo.external_id == vid))
    if sv and sv.project_id and (old := db.get(Project, sv.project_id)):
        return JSONResponse({"detail": f"Already imported as project \"{old.name}\".", "project_id": old.id}, 409)
    automated = settings_of(user)["enabled"]
    if platform == "twitch":
        p = twitch.create_import(db, user, vid, automated=automated)
    elif platform == "youtube":
        p = youtube.create_import(db, user, vid, automated=automated)
    else:
        p = tiktok.create_import(db, user, extra, vid, automated=automated)
    return projects.project_out(p, db)
