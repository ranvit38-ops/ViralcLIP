import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import config, llm
from .auth import current_user
from .db import get_db
from .models import Clip, Job, Project, Transcript, User
from .projects import job_out, owned_project, project_dir

router = APIRouter(prefix="/api", tags=["clips"])
ACTIVE = ("queued", "running", "cancel_requested")


# ---------- helpers ----------

def owned_clip(db: Session, user: User, cid: int) -> Clip:
    c = db.get(Clip, cid)
    if not c or c.project.user_id != user.id:
        raise HTTPException(404, "Clip not found")
    return c


def clip_out(c: Clip, db: Session) -> dict:
    from .pipeline import render_settings
    d = {k: getattr(c, k) for k in (
        "id", "project_id", "start", "end", "transcript_excerpt", "score", "score_breakdown", "reason",
        "review_status", "title", "title_options", "title_edited", "description", "hashtags", "render_status",
        "caption_overrides", "created_at")}
    d["duration"] = round(c.end - c.start, 2)
    d["project_name"] = c.project.name
    d["render_settings"] = {k: v for k, v in render_settings(c, db.get(User, c.project.user_id).settings).items()}
    d["video_url"] = f"/api/clips/{c.id}/video" if c.render_status in ("done", "outdated") and c.render_path else None
    d["thumbnail_url"] = f"/api/clips/{c.id}/thumbnail" if c.thumbnail_path else f"/api/projects/{c.project_id}/thumbnail"
    job = db.scalar(select(Job).where(Job.clip_id == c.id).order_by(Job.id.desc()).limit(1))
    d["job"] = job_out(job) if job else None
    return d


def enqueue(db: Session, project_id: int, job_type: str, params: dict | None = None, clip_id: int | None = None) -> Job:
    q = select(Job).where(Job.project_id == project_id, Job.job_type == job_type, Job.status.in_(ACTIVE))
    q = q.where(Job.clip_id == clip_id) if clip_id else q
    if db.scalar(q):
        raise HTTPException(409, f"A {job_type} job is already running for this {'clip' if clip_id else 'project'}.")
    j = Job(project_id=project_id, clip_id=clip_id, job_type=job_type, params=params or {}, message="Waiting for a worker")
    db.add(j)
    db.commit()
    return j


# ---------- analyze / transcript ----------

class AnalyzeIn(BaseModel):
    min_len: float = Field(30, ge=5, le=600)
    max_len: float = Field(60, ge=6, le=600)
    count: int = Field(8, ge=1, le=50)
    language: str | None = Field(None, max_length=10)
    model: str | None = None
    retranscribe: bool = False


@router.post("/projects/{pid}/analyze", status_code=202)
def analyze(pid: int, body: AnalyzeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    if p.status not in ("uploaded", "ready"):
        raise HTTPException(409, "The video must finish uploading first.")
    if not p.has_audio:
        raise HTTPException(422, "This video has no audio track, so it can't be transcribed.")
    if body.min_len >= body.max_len:
        raise HTTPException(422, "Minimum length must be shorter than maximum length.")
    from .pipeline import WHISPER_MODELS
    if body.model and body.model not in WHISPER_MODELS:
        raise HTTPException(422, "Unknown transcription model.")
    return job_out(enqueue(db, p.id, "analyze", body.model_dump()))


@router.get("/projects/{pid}/transcript")
def transcript(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    t = db.scalar(select(Transcript).where(Transcript.project_id == p.id))
    if not t:
        raise HTTPException(404, "No transcript yet")
    return {"language": t.language, "model": t.model, "text": t.text,
            "segments": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in t.segments]}


# ---------- clips ----------

@router.get("/clips")
def list_clips(project_id: int | None = None, review_status: str | None = None, render_status: str | None = None,
               q: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Clip).join(Project).where(Project.user_id == user.id)
    if project_id:
        stmt = stmt.where(Clip.project_id == project_id)
    if review_status:
        stmt = stmt.where(Clip.review_status == review_status)
    if render_status:
        stmt = stmt.where(Clip.render_status == render_status)
    if q:
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Clip.title.ilike(like), Clip.transcript_excerpt.ilike(like), Project.name.ilike(like)))
    order = (Clip.start,) if project_id else (Clip.created_at.desc(), Clip.id)
    return [clip_out(c, db) for c in db.scalars(stmt.order_by(*order).limit(500))]


