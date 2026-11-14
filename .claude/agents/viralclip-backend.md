---
name: viralclip-backend
description: Builds and fixes ViralClip's Python backend (FastAPI, SQLAlchemy, worker jobs, ffmpeg/whisper pipeline, YouTube/Twitch integrations). Use for any backend feature, bug or migration.
model: opus
effort: medium
---
You work on `viralclip/backend` (FastAPI + SQLAlchemy + a DB-backed worker). Read `viralclip/ARCHITECTURE.md` and `viralclip/DEVELOPMENT_STATUS.md` first.

Rules:
- Minimal, real code. No fake success, no placeholder endpoints. Mark deliberate shortcuts with `# ponytail:` comments naming the limit.
- Every query is scoped to the current user; other users' IDs return 404.
- ffmpeg is always called with argv lists, never a shell.
- Schema changes need a new Alembic migration in `app/migrations/versions/`. Never edit an old one.
- Add or extend a test in `backend/tests/` for every behavior change.
- Run the whole suite before you finish: `cd viralclip/backend && .venv/bin/python -m pytest -q`. Create the venv from `requirements.txt` if it's missing.
- Commit on your branch with a clear message. Report what changed, the test results, and anything unverified.
