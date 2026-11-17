# SupoClip automation

Drop a video into `inbox/` and it gets clipped by your self-hosted
[SupoClip](https://github.com/FujiwaraChoki/supoclip), then the best clips are scheduled to every
YouTube and TikTok account you connected in SupoClip. One Python file, no installs.

SupoClip is a separate program (AGPL-3.0) that this script only talks to over its API.

## What it does, every minute
1. Uploads any finished video file in `inbox/` to SupoClip and starts clipping. The file moves to `inbox/done/`.
2. When SupoClip finishes, takes the `top_n` clips scoring at least `min_score`.
3. Schedules each one to every connected account, one per time slot in `post_times`, never twice.

Progress is printed and also written to `auto.log`.

## Settings (`config.json`, created on first run)
| Setting | Default | Meaning |
|---|---|---|
| `api_key` | empty | Your `sk_...` key from SupoClip → Agents & API |
| `api_url` | `http://localhost:8000` | SupoClip's backend |
| `inbox` | `inbox` next to the script | Folder to watch |
| `top_n` / `min_score` | 3 / 70 | How many clips per video, and the lowest score to post |
| `post_times` | `["12:00", "18:00"]` | Daily slots in your local time, per account |
| `providers` | `["youtube", "tiktok"]` | Which connected platforms to post to |
| `privacy` | `{}` | e.g. `{"youtube": "private"}`; empty uses SupoClip's default |
| `clip_length` | `auto` | `auto`, `short`, `medium` or `long` |
| `hashtags` | `["shorts"]` | Added to every post |

## Run
- `start.bat`: run in a window.
- `autostart.ps1`: run hidden at every login. `autostart.ps1 -Remove` undoes it.
- `python auto.py --test`: self-check against a fake SupoClip.

## Not verified
Tested only against a fake SupoClip built from its source code and API docs, not a running one.
