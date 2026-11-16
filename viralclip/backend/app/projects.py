import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, media
from .auth import current_user
from .db import get_db
from .models import Clip, Job, Project, User

router = APIRouter(prefix="/api", tags=["projects"])


# ---------- helpers ----------

def project_dir(pid: int) -> Path:
    d = config.DATA_DIR / "projects" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_path(p: Project) -> Path:
    """Final source file. The extension is from our allow-list, never raw user input."""
    return project_dir(p.id) / f"source{Path(p.source_filename or '').suffix.lower()}"


def part_path(p: Project) -> Path:
    return project_dir(p.id) / "source.part"


def owned_project(db: Session, user: User, pid: int) -> Project:
    p = db.get(Project, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Project not found")  # 404, not 403: don't reveal it exists
    return p


def job_out(j: Job) -> dict:
    return {k: getattr(j, k) for k in (
        "id", "project_id", "job_type", "status", "progress", "message", "error",
        "created_at", "started_at", "finished_at")}


def project_out(p: Project, db: Session | None = None) -> dict:
    d = {k: getattr(p, k) for k in (
        "id", "name", "status", "archived", "error", "settings", "source_filename", "upload_size",
        "duration", "width", "height", "fps", "video_codec", "audio_codec", "has_audio",
        "created_at", "updated_at")}
    d["bytes_received"] = part_path(p).stat().st_size if p.status == "uploading" and part_path(p).exists() else (
        p.upload_size if p.status != "uploading" else 0)
    d["thumbnail_url"] = f"/api/projects/{p.id}/thumbnail" if (project_dir(p.id) / "thumb.jpg").exists() else None
    if db is not None:
        d["clip_count"] = db.scalar(select(func.count()).select_from(Clip).where(Clip.project_id == p.id))
    return d


# ---------- projects ----------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ext = Path(body.filename).suffix.lower()
    if ext not in media.ALLOWED_EXTS:
        raise HTTPException(422, f"Unsupported file type '{ext or 'none'}'. Use MP4, MOV or WEBM.")
    if body.size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is larger than the {config.MAX_UPLOAD_BYTES / 1024**3:.0f} GB limit.")
    free = shutil.disk_usage(config.DATA_DIR).free
    if free - body.size < config.MIN_FREE_BYTES:
        raise HTTPException(507, f"Not enough disk space: {free / 1024**3:.1f} GB free.")
    from .automation import settings_of
    # With automation on, an uploaded VOD runs the same chain as an imported one (analyze → approve → render → publish).
    p = Project(user_id=user.id, name=body.name.strip(), source_filename=Path(body.filename).name[:255],
                upload_size=body.size, status="uploading",
                settings={"automation": {"auto": True}} if settings_of(user)["enabled"] else {})
    db.add(p)
    db.commit()
    part_path(p).touch()
    return project_out(p, db)


@router.get("/projects")
def list_projects(archived: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.user_id == user.id, Project.archived == archived)
                      .order_by(Project.created_at.desc())).all()
    return [project_out(p, db) for p in rows]


@router.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    out = project_out(p, db)
    out["jobs"] = [job_out(j) for j in sorted(p.jobs, key=lambda j: j.id, reverse=True)]
    return out


