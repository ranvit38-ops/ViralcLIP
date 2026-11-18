# ViralClip

A self-hosted tool that turns long videos into captioned vertical clips, writes their titles, descriptions and hashtags, and uploads or schedules them on YouTube.
No subscriptions, credits or watermarks. Everything runs on your machine.

- Status of every feature: `DEVELOPMENT_STATUS.md`
- Design: `ARCHITECTURE.md`
- YouTube setup: `docs/YOUTUBE_SETUP.md`
- **Start here: `docs/SETUP_GUIDE.md`** (everything, step by step)
- Twitch setup: `docs/TWITCH_SETUP.md`

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
   - Talking heads get a face-tracked 9:16 crop with punch-in zooms.
   - Streams with a facecam get a split screen: facecam on top, gameplay below.
   - Gameplay and screen recordings get the full frame on a blurred background.
   - Jump cuts remove dead air, and a hook title shows for the first 3 seconds.
   - Word-by-word highlighted captions are burned in.
   - Audio is normalized to -14 LUFS, and video is encoded as H.264 CRF 18.
5. **Automate** (optional): connect your Twitch account(s) or YouTube channel(s) in **Automation**.
   - New streams and videos are imported, clipped, optionally auto-approved, rendered, and handed to the auto-publish rule.
   - Only your own connected channels can be imported. See `docs/TWITCH_SETUP.md`.
   - **Link queue** (`POST /api/queue`, up to 50 links): paste links to your own YouTube, Twitch or TikTok videos once. While automation is on, the next link is imported only when nothing is importing, analyzing or rendering and fewer than `queue_buffer_days` (default 3) days of posts are lined up on your fullest account (posts/day from your auto-publish rules, default 6). Links to videos that aren't yours are skipped, never downloaded. `GET /api/automation/status` is the weekly glance: queue counts, scheduled posts, days of runway, recent failures, and whether to add more links.
6. **Publish**: connect one or more YouTube channels. Then upload now, schedule, bulk-schedule (daily, every other day, weekly), or turn on an auto-publish rule. Rules only ever take clips you approved.

## Quick start (no Docker)

**1. Install the three tools once**, then close and reopen your terminal:
- Windows (PowerShell): `winget install Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg`
- macOS: `brew install python@3.12 node ffmpeg`
- Linux: `sudo apt install python3 python3-venv ffmpeg`, plus Node 20+ from nodejs.org

**2. Start it:**
- Windows: double-click `start.bat`
- macOS/Linux: `./start.sh`
- Any system: `python run.py`

The first run sets everything up (5–15 min). Later runs start in seconds. Your browser opens at http://localhost:3000.
To automate without keeping a window open, run `autostart.ps1` once (Windows). ViralClip then runs hidden in the background from every login, and restarts crashed parts. Stop it with `autostart.ps1 -Remove` or `python run.py --stop`. Otherwise Ctrl+C in the window stops everything.

Your keys go in `viralclip/.env`, which the first run creates from `.env.example`. Restart after editing it.
Data and logs are in `backend/data/`.

Options:
- `python run.py --port 3001` to use a different port
- `--workers 3` for more parallel renders
- `--rebuild` after pulling new code if something looks stale

## Alternative: Docker

```bash
cp .env.example .env
docker compose up --build   # http://localhost:3000
```

Optional local LLM in Docker: `docker compose --profile llm up -d --build`, then `docker compose exec ollama ollama pull qwen2.5:7b-instruct`, and set `LLM_BASE_URL=http://ollama:11434/v1` and `LLM_MODEL=qwen2.5:7b-instruct` in `.env`.

Without Docker, install Ollama from ollama.com, run `ollama pull qwen2.5:7b-instruct`, and set `LLM_BASE_URL=http://localhost:11434/v1` and `LLM_MODEL=qwen2.5:7b-instruct` in `.env`.

## AI models and hardware

| Piece | Default | CPU (4 cores) | Notes |
|---|---|---|---|
| Transcription | faster-whisper `small`, batched | ~0.1× real time, so a 2 h stream takes ~12 min (measured: 10.5 min in 62 s) | `tiny` is ~2× faster and less accurate. `large-v3` is best but needs an NVIDIA GPU to be practical. Set `WHISPER_DEVICE=cuda` (auto-detected). |
| Clip scoring | built-in heuristic | instant | Transparent; always on. |
| Titles / re-ranking | none, or any OpenAI-compatible LLM | 3B model: ~25 s per clip (8 clips in ~200 s) | See "Fast AI setup" below. Small models invent details and drift languages; the app filters the worst of it, but you must review. |
| Rendering | ffmpeg libx264 `veryfast` CRF 18 | ~0.4–0.5× clip length (measured: 45 s clip in 18 s) | Two workers by default, so renders don't wait behind transcription. |

You don't need a GPU; everything has a CPU path.
RAM: ~2 GB for `small`, ~5 GB for a 7B LLM, plus about 0.5 GB per hour of source video while it's being transcribed.

### Fast AI setup

The AI step re-ranks the shortlisted moments, then writes titles, descriptions and hashtags for several clips per request (`LLM_BATCH`, default 4). Measured on 4 CPU cores (llama.cpp, q4_k_m), 8 clips from a 9-minute talk, re-rank plus metadata:

| Model | Before | Now |
|---|---|---|
| `qwen2.5:3b-instruct` | 501 s (63 s/clip) | 196 s (25 s/clip) |
| `qwen2.5:1.5b-instruct` | 291 s (36 s/clip) | 186 s (23 s/clip) |

- **CPU only:** `ollama pull qwen2.5:3b-instruct`. It gives the best speed for the quality on CPU; 1.5B is faster, but its titles are noticeably weaker.
- **NVIDIA/Apple GPU:** `ollama pull qwen2.5:7b-instruct`. A GPU makes the AI step roughly 10× faster (typical for llama.cpp; not measured on this machine), so a 7B model, which writes the best titles, fits comfortably.
- **No LLM at all:** leave `LLM_BASE_URL` empty. Titles come instantly from the clip's own strongest lines (labeled "Basic").

Knobs, all optional: `LLM_TIMEOUT` (per request, default 90 s), `LLM_BUDGET` (whole AI step per analysis, default 300 s; clips past it get basic metadata labeled `+timeout` and a background metadata job retries them), `LLM_CONCURRENCY` (default 1; set 2 only if the server has parallel slots, e.g. Ollama with `OLLAMA_NUM_PARALLEL=2`), `LLM_BATCH`.

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
