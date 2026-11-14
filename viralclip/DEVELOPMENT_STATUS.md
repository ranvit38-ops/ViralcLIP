# Development status

_Last updated: Phase 1 complete, Phase 2 starting._

## Working and tested

- **Auth:** register, log in, log out, and `me`. Sessions are server-side, stored in an httpOnly cookie, and passwords are hashed with scrypt.
- **Projects:** create, list, search (client-side), rename, archive and unarchive, and delete. Deleting removes the files. Projects persist in SQLite.
- **Resumable chunked upload:** offset-based, so a retried or duplicated chunk is refused with 409 and the client resyncs. Uploads resume after a page reload by re-selecting the same file. Cancelling deletes the project.
- **Validation:**
  - Extension allow-list, a 20 GB size limit, and a free-disk check before accepting a file.
  - ffprobe container check, plus a frame-decode test to catch corrupted files or unsupported codecs.
  - A missing audio track is flagged.
- **Metadata:** duration, resolution (rotation-aware), fps, codecs, and audio presence, plus a thumbnail.
- **Preview:** in-browser playback of the source video with Range support.
- **Dashboard:** project, clip, awaiting-review, active-job and failed-job counts, all from real database queries.
- **Per-user isolation:** another user's IDs return 404 on every endpoint.
- **UI:** works at desktop and phone widths.

## Test results

- `backend/tests`: **10 passed** (`cd backend && .venv/bin/python -m pytest -q`).
- Frontend: `eslint` and `tsc` are clean, and `next build` succeeds.
- Manual end-to-end in headless Chromium: register, upload a 29 MB 1080p file in 8 MB chunks through the Next proxy, then the metadata appears correctly.

## Not implemented yet

| Phase | Item |
|---|---|
| 2 | Worker process, audio extraction, faster-whisper transcription, transcript viewer, clip candidate selection and scoring, review UI |
| 3 | ffmpeg rendering, 9:16/1:1/4:5/16:9, face-tracked reframe, burned captions, downloads |
| 4 | Editor (trim, crop, caption styles), AI titles, descriptions and hashtags |
| 5–7 | YouTube OAuth, uploads, quota-aware queue, scheduling, calendar, bulk scheduling, auto-publish rules |
| 8 | Alembic migrations, Docker Compose, Postgres verification, storage cleanup, settings page |
| new | Watch-folder auto-import (for OBS recordings) |

The sidebar only shows sections that work. Clips, YouTube, Calendar, Queue and Settings will appear as they're built.

## Known gaps and bugs

- There are no schema migrations yet (`create_all`). Add Alembic before anyone has data worth keeping.
- The upload offset check is atomic per process. Run the API with **one** worker process until a file lock is added.
- Duplicate *files* (the same video uploaded twice as two projects) are not detected.
- There's no light theme yet.

## Required external configuration

- `ffmpeg`/`ffprobe` on PATH.
- Phase 2+: network access to huggingface.co for Whisper models on first run. An optional OpenAI-compatible LLM endpoint (e.g. Ollama).
- Phase 5+: a Google Cloud project with YouTube Data API v3 and an OAuth web client (setup docs will come with that phase).

## Next tasks

1. Build the worker and the analyze job (faster-whisper with word timestamps).
2. Candidate selection: speech-based for videos with commentary, and loudness/scene-based for gameplay with no speech.
3. A transparent scoring breakdown, then the clip review UI.
