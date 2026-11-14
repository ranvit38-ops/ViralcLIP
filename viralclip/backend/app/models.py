from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


def _id():
    return mapped_column(Integer, primary_key=True)


def _created():
    return mapped_column(DateTime(timezone=True), default=now, index=True)


def _fk(target):
    return mapped_column(ForeignKey(target, ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = _id()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of cookie value
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    name: Mapped[str] = mapped_column(String(200))
    # uploading -> uploaded -> processing -> ready | failed
    status: Mapped[str] = mapped_column(String(20), default="uploading", index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # clip-generation settings

    # upload (resumable, offset-based)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    upload_size: Mapped[int | None] = mapped_column(Integer)
    bytes_received: Mapped[int] = mapped_column(Integer, default=0)

    # media metadata from ffprobe
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(50))
    audio_codec: Mapped[str | None] = mapped_column(String(50))
    has_audio: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan", passive_deletes=True)
    clips: Mapped[list["Clip"]] = relationship(back_populates="project", cascade="all, delete-orphan", passive_deletes=True)


class Transcript(Base):
    __tablename__ = "transcripts"
    id: Mapped[int] = _id()
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    language: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    # [{start, end, text, words: [{start, end, word, probability}]}]
    segments: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _created()


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = _id()
    project_id: Mapped[int] = _fk("projects.id")
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    transcript_excerpt: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    # suggested | approved | rejected
    review_status: Mapped[str] = mapped_column(String(20), default="suggested", index=True)

    title: Mapped[str] = mapped_column(String(200), default="")
    title_options: Mapped[list] = mapped_column(JSON, default=list)
    title_edited: Mapped[bool] = mapped_column(Boolean, default=False)  # never auto-overwrite
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)

    # render: none | queued | rendering | done | failed
    render_status: Mapped[str] = mapped_column(String(20), default="none", index=True)
    render_settings: Mapped[dict] = mapped_column(JSON, default=dict)  # aspect, captions, crop
    caption_overrides: Mapped[list] = mapped_column(JSON, default=list)  # user-edited caption lines
    render_path: Mapped[str | None] = mapped_column(String(500))
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = _created()
    project: Mapped[Project] = relationship(back_populates="clips")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = _id()
    project_id: Mapped[int] = _fk("projects.id")
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[str] = mapped_column(String(30))  # analyze | render | (youtube jobs)
    # queued | running | cancel_requested | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)  # 0..1, only from real work done
    message: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # e.g. wait for quota reset
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project: Mapped[Project] = relationship(back_populates="jobs")


# ---------- YouTube ----------

class YouTubeAccount(Base):
    __tablename__ = "youtube_accounts"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    channel_id: Mapped[str] = mapped_column(String(64))
    channel_title: Mapped[str] = mapped_column(String(200), default="")
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    encrypted_tokens: Mapped[str] = mapped_column(Text)  # Fernet(JSON{access_token, refresh_token})
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | reauth_required
    created_at: Mapped[datetime] = _created()


class OAuthState(Base):
    __tablename__ = "oauth_states"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class YouTubeUpload(Base):
    __tablename__ = "youtube_uploads"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    clip_id: Mapped[int] = _fk("clips.id")
    # SET NULL: disconnecting a channel keeps the history (and video links) of past uploads.
    account_id: Mapped[int | None] = mapped_column(ForeignKey("youtube_accounts.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    # queued | uploading | paused | uploaded | failed | cancelled | auth_required | quota_exceeded
    upload_status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    privacy: Mapped[str] = mapped_column(String(10), default="private")
    category_id: Mapped[str] = mapped_column(String(4), default="22")
    session_uri: Mapped[str | None] = mapped_column(Text)  # resumable session; reused on retry
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int | None] = mapped_column(Integer)
    quota_day: Mapped[str | None] = mapped_column(String(10), index=True)  # Pacific date the insert started
    transient_failures: Mapped[int] = mapped_column(Integer, default=0)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # UTC
    timezone: Mapped[str | None] = mapped_column(String(64))  # zone the user picked, for display
    # none | scheduled | failed | published (independent of upload_status)
    schedule_status: Mapped[str] = mapped_column(String(12), default="none", index=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutoPublishRule(Base):
    """One rule per channel: scheduled public publishing. Only ever schedules clips the user approved."""
    __tablename__ = "autopublish_rules"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users.id")
    account_id: Mapped[int] = mapped_column(ForeignKey("youtube_accounts.id", ondelete="CASCADE"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    time_of_day: Mapped[str] = mapped_column(String(5), default="18:00")  # HH:MM local
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    clips_per_day: Mapped[int] = mapped_column(Integer, default=1)
    project_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = any project
    created_at: Mapped[datetime] = _created()