@router.get("/clips/{cid}")
def get_clip(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return clip_out(owned_clip(db, user, cid), db)


class CaptionLine(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=300)


class ClipUpdate(BaseModel):
    review_status: str | None = Field(None, pattern="^(suggested|approved|rejected)$")
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    title: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=4500)
    hashtags: list[str] | None = Field(None, max_length=30)
    render_settings: dict | None = None
    caption_overrides: list[CaptionLine] | None = None


RENDER_KEYS = {"aspect": {"9:16", "1:1", "4:5", "16:9"}, "layout": {"auto", "crop", "fit", "stream"},
               "preset": {"bold", "karaoke", "clean", "minimal"}, "position": {"top", "middle", "lower", "bottom"}}
STYLE_KEYS = {"font", "size", "color", "highlight", "outline", "outline_width", "uppercase", "words", "box", "pop", "bold", "shadow"}


def validate_render_settings(rs: dict) -> dict:
    out = {}
    for k, v in rs.items():
        if k in RENDER_KEYS:
            if v not in RENDER_KEYS[k]:
                raise HTTPException(422, f"Invalid {k}")
            out[k] = v
        elif k in ("captions", "remove_pauses", "zoom", "hook"):
            out[k] = bool(v)
        elif k == "hook_text":
            out[k] = (str(v).strip()[:100] or None) if v is not None else None
        elif k == "crop_x":
            out[k] = None if v is None else max(0.0, min(1.0, float(v)))
        elif k == "style":
            style = {}
            for sk, sv in (v or {}).items():
                if sk not in STYLE_KEYS:
                    continue
                if sk in ("color", "highlight", "outline") and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(sv)):
                    raise HTTPException(422, f"Invalid color for {sk}")
                if sk == "font" and not re.fullmatch(r"[\w .-]{1,60}", str(sv)):
                    raise HTTPException(422, "Invalid font name")
                if sk in ("size", "outline_width", "words", "shadow"):
                    sv = max(0, min({"size": 200, "outline_width": 20, "words": 12, "shadow": 10}[sk], int(sv)))
                    if sk in ("size", "words"):
                        sv = max(sv, 1 if sk == "words" else 16)
                if sk in ("uppercase", "box", "pop", "bold"):
                    sv = bool(sv)
                style[sk] = sv
            out[k] = style
    return out


