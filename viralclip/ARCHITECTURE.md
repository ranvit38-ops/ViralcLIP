# ViralClip architecture

The plan, kept short. `DEVELOPMENT_STATUS.md` says what actually exists today.

## 1. Architecture

```
Browser ──> Next.js (UI, :3000) ──/api rewrite──> FastAPI (:8000) ──> SQLite / Postgres
                                                     │
                                                     └─ jobs table <── worker process(es)
                                                                        ffmpeg, faster-whisper,
                                                                        OpenCV, LLM (HTTP)
```

- The browser only talks to the Next.js origin. `/api/*` is proxied to FastAPI, so the session cookie is first-party and httpOnly, and there's no CORS.
- **Background jobs live in a database table, not Redis/Celery.** A worker claims a job with an atomic `UPDATE … WHERE status='queued'`, updates `progress` and `heartbeat_at` as it does real work, and checks for cancellation. Jobs survive restarts and closed tabs, and a stale heartbeat gets the job requeued. That's one less service to install. Switch to Celery only if you need more than one machine.
- The original upload, renders and thumbnails live under `VC_DATA_DIR/projects/<id>/`. Paths are built from database IDs, never from user input.

## 2. Folder structure

```
viralclip/
  backend/app/      config, db, models, auth, media (ffmpeg), projects (+uploads), …
  backend/tests/    pytest, generates its own fixture videos with ffmpeg
  frontend/app/     Next.js App Router pages; (app)/ = authenticated shell
  frontend/lib/     api client, resumable uploader
  frontend/components/  ui primitives, shell
```

## 3. Dependencies

Backend: FastAPI, SQLAlchemy 2, uvicorn, faster-whisper, opencv-python-headless, and the system tools ffmpeg/ffprobe.
Frontend: Next.js 16, React 19, Tailwind 4, lucide-react.
I didn't use shadcn/ui: its registry was unreachable here, and five hand-written primitives in `components/ui.tsx` cover the need.

## 4. Database

`users`, `auth_sessions`, `projects` (upload and media metadata), `transcripts` (segments and word timestamps in JSON), `clips` (boundaries, score with breakdown, review status, metadata, render settings), `jobs`.
YouTube tables (`youtube_accounts` with Fernet-encrypted tokens, `youtube_uploads`, `scheduled_posts`) arrive in Phase 5.
FKs are `ON DELETE CASCADE`, and every query is scoped by `user_id`. Another user's resources return 404.

## 5. Video pipeline

upload (resumable) → ffprobe validate → thumbnail → **analyze job**: extract 16 kHz mono WAV → faster-whisper (word timestamps, VAD) → candidate windows snapped to sentence boundaries → score (LLM, with heuristic fallback) → de-duplicate overlapping candidates → clips to review → **render job** per approved clip: ffmpeg cut → reframe (face track / center / facecam split) → ASS subtitles burned in → H.264/AAC MP4 → thumbnail.

**Gameplay without commentary has no speech,** so transcript-based selection finds nothing there. The analyzer will also score windows by audio loudness peaks and scene-cut density, so gameplay-only footage still gets candidates. They'll be weaker candidates, though, and that's the honest trade-off.

## 6. AI models

- Transcription: faster-whisper. `small` is the CPU default and `large-v3` is for GPU. Configurable, and models download from Hugging Face on first use.
- Clip scoring and metadata: any **OpenAI-compatible chat endpoint** (Ollama, llama.cpp server, LM Studio, vLLM), set via `LLM_BASE_URL`/`LLM_MODEL`. There's no proprietary lock-in. If no endpoint is configured, a transparent heuristic scorer is used and labeled as such.
- Faces: OpenCV's bundled Haar cascade (no download). Swap it for YuNet/MediaPipe if quality needs it.
- Diarization (pyannote) is deferred. It needs a Hugging Face token and gated model terms, and it's slow on CPU.

## 7. YouTube

OAuth 2.0 web flow (authorization code, `state` checked against a server-side value). Scope: `youtube.upload` plus `youtube.readonly` for the channel name.
Tokens are Fernet-encrypted at rest and never sent to the browser.
Uploads use resumable `videos.insert`, with the session URI persisted so a restart resumes instead of duplicating. Scheduling uses `status.privacyStatus=private` and `status.publishAt`.
Upload state and schedule state are tracked separately.

**Hard limits you can't engineer around:**
- Videos uploaded through an **unaudited** Google Cloud project are locked to *private* until the project passes Google's API compliance audit. Until then, scheduled public publishing won't happen.
- The default quota is 10,000 units/day, and an upload costs about 1,600 units, so that's roughly **6 uploads/day**. Check the current numbers in Google's docs.

## 8. Security

- scrypt password hashes and sha256-hashed session tokens.
- httpOnly + SameSite=Lax cookies, Secure when `APP_URL` is https.
- ffmpeg is always called with argv lists (no shell).
- Upload extension allow-list plus ffprobe container check.
- Paths are derived from database IDs.
- Generic 500s; details are logged server-side only.
- OAuth tokens are encrypted, and secrets come only from environment variables.

## 9. Major risks

- CPU-only transcription of a 2-hour video with `small` takes tens of minutes, and with `large-v3` it takes hours.
- Local LLM quality on small models is mediocre, so scores are a ranking aid, not a prediction.
- YouTube audit and quota (above).
- Copyright: in-game licensed music (for example, GTA radio) triggers Content ID claims.
- No Alembic migrations yet.

## 10. Implementation sequence

Phases 1–8, as in the brief. **Planned addition:** a watch-folder import (e.g. your OBS recordings folder) so new recordings are picked up and analyzed automatically.