@router.patch("/projects/{pid}")
def update_project(pid: int, body: ProjectUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    if body.name is not None:
        p.name = body.name.strip()
    if body.archived is not None:
        p.archived = body.archived
    db.commit()
    return project_out(p, db)


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    # Ask running jobs to stop; the worker checks this and bails out.
    for j in p.jobs:
        if j.status in ("queued", "running"):
            j.status = "cancel_requested"
    db.delete(p)
    db.commit()
    shutil.rmtree(config.DATA_DIR / "projects" / str(pid), ignore_errors=True)


@router.get("/projects/{pid}/thumbnail")
def project_thumbnail(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    f = project_dir(p.id) / "thumb.jpg"
    if not f.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(f, media_type="image/jpeg")


@router.get("/projects/{pid}/source")
def project_source(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Original video, streamed with Range support for the in-browser preview."""
    p = owned_project(db, user, pid)
    if p.status == "uploading" or not source_path(p).exists():
        raise HTTPException(404, "Video not available")
    return FileResponse(source_path(p), filename=p.source_filename)


# ---------- resumable upload ----------
# Protocol: GET /upload -> current offset; PUT /upload?offset=N (raw bytes) appends a chunk only
# if N equals the bytes already on disk, so retried or duplicated chunks can never corrupt the file.
# The file on disk is the source of truth, which also survives server restarts.

@router.get("/projects/{pid}/upload")
def upload_status(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    return {"status": p.status, "offset": project_out(p)["bytes_received"], "size": p.upload_size}


@router.put("/projects/{pid}/upload")
async def upload_chunk(pid: int, request: Request, offset: int = Query(ge=0),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    if p.status != "uploading":
        raise HTTPException(409, "Upload already finished")
    length = int(request.headers.get("content-length") or 0)
    if not 0 < length <= config.MAX_CHUNK_BYTES:
        raise HTTPException(413, "Chunk too large or empty")
    data = await request.body()
    # ponytail: no await between the offset check and the write, so this is atomic per process.
    # Run the API with a single worker process (default); use a file lock if you scale it out.
    part = part_path(p)
    have = part.stat().st_size if part.exists() else 0
    if offset != have:
        raise HTTPException(409, detail={"message": "Offset mismatch", "offset": have})
    if have + len(data) > p.upload_size:
        raise HTTPException(400, "Chunk exceeds the declared file size")
    try:
        with open(part, "ab") as f:
            f.write(data)
    except OSError:
        raise HTTPException(507, "The server ran out of disk space while saving the upload.")
    return {"offset": have + len(data), "size": p.upload_size}


@router.post("/projects/{pid}/upload/complete")
def upload_complete(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_project(db, user, pid)
    if p.status != "uploading":
        return project_out(p, db)  # idempotent: double-submit returns the same result
    part = part_path(p)
    have = part.stat().st_size if part.exists() else 0
    if have != p.upload_size:
        raise HTTPException(409, detail={"message": "Upload incomplete", "offset": have})
    part.rename(source_path(p))
    try:
        finalize_source(db, p)
    except media.MediaError as e:
        raise HTTPException(422, str(e))
    return project_out(p, db)


def finalize_source(db: Session, p: Project) -> None:
    """Validate source_path(p), store its metadata and thumbnail. Shared by upload and link import.
    On a bad file: marks the project failed, deletes the file, commits, re-raises MediaError."""
    src = source_path(p)
    try:
        meta = media.probe(src)
        media.thumbnail(src, project_dir(p.id) / "thumb.jpg", at=min(meta["duration"] * 0.1, 5))
    except media.MediaError as e:
        p.status, p.error = "failed", str(e)
        src.unlink(missing_ok=True)
        db.commit()
        raise
    for k, v in meta.items():
        setattr(p, k, v)
    p.status, p.error = "uploaded", None
    if not meta["has_audio"]:
        p.error = "This video has no audio track, so it can't be transcribed or captioned."
    db.commit()


# ---------- dashboard ----------

@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    mine = select(Project.id).where(Project.user_id == user.id)

    def count(stmt):
        return db.scalar(select(func.count()).select_from(stmt.subquery()))

    jobs_q = select(Job).where(Job.project_id.in_(mine))
    return {
        "projects": count(select(Project.id).where(Project.user_id == user.id, Project.archived.is_(False))),
        "clips": count(select(Clip.id).where(Clip.project_id.in_(mine))),
        "clips_awaiting_review": count(select(Clip.id).where(Clip.project_id.in_(mine), Clip.review_status == "suggested")),
        "active_jobs": count(jobs_q.where(Job.status.in_(("queued", "running", "cancel_requested")))),
        "failed_jobs": count(jobs_q.where(Job.status == "failed")),
        "recent_jobs": [job_out(j) for j in db.scalars(jobs_q.order_by(Job.created_at.desc()).limit(10))],
        "recent_projects": [project_out(p) for p in db.scalars(
            select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc()).limit(5))],
    }