@router.patch("/clips/{cid}")
def update_clip(cid: int, body: ClipUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_clip(db, user, cid)
    changes = body.model_dump(exclude_unset=True)
    visual = False
    if "start" in changes or "end" in changes:
        s, e = changes.get("start", c.start), changes.get("end", c.end)
        if s is None or e is None or not (0 <= s < e <= (c.project.duration or e)) or e - s < 1 or e - s > 600:
            raise HTTPException(422, "Clip must be 1 s to 10 min long and inside the video.")
        if (s, e) != (c.start, c.end):
            c.start, c.end = round(s, 2), round(e, 2)
            c.caption_overrides = []  # timings no longer match
            visual = True
    if changes.get("review_status"):
        c.review_status = changes["review_status"]
    if "title" in changes and changes["title"] is not None:
        c.title, c.title_edited = changes["title"].strip(), True
    if "description" in changes and changes["description"] is not None:
        c.description = changes["description"]
    if "hashtags" in changes and changes["hashtags"] is not None:
        tags = [re.sub(r"[^\w]", "", t.lstrip("#"))[:40] for t in changes["hashtags"]]
        c.hashtags = list(dict.fromkeys(t for t in tags if t))
    if changes.get("render_settings") is not None:
        new = {**(c.render_settings or {}), **validate_render_settings(changes["render_settings"])}
        for k in ("_rendered_layout", "_rendered_duration", "_cuts"):
            new.pop(k, None)
        if new != {k: v for k, v in (c.render_settings or {}).items() if not k.startswith("_")}:
            c.render_settings, visual = new, True
    if "caption_overrides" in changes and changes["caption_overrides"] is not None:
        lines = [ln for ln in changes["caption_overrides"] if ln["end"] > ln["start"] and ln["text"].strip()]
        if lines != c.caption_overrides:
            c.caption_overrides, visual = lines, True
    if visual and c.render_status == "done":
        c.render_status = "outdated"  # file on disk no longer matches the settings
    db.commit()
    return clip_out(c, db)


@router.get("/clips/{cid}/captions")
def clip_captions(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .pipeline import caption_lines, clip_words, render_settings
    c = owned_clip(db, user, cid)
    if c.caption_overrides:
        return {"edited": True, "lines": c.caption_overrides}
    t = db.scalar(select(Transcript).where(Transcript.project_id == c.project_id))
    per = int(render_settings(c, user.settings)["style"]["words"])
    lines = caption_lines(clip_words(t.segments if t else [], c.start, c.end), per)
    return {"edited": False, "lines": [{"start": ln["start"], "end": ln["end"], "text": ln["text"]} for ln in lines]}


@router.post("/clips/{cid}/render", status_code=202)
def render_clip(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_clip(db, user, cid)
    j = enqueue(db, c.project_id, "render", clip_id=c.id)
    c.render_status = "queued"
    db.commit()
    return job_out(j)


class Bulk(BaseModel):
    clip_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/clips/render", status_code=202)
def render_many(body: Bulk, user: User = Depends(current_user), db: Session = Depends(get_db)):
    queued = []
    for cid in body.clip_ids:
        c = owned_clip(db, user, cid)
        try:
            enqueue(db, c.project_id, "render", clip_id=c.id)
            c.render_status = "queued"
            db.commit()
            queued.append(cid)
        except HTTPException:
            pass  # already rendering
    return {"queued": queued}


class MetaIn(BaseModel):
    fields: list[str] = ["titles", "description", "hashtags"]


@router.post("/clips/{cid}/metadata")
def regenerate_metadata(cid: int, body: MetaIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .pipeline import apply_metadata
    c = owned_clip(db, user, cid)
    tr = db.scalar(select(Transcript).where(Transcript.project_id == c.project_id))
    apply_metadata(c, llm.generate_metadata(c.transcript_excerpt, (user.settings or {}).get("metadata") or {},
                                            tr.language if tr else None), set(body.fields))
    db.commit()
    return clip_out(c, db)


@router.post("/projects/{pid}/metadata", status_code=202)
def regenerate_project_metadata(pid: int, body: MetaIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    return job_out(enqueue(db, p.id, "metadata", {"fields": body.fields}))


@router.delete("/clips/{cid}", status_code=204)
def delete_clip(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_clip(db, user, cid)
    d = project_dir(c.project_id) / "clips"
    for f in (f"{c.id}.mp4", f"{c.id}.jpg", f"{c.id}.ass", f"{c.id}.tmp.mp4"):
        (d / f).unlink(missing_ok=True)
    db.delete(c)
    db.commit()


def _clip_file(c: Clip) -> Path:
    if not c.render_path:
        raise HTTPException(404, "This clip hasn't been rendered yet")
    f = project_dir(c.project_id) / c.render_path
    if not f.exists():
        raise HTTPException(404, "Rendered file is missing; render again")
    return f


def _slug(s: str) -> str:
    return re.sub(r"[^\w-]+", "-", s).strip("-")[:60] or "clip"


@router.get("/clips/{cid}/video")
def clip_video(cid: int, download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_clip(db, user, cid)
    return FileResponse(_clip_file(c), media_type="video/mp4",
                        filename=f"{_slug(c.title)}-{c.id}.mp4" if download else None)


@router.get("/clips/{cid}/thumbnail")
def clip_thumb(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_clip(db, user, cid)
    f = project_dir(c.project_id) / (c.thumbnail_path or "")
    if not c.thumbnail_path or not f.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(f, media_type="image/jpeg")


@router.post("/clips/download")
def download_many(body: Bulk, bg: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    files = []
    for cid in body.clip_ids:
        c = owned_clip(db, user, cid)
        if c.render_path and (project_dir(c.project_id) / c.render_path).exists():
            files.append((project_dir(c.project_id) / c.render_path, f"{_slug(c.title)}-{c.id}.mp4"))
    if not files:
        raise HTTPException(404, "None of the selected clips have been rendered")
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=config.DATA_DIR)
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:  # mp4 doesn't compress; store is fast
        for path, name in files:
            z.write(path, name)
    bg.add_task(os.unlink, tmp)
    return FileResponse(tmp, media_type="application/zip", filename="viralclip-clips.zip")


# ---------- jobs ----------

def owned_job(db: Session, user: User, jid: int) -> Job:
    j = db.get(Job, jid)
    if not j or j.project.user_id != user.id:
        raise HTTPException(404, "Job not found")
    return j


@router.get("/jobs")
def list_jobs(active: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Job).join(Project).where(Project.user_id == user.id)
    if active:
        q = q.where(Job.status.in_(ACTIVE))
    return [job_out(j) for j in db.scalars(q.order_by(Job.id.desc()).limit(100))]


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = owned_job(db, user, jid)
    if j.status == "queued":
        j.status = "cancelled"
        if j.clip and j.clip.render_status == "queued":
            j.clip.render_status = "none" if not j.clip.render_path else "outdated"
    elif j.status == "running":
        j.status = "cancel_requested"
    else:
        raise HTTPException(409, "This job isn't running")
    db.commit()
    return job_out(j)


@router.post("/jobs/{jid}/retry", status_code=202)
def retry_job(jid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = owned_job(db, user, jid)
    if j.status not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    if j.job_type not in ("analyze", "render", "metadata"):
        raise HTTPException(409, "Retry this from its own page")
    new = enqueue(db, j.project_id, j.job_type, j.params, j.clip_id)
    if j.clip_id:
        db.get(Clip, j.clip_id).render_status = "queued"
        db.commit()
    return job_out(new)


# ---------- settings / system ----------

class SettingsIn(BaseModel):
    settings: dict


ALLOWED_SETTINGS = {"defaults", "render", "metadata", "whisper_model", "youtube"}


@router.get("/settings")
def get_settings(user: User = Depends(current_user)):
    return user.settings or {}


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .pipeline import WHISPER_MODELS
    s = {k: v for k, v in body.settings.items() if k in ALLOWED_SETTINGS}
    if "render" in s:
        s["render"] = validate_render_settings(s["render"] or {})
    if s.get("whisper_model") not in (None, *WHISPER_MODELS):
        raise HTTPException(422, "Unknown transcription model")
    for k in ("defaults", "metadata", "youtube"):
        if k in s and not isinstance(s[k], dict):
            raise HTTPException(422, f"{k} must be an object")
    import json
    if len(json.dumps(s)) > 20000:
        raise HTTPException(413, "Settings too large")
    user.settings = {**(user.settings or {}), **s}
    db.commit()
    return user.settings


@router.get("/system")
def system(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .pipeline import CAPTION_PRESETS, WHISPER_MODELS
    import subprocess
    cache = Path(os.getenv("WHISPER_CACHE_DIR") or Path.home() / ".cache" / "huggingface" / "hub")
    downloaded = [m for m in WHISPER_MODELS if any(cache.glob(f"*faster-whisper-{m}*"))]
    try:
        fonts = sorted({f.split(",")[0].strip() for f in subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout.splitlines() if f.strip()})
    except (OSError, subprocess.TimeoutExpired):
        fonts = []
    du = shutil.disk_usage(config.DATA_DIR)
    used = sum(f.stat().st_size for f in (config.DATA_DIR / "projects").rglob("*") if f.is_file()) \
        if (config.DATA_DIR / "projects").exists() else 0
    workers_alive = db.scalar(select(Job.id).where(Job.status == "running").limit(1)) is not None
    return {
        "whisper_models": WHISPER_MODELS, "whisper_downloaded": downloaded,
        "whisper_default": os.getenv("WHISPER_MODEL", "small"), "whisper_device": os.getenv("WHISPER_DEVICE", "auto"),
        "llm_configured": llm.configured(), "llm_model": os.getenv("LLM_MODEL") if llm.configured() else None,
        "caption_presets": CAPTION_PRESETS, "fonts": fonts[:200],
        "disk_free": du.free, "disk_total": du.total, "storage_used": used, "data_dir_configured": True,
        "job_running": workers_alive,
    }
