# SupoClip automation

Drop a video into `inbox/` and it gets clipped by your self-hosted
[SupoClip](https://github.com/FujiwaraChoki/supoclip), then the best clips are scheduled to every
YouTube and TikTok account you connected in SupoClip. One Python file, no installs.

SupoClip is a separate program (AGPL-3.0) that this script only talks to over its API.

## What it does, every minute
1. Uploads any finished video file in `inbox/` to SupoClip and starts clipping. The file moves to `inbox/done/`.
2. Takes the next link from `links.txt` (one per line). Links must be videos published by the YouTube channel
   you connected in SupoClip; anything else is skipped and listed in `status.txt`. One video is clipped at a
   time, and the next one starts only when fewer than `buffer_days` of posts are queued.
3. When SupoClip finishes, takes the `top_n` clips scoring at least `min_score`.
4. Schedules each one to every connected account, one per time slot in `post_times` (six a day by default), never twice.

Progress is printed and written to `auto.log`. `status.txt` is the weekly glance: links waiting, posts
scheduled, skipped links, failed videos, and "ADD MORE LINKS" when the queue is nearly empty.

## Settings (`config.json`, created on first run)
| Setting | Default | Meaning |
|---|---|---|
| `api_key` | empty | Your `sk_...` key from SupoClip → Agents & API |
| `api_url` | `http://localhost:8000` | SupoClip's backend |
| `inbox` | `inbox` next to the script | Folder to watch |
| `top_n` / `min_score` | 3 / 70 | How many clips per video, and the lowest score to post |
| `links` | `links.txt` next to the script | Link queue: your own YouTube videos, one per line |
| `post_times` | six slots, 09:00–22:00 | Daily slots in your local time, per account |
| `buffer_days` | 3 | Days of queued posts before the next link waits |
| `providers` | `["youtube", "tiktok"]` | Which connected platforms to post to |
| `privacy` | `{}` | e.g. `{"youtube": "private"}`; empty uses SupoClip's default |
| `clip_length` | `auto` | `auto`, `short`, `medium` or `long` |
| `hashtags` | `["shorts"]` | Added to every post |

## Run
- `start.bat`: run in a window.
- `autostart.ps1`: run hidden at every login. `autostart.ps1 -Remove` undoes it.
- `python auto.py --test`: self-check against a fake SupoClip.

## SupoClip settings this needs
In SupoClip's `.env` (then `docker compose up -d --build`):

```env
# free, local clipping (no paid keys)
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL_SIZE=base
BACKEND_CPU_ONLY=true
LLM=ollama:qwen2.5:3b
OLLAMA_BASE_URL=
# posting
SOCIAL_PUBLISHING_ACCESS=all
YOUTUBE_OAUTH_CLIENT_ID=...
YOUTUBE_OAUTH_CLIENT_SECRET=...
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```

- The LLM line needs [Ollama](https://ollama.com) running on the PC with that model pulled (`ollama pull qwen2.5:3b`).
- **API key**: SupoClip only creates one for a verified email, and it can only send the verification mail through
  Amazon SES. Without SES, mark yourself verified once: run
  `docker exec -it supoclip-postgres psql -U supoclip -d supoclip`, then type `update users set "emailVerified"=true;`
- **Posting** needs your own Google and TikTok developer apps (the four keys above), then connect the accounts in
  SupoClip. Without them SupoClip answers "publishing is not configured on this server".
- **TikTok** is switched off in SupoClip by default ("TikTok publishing is paused"): turn on the `tiktok_publishing`
  flag in SupoClip's `/admin`. Until TikTok has audited your app it only accepts private posts.
- A post SupoClip refuses is retried every minute for about 30 minutes, then dropped (the reason is in `auto.log`).
- Without `ANTHROPIC_API_KEY` on SupoClip, each post's description is the clip's transcript.

## What was checked against a real SupoClip (Nov 2026, Linux, no Docker)
Worked: API key, upload, clipping (3 clips with scores from a 2m50s video), failed-task detection, "no account
connected" retry, scheduling to a YouTube account in the right slots, no duplicates on later passes, `privacy`.

Not checked: a real YouTube or TikTok upload (no accounts; the YouTube account was a made-up database row, so
nothing was published), TikTok scheduling (SupoClip asks TikTok first), Docker, Windows, and real Ollama
(a llama.cpp server stood in for it).
