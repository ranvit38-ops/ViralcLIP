# ViralClip

A self-hosted tool that turns long videos into captioned vertical clips, writes their titles, descriptions and hashtags, and uploads or schedules them on YouTube.
No subscriptions, credits or watermarks. Everything runs on your machine.

- Status of every feature: `DEVELOPMENT_STATUS.md`
- Design: `ARCHITECTURE.md`
- YouTube setup: `docs/YOUTUBE_SETUP.md`

**You are responsible for having the rights to everything you upload, edit and publish.** YouTube link import only accepts videos from channels you've connected.

## How it works

1. **Upload** a long video (MP4/MOV/WEBM, resumable), or import one of your own YouTube videos by link.
2. **Generate clips**:
   - faster-whisper transcribes the audio with word-level timestamps.
   - Candidate clips are cut on sentence boundaries and scored. The score has a visible breakdown: hook, standalone, completeness, value, pacing and vocal energy.
   - Footage with little speech, such as gameplay, falls back to loudness peaks.
   - An optional local LLM re-ranks the candidates and writes titles, descriptions and hashtags.
3. **Review**: approve or reject, trim, change framing and caption style, and edit captions and metadata.
4. **Render**:
   - Talking heads get a face-tracked 9:16 crop. Gameplay and screen recordings get the full frame on a blurred background.
   - Word-by-word highlighted captions are burned in.
   - Audio is normalized to -14 LUFS, and video is encoded as H.264 CRF 18.
5. **Publish**: connect one or more YouTube channels. Then upload now, schedule, bulk-schedule (daily, every other day, weekly), or turn on an auto-publish rule. Rules only ever take clips you approved.

## Quick start (Docker)

```bash
cp .env.example .env        # optional: fill in Google OAuth for YouTube, an LLM endpoint
docker compose up --build   # http://localhost:3000
```

The first clip generation downloads the Whisper model (~500 MB for `small`) into the `models` volume.

To add a local LLM for better titles:

```bash
docker compose --profile llm up -d --build
docker compose exec ollama ollama pull qwen2.5:7b-instruct
# in .env:
#   LLM_BASE_URL=http://ollama:11434/v1
#   LLM_MODEL=qwen2.5:7b-instruct
```

Then `docker compose up -d` again.

## Quick start (without Docker)

Needs Python 3.11+, Node 20+, and ffmpeg 6+ on PATH. Set the environment variables first, e.g. `set -a; . ../.env; set +a`.

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000          # API: keep it to ONE process
.venv/bin/python -m app.worker                       # in a 2nd terminal; start more for parallel jobs
```

```bash
cd frontend && npm install && npm run build && npx next start -p 3000
```

PostgreSQL works too: `DATABASE_URL=postgresql+psycopg://user:pass@host/db`. The test suite passes on SQLite and Postgres 16.

## AI models and hardware

| Piece | Default | CPU (4 cores) | Notes |
|---|---|---|---|
| Transcription | faster-whisper `small` | ~0.2× real time, so a 2 h video takes ~25 min | `tiny` is ~2× faster and less accurate. `large-v3` is best but needs an NVIDIA GPU to be practical. Set `WHISPER_DEVICE=cuda` (auto-detected). |
| Clip scoring | built-in heuristic | instant | Transparent; always on. |
| Titles / re-ranking | none, or any OpenAI-compatible LLM | 3B model: ~40 s per clip | Use a **7B+ model** (e.g. `qwen2.5:7b-instruct` via Ollama). 3B models invent details and drift languages; the app filters the worst of it, but you must review. |
| Rendering | ffmpeg libx264 `medium` | ~1× real time for 1080×1920 (measured: 20 s clip in 16 s) | `RENDER_PRESET=fast` for speed, `slow` for size. |

You don't need a GPU; everything has a CPU path.
RAM: ~2 GB for `small`, ~5 GB for a 7B LLM, plus about 0.5 GB per hour of source video while it's being transcribed.

## Costs

The software is free, but these aren't:
- Running it locally costs your electricity and disk. Sources plus renders take roughly 1.5× the source size.
- Hosting it on a server means paying for the VM, storage and bandwidth. GPU servers are expensive, so expect CPU-speed transcription on cheap hosts.
- YouTube API usage is free, but quota-limited: about 6 uploads/day per Google Cloud project by default.

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

The end-to-end test needs espeak-ng and runs real Whisper `tiny`.

```bash
cd frontend && npx tsc --noEmit && npx eslint . && npm run build
```

There's also a browser end-to-end script; see `frontend/e2e/`.
