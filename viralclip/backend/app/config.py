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
APP_URL = os.getenv("APP_URL", "http://localhost:3000")
SECURE_COOKIES = APP_URL.startswith("https://")
SESSION_DAYS = 30

# YouTube (Google OAuth web client). See docs/YOUTUBE_SETUP.md.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:3000/api/youtube/callback")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
YOUTUBE_DAILY_UPLOAD_LIMIT = int(os.getenv("YOUTUBE_DAILY_UPLOAD_LIMIT", "6"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
