"""Twitch chat spikes: record WHEN messages arrive in the user's own connected channels, and find the busy moments.

Privacy: the recorder joins anonymously (justinfan<n>, read-only) and stores one epoch second per message in
DATA_DIR/chat/<login>.log. Message text and usernames are never stored. Only channels of connected
TwitchAccounts are joined. One process records at a time (DATA_DIR/chat.lock holds its pid).
"""
import atexit
import logging
import os
import random
import re
import socket
import ssl
import statistics
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .auth import current_user
from .db import SessionLocal, get_db
from .models import TwitchAccount, User

log = logging.getLogger("viralclip.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

WINDOW = 30
BONUS = float(os.getenv("CHAT_SPIKE_BONUS", "20"))
RESYNC = 300  # seconds between re-reading the connected-account list
LOGIN = re.compile(r"^\w{1,64}$")  # Twitch logins are [a-z0-9_]; also keeps them safe as file names


def chat_dir():
    return config.DATA_DIR / "chat"


def log_path(login: str):
    if not LOGIN.match(login or ""):
        raise ValueError(f"bad Twitch login {login!r}")
    return chat_dir() / f"{login.lower()}.log"


def stamps(login: str) -> list[float]:
    # ponytail: reads the whole log; a busy channel is ~100k lines/day, fine until logs reach months. Rotate then.
    try:
        return [float(x) for x in log_path(login).read_text().split() if x.isdigit()]
    except (FileNotFoundError, ValueError):
        return []


# ---------- spikes ----------

def spikes(stamps: list[float], start: float, length: float) -> list[dict]:
    """Busiest 30 s windows of [start, start+length): count >= mean + 2*stdev and >= 5, at most one per 2 minutes,
    top 8, sorted by offset. Fewer than 50 messages in range can't tell a spike from noise: []."""
    counts = [0] * (int(length // WINDOW) + 1)
    for t in stamps:
        if start <= t < start + length:
            counts[int((t - start) // WINDOW)] += 1
    if sum(counts) < 50:
        return []
    bar = statistics.mean(counts) + 2 * statistics.pstdev(counts)
    picked = []
    for i in sorted((i for i, c in enumerate(counts) if c >= bar and c >= 5), key=lambda i: -counts[i]):
        if all(abs(i - j) * WINDOW >= 120 for j in picked):  # one moment fills several windows
            picked.append(i)
    # ponytail: chat reacts a few seconds late; the window start is close enough for clip matching
    return sorted(({"offset_s": i * WINDOW, "count": counts[i]} for i in picked[:8]), key=lambda s: s["offset_s"])


def vod_spikes(meta: dict) -> list[dict]:
    """Spikes for project.settings["twitch"] ({login, started_at, duration_s}); [] when the log doesn't cover it."""
    try:
        start = datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00")).timestamp()
        return spikes(stamps(meta["login"]), start, float(meta["duration_s"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


# ---------- single-recorder lock ----------

def _alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would send CTRL_C on Windows
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
        return bool(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock() -> bool:
    """True if this process now holds DATA_DIR/chat.lock. A lock whose pid is dead is taken over."""
    f = config.DATA_DIR / "chat.lock"
    for _ in range(3):
        try:
            fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(f.read_text().strip() or 0)
            except (FileNotFoundError, ValueError):
                pid = 0
            if pid and _alive(pid):
                return False  # held by another process (or already by this one: don't start twice)
            f.unlink(missing_ok=True)  # ponytail: pid reuse after a crash can keep a dead lock "alive"; rare
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        atexit.register(release_lock)
        return True
    return False


def release_lock():
    f = config.DATA_DIR / "chat.lock"
    try:
        if int(f.read_text().strip()) == os.getpid():
            f.unlink()
    except (FileNotFoundError, ValueError):
        pass


# ---------- recorder ----------

def _irc():
    return (os.getenv("CHAT_IRC_HOST", "irc.chat.twitch.tv"), int(os.getenv("CHAT_IRC_PORT", "6697")),
            os.getenv("CHAT_IRC_TLS", "true").lower() not in ("0", "false", "no"))


def _live(login: str):
    return chat_dir() / f"{login.lower()}.live"


def record(login: str, stop: threading.Event, max_backoff: float = 300):
    """Record one channel until `stop` is set. Reconnects with exponential backoff."""
    out, live = log_path(login), _live(login)
    chat_dir().mkdir(parents=True, exist_ok=True)
    backoff = 1.0
    while not stop.is_set():
        host, port, tls = _irc()
        try:
            sock = socket.create_connection((host, port), timeout=10)
            if tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            with sock, open(out, "a", encoding="ascii") as f:
                sock.settimeout(2)  # short, so `stop` is noticed quickly
                sock.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{login.lower()}\r\n".encode())
                buf, heard, touched = b"", time.time(), 0.0
                while not stop.is_set():
                    if time.time() - touched > 60:
                        live.touch()
                        touched = time.time()
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        if time.time() - heard > 360:  # Twitch PINGs every ~5 min; silence = dead link
                            raise OSError("no data for 6 minutes")
                        continue
                    if not data:
                        raise OSError("server closed the connection")
                    heard, backoff = time.time(), 1.0
                    *lines, buf = (buf + data).split(b"\r\n")
                    for line in lines:
                        if line.startswith(b"PING"):
                            sock.sendall(b"PONG" + line[4:] + b"\r\n")
                        elif b" PRIVMSG " in line:
                            f.write(f"{time.time():.0f}\n")  # the time only: never text or username
                            f.flush()
        except OSError as e:
            log.info("Twitch chat #%s: %s; reconnecting in %.0f s", login, e, backoff)
            live.unlink(missing_ok=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, max_backoff)
    live.unlink(missing_ok=True)


def _logins() -> set[str]:
    with SessionLocal() as db:
        return {a.lower() for a in db.scalars(select(TwitchAccount.login)) if LOGIN.match(a or "")}


def supervise(stop: threading.Event, resync: float = RESYNC):
    """One recorder thread per connected channel; the account list is re-read every `resync` seconds."""
    running: dict[str, threading.Event] = {}
    while not stop.is_set():
        try:
            want = _logins()
        except Exception:
            log.exception("chat: reading Twitch accounts failed")
            want = set(running)
        for login in want - set(running):
            running[login] = ev = threading.Event()
            threading.Thread(target=record, args=(login, ev), daemon=True, name=f"chat-{login}").start()
        for login in set(running) - want:  # disconnected account
            running.pop(login).set()
        stop.wait(resync)
    for ev in running.values():
        ev.set()


def start(stop: threading.Event | None = None) -> threading.Thread:
    """Background thread for the worker: becomes the recorder once it holds the lock (retried every RESYNC s,
    so another worker takes over when the recording one dies)."""
    stop = stop or threading.Event()

    def main():
        while not stop.is_set():
            if acquire_lock():
                log.info("chat: recording connected Twitch channels (timestamps only)")
                try:
                    supervise(stop)
                finally:
                    release_lock()
                return
            stop.wait(RESYNC)

    t = threading.Thread(target=main, daemon=True, name="chat-recorder")
    t.start()
    return t


# ---------- API ----------

def _iso(ts: float | None):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


@router.get("/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock = config.DATA_DIR / "chat.lock"
    try:
        recording = _alive(int(lock.read_text().strip()))
    except (FileNotFoundError, ValueError):
        recording = False
    channels, cutoff = [], time.time() - 86400
    for login in sorted({a.lower() for a in db.scalars(select(TwitchAccount.login).where(TwitchAccount.user_id == user.id))
                         if LOGIN.match(a or "")}):
        s = stamps(login)
        try:
            live = time.time() - _live(login).stat().st_mtime < 180
        except FileNotFoundError:
            live = False
        channels.append({"login": login, "recording": recording and live,
                         "messages_24h": sum(t >= cutoff for t in s), "last_message_at": _iso(s[-1] if s else None)})
    return {"recording": recording, "channels": channels}
