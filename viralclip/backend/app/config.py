"""All configuration comes from environment variables (see ../../.env.example)."""
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("VC_DATA_DIR", Path(__file__).resolve().parents[1] / "data")).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'viralclip.db'}")

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

MAX_UPLOAD_BYTES = int(float(os.getenv("VC_MAX_UPLOAD_GB", "20")) * 1024**3)
# Refuse uploads that would leave less than this much free disk (renders need room too).
MIN_FREE_BYTES = int(float(os.getenv("VC_MIN_FREE_GB", "2")) * 1024**3)
MAX_CHUNK_BYTES = 16 * 1024**2

ALLOW_SIGNUP = os.getenv("VC_ALLOW_SIGNUP", "true").lower() == "true"
# Skip the sign-in screen for requests from this PC (run.py binds everything to 127.0.0.1). Off by default.
AUTO_LOGIN = os.getenv("VC_AUTO_LOGIN", "false").lower() == "true"
APP_URL = os.getenv("APP_URL", "http://localhost:3000")
SECURE_COOKIES = APP_URL.startswith("https://")
SESSION_DAYS = 30

# YouTube (Google OAuth web client). See docs/YOUTUBE_SETUP.md.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:3000/api/youtube/callback")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
YOUTUBE_DAILY_UPLOAD_LIMIT = int(os.getenv("YOUTUBE_DAILY_UPLOAD_LIMIT", "6"))
YOUTUBE_DAILY_QUOTA = int(os.getenv("YOUTUBE_DAILY_QUOTA", "10000"))  # units per Cloud project per Pacific day
# Trend research (search.list, read-only). A plain API key, ideally from a second Cloud project, so research
# never eats upload quota. Without it the connected channel's OAuth token is used, with a smaller budget.
YOUTUBE_RESEARCH_API_KEY = os.getenv("YOUTUBE_RESEARCH_API_KEY", "")
YOUTUBE_RESEARCH_DAILY_SEARCHES = os.getenv("YOUTUBE_RESEARCH_DAILY_SEARCHES", "")  # default: 90 with key, 20 shared

# Twitch (dev.twitch.tv application). See docs/TWITCH_SETUP.md.
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:3000/api/twitch/callback")

# TikTok (developers.tiktok.com app: Login Kit + Content Posting API). See docs/TIKTOK_SETUP.md.
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:3000/api/tiktok/callback")
# Unaudited apps may only post privately (SELF_ONLY); drafts to the inbox are the useful default then.
TIKTOK_AUDITED = os.getenv("TIKTOK_AUDITED", "false").lower() == "true"
TIKTOK_DAILY_POST_LIMIT = int(os.getenv("TIKTOK_DAILY_POST_LIMIT", "15"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
