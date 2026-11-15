# Development status

_Last updated: all planned phases are implemented. What's verified and what isn't is listed below._

## Working and verified

| Area | What works | How it was verified |
|---|---|---|
| Accounts | Register and login (scrypt), server-side sessions, failed-login throttle, per-user isolation (404 on other users' IDs) | pytest |
| Upload | Resumable 8 MB chunks, duplicate-chunk safe, resume after reload, cancel, size/disk/extension/codec/corruption checks | pytest + browser (29 MB 1080p) |
| Transcription | faster-whisper with word timestamps and real progress; models download on first use; CPU or GPU | Real Whisper tiny and small on synthesized speech |
| Clip finding | Sentence-bounded windows; transparent score (hook, standalone, completeness, value, pacing, energy) with reasons; no overlapping duplicates; transition and housekeeping lines avoided; loudness-peak fallback for no-speech footage | pytest on a real transcript |
| AI (optional) | Any OpenAI-compatible LLM re-ranks clips and writes 4 titles, a description and hashtags; filters placeholders, wrong-language output and stray punctuation; falls back to basic suggestions | Real Qwen2.5-3B via llama.cpp, plus pytest with mocks |
| Similar-Shorts research | Titles/hashtags learn style and hashtags from recent high-view Shorts (YouTube Data API v3 `search.list` + `videos.list`, no scraping) in analyze, the metadata job and per-clip regenerate; niche setting; 24 h DB cache; daily search budget in the DB (dedicated `YOUTUBE_RESEARCH_API_KEY`, or the channel's OAuth quota only while uploads leave room); `quotaExceeded` backs off to Pacific midnight; provenance shown in the clip editor | pytest with Google mocked |
| Rendering | 9:16 / 1:1 / 4:5 / 16:9; **face-tracked crop**; **streamer split screen** (facecam top, gameplay bottom, auto-detected); blurred-background fit; **active-speaker crop** (2+ people); **jump cuts** (pauses, retakes, um/uh fillers; edges snapped to the quietest point); **punch-in zoom**; **hook title**; word-highlight captions (4 presets, full style control, editable text); -14 LUFS audio; H.264 CRF 18 | Real renders checked frame by frame (real face image) + pytest |
| Editing | Trim, aspect, layout, crop position, caption style and text, Shorts effects, title/description/hashtags, regenerate (never overwrites an edited title), approve/reject, delete | 38-check browser end-to-end |
| Clips library | Filters, search, bulk approve / render / zip download / publish | Browser end-to-end |
| Jobs | DB-backed worker; real progress; cancel and retry; restart-safe; stale jobs requeued; parallel workers | pytest |
| YouTube | OAuth with multiple channels; Fernet-encrypted tokens; refresh and revoke; resumable `videos.insert` that resumes the same session after a crash (no duplicates); quota-aware queue (default 6 per channel per day); schedule and bulk schedule (daily, every other day, weekly, custom) with preview; calendar; queue with pause/resume/cancel/retry; auto-publish rules (approved clips only); own-channel link import (yt-dlp, up to 4K) | pytest with Google mocked + browser with Google mocked |
| Twitch | OAuth with multiple accounts; VOD list; own-VOD import (yt-dlp); dedupe | pytest with Twitch and yt-dlp mocked |
| TikTok | Login Kit OAuth with multiple accounts; Fernet-encrypted tokens with refresh and revoke; direct post (chunked FILE_UPLOAD, status polling) and draft-to-inbox mode; unaudited apps limited to SELF_ONLY; per-account daily cap (default 15); worker-run scheduling (TikTok has no publish-at); duplicate guard; auto-publish rules (approved + rendered only) | pytest with TikTok mocked (SQLite and Postgres 16) |
| Automation | Watches Twitch and YouTube sources every 30 min → import → analyze → opt-in auto-approve (top N, minimum score) → render → auto-publish; one download at a time; skips live streams and back catalog; optional source cleanup; activity log | pytest (chain, idempotency, isolation) + browser (settings, toggle, log) |
| Settings | Defaults for clip length/count/aspect/layout/captions/effects, metadata preferences, model; system info (disk, models, LLM) | Browser (persistence) |
| Infrastructure | SQLite or PostgreSQL; Alembic migrations, safe when processes start concurrently; Docker Compose (Ubuntu 24.04 image) | Suite passes on Postgres 16; images built and run end to end |

**Tests:** backend 99 passed (`cd backend && .venv/bin/python -m pytest -q`). Frontend tsc, eslint and build are clean. The browser end-to-end script (`frontend/e2e/clips.e2e.cjs`) passed 38 checks, and a separate whole-product smoke test covered every page at desktop and 390 px with no console errors.

## Not verified (impossible without your real accounts)

- **Real Google sign-in and real YouTube uploads.** All tests use a mocked Google. Before trusting it, connect a channel and upload one private test clip; follow `docs/YOUTUBE_SETUP.md`.
- **Real Twitch sign-in and real VOD downloads.** Mocked too. Follow `docs/TWITCH_SETUP.md`.
- **Real TikTok sign-in and posting.** Mocked, and written without access to TikTok's developer docs. Items to check are marked `UNVERIFIED` in `backend/app/tiktok.py` and "(verify)" in `docs/TIKTOK_SETUP.md`.
- **Real YouTube research calls.** `search.list`/`videos.list` are mocked in tests; check Settings → AI after adding `YOUTUBE_RESEARCH_API_KEY` and regenerate one clip's hashtags.
- **yt-dlp against live YouTube and Twitch.** It works today, but it breaks whenever those sites change. Update it with `pip install -U yt-dlp`.

## Known limits

- **Unaudited Google Cloud projects can only upload private videos.** Public or scheduled publishing needs Google's audit. Default quota is about 6 uploads a day per Cloud project, not per channel.
- **TikTok scheduling runs in our worker** (TikTok has no publish-at), so it only works while ViralClip is running. Unaudited TikTok apps can only post privately or send drafts.
- **Face detection uses OpenCV's Haar cascade.** It's fast and needs no download, but it misses profile and turned faces. With two or more people on screen the crop follows whoever's mouth moves most (frame differencing, hysteresis); it's visual only, so there's no audio diarization, and a static box per person means people who walk around aren't tracked.
- **Small local LLMs (3B) invent details.** Use 7B+ for titles you'd publish unreviewed.
- **No emoji in captions.** libass can't render color emoji.
- **No background music or B-roll.** Copyright and licensing make these a liability.
- **No light theme.**
- **Single API process.** The upload offset lock and login throttle are in-memory. Workers scale freely.
- **Transcription RAM is flat in source length** (audio is streamed in ~10 min chunks): about 2.7 GB peak worker RSS with `small`. `WHISPER_BATCH=4` lowers it at ~20% speed cost.

## Next tasks, if needed

1. Run the real YouTube and Twitch connection tests above.
2. A better face model (YuNet) if framing misses faces on your footage.
3. Scene-cut and chat-activity signals for gameplay highlight detection. Loudness only is the current ceiling.
