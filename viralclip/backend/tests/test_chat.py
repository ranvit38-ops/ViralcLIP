"""Twitch chat spikes: spike finding, the anonymous recorder against a local fake IRC server, the single-recorder lock,
VOD metadata on import, the analyze bonus, and /api/chat/status isolation."""
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import select

from app import chat, config, pipeline, selection, worker
from app.db import SessionLocal
from app.models import Clip, Job, Project, TwitchAccount
from tests.conftest import make_video, new_client, upload
from tests.test_twitch import connect, tw  # noqa: F401  (fixture)

T0 = 1_700_000_000
chat.chat_dir().mkdir(parents=True, exist_ok=True)


def background(length, every=10):
    return [T0 + t for t in range(0, length, every)]


def test_spikes_finds_exactly_two_bursts_and_ignores_sparse_chat():
    stamps = background(3600)
    stamps += [T0 + 300 + i * 0.5 for i in range(40)]  # burst inside one window
    stamps += [T0 + 1190 + i for i in range(30)]  # burst straddling two windows: still one spike
    got = chat.spikes(stamps, T0, 3600)
    assert [s["offset_s"] for s in got] == [300, 1200], got
    assert got[0]["count"] == 43
    assert chat.spikes(background(400, every=10), T0, 400) == []  # 40 messages: too little to tell
    assert chat.spikes(stamps, T0 + 5000, 600) == []  # log doesn't cover this VOD


class FakeIRC:
    """Plain-socket IRC server: expects NICK/JOIN, sends PING (expects PONG) and PRIVMSG lines, then hangs up so
    the recorder has to reconnect."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.got, self.pongs, self.conns = [], 0, 0
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        for msgs in (3, 1):
            conn, _ = self.srv.accept()
            self.conns += 1
            with conn, conn.makefile("rb") as f:
                line = b""
                while not line.startswith(b"JOIN"):
                    line = f.readline().strip()
                    self.got.append(line)
                conn.sendall(b"PING :tmi.twitch.tv\r\n")
                if f.readline().strip() == b"PONG :tmi.twitch.tv":
                    self.pongs += 1
                for _ in range(msgs):
                    conn.sendall(b":alice!alice@alice.tmi.twitch.tv PRIVMSG #me :my secret password\r\n")
                time.sleep(0.3)


def wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_recorder_writes_only_timestamps_and_reconnects(monkeypatch):
    irc = FakeIRC()
    monkeypatch.setenv("CHAT_IRC_HOST", "127.0.0.1")
    monkeypatch.setenv("CHAT_IRC_PORT", str(irc.port))
    monkeypatch.setenv("CHAT_IRC_TLS", "false")
    chat.log_path("recme").unlink(missing_ok=True)
    stop = threading.Event()
    t = threading.Thread(target=chat.record, args=("recme", stop), daemon=True)
    t.start()
    try:
        assert wait(lambda: chat.log_path("recme").exists() and len(chat.log_path("recme").read_text().split()) == 4)
    finally:
        stop.set()
        t.join(5)
    text = chat.log_path("recme").read_text()
    assert all(x.isdigit() and abs(int(x) - time.time()) < 60 for x in text.split())
    assert "secret" not in text and "alice" not in text
    assert irc.conns == 2 and irc.pongs == 2
    assert irc.got[0].startswith(b"NICK justinfan") and b"JOIN #recme" in irc.got
    assert not chat._live("recme").exists()


def test_only_one_recorder_and_dead_lock_is_taken_over():
    lock = config.DATA_DIR / "chat.lock"
    lock.unlink(missing_ok=True)
    assert chat.acquire_lock()
    try:
        assert not chat.acquire_lock()  # held by a live pid (this one)
        other = subprocess.run([sys.executable, "-c", "from app import chat; print(chat.acquire_lock())"],
                               capture_output=True, text=True, cwd=Path(__file__).parents[1],
                               env={**__import__("os").environ, "VC_DATA_DIR": str(config.DATA_DIR)})
        assert other.stdout.strip() == "False", other.stderr
    finally:
        chat.release_lock()
    assert not lock.exists()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock.write_text(str(dead.pid))
    assert chat.acquire_lock() and lock.read_text() == str(__import__("os").getpid())
    chat.release_lock()


def test_supervisor_follows_the_account_list(monkeypatch):
    started, stopped = [], []

    def fake_record(login, ev):
        started.append(login)
        ev.wait()
        stopped.append(login)

    monkeypatch.setattr(chat, "record", fake_record)
    stop = threading.Event()
    t = threading.Thread(target=chat.supervise, args=(stop, 0.05), daemon=True)
    t.start()
    try:
        c = new_client()
        uid = c.get("/api/auth/me").json()["id"]
        with SessionLocal() as db:
            db.add(TwitchAccount(user_id=uid, twitch_user_id="42", login="SupChan", encrypted_tokens="x"))
            db.commit()
        assert wait(lambda: "supchan" in started)
        assert "supchan" not in stopped
        with SessionLocal() as db:
            db.query(TwitchAccount).filter(TwitchAccount.login == "SupChan").delete()
            db.commit()
        assert wait(lambda: "supchan" in stopped)
    finally:
        stop.set()
        t.join(5)


def test_import_saves_vod_metadata(tw, client):  # noqa: F811
    connect(client)
    tw.vod("10000777", hours=1.5)
    r = client.post("/api/twitch/import", json={"url": "https://www.twitch.tv/videos/10000777"})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        meta = db.get(Project, r.json()["id"]).settings["twitch"]
    assert meta == {"login": "me", "vod_id": "10000777", "started_at": tw.vods["10000777"]["created_at"],
                    "duration_s": 5400}


def _sentence(text, t0):
    return [{"start": round(t0 + i * 0.4, 2), "end": round(t0 + i * 0.4 + 0.3, 2), "word": w + " "}
            for i, w in enumerate(text.split())]


STRONG = "Why is this the biggest secret you never knew? It is the one habit that changed my life."
WEAK = "Here is what I learned that day. You should check your saves first."  # ~61, vs ~78 for STRONG


def test_chat_spike_outranks_a_higher_scored_clip_and_carries_the_reason():
    segs = [{"start": 0, "end": 8, "text": STRONG, "words": _sentence(STRONG, 0)},
            {"start": 200, "end": 208, "text": WEAK, "words": _sentence(WEAK, 200)}]
    plain = selection.select_clips(segs, 600, 5, 15, 1)
    assert plain[0]["start"] < 10 and "chat" not in plain[0]["reason"]
    boosted = selection.select_clips(segs, 600, 5, 15, 1, chat_spikes=[{"offset_s": 210, "count": 47}])
    assert boosted[0]["start"] >= 199, boosted
    assert "Twitch chat spiked here (47 messages in 30 s)" in boosted[0]["reason"]
    assert boosted[0]["score"] <= 100 and boosted[0]["breakdown"]["chat_spike"] == 47
    # +-10 s padding, and nothing else changes without spikes
    far = selection.chat_boost([{"start": 0, "end": 20, "score": 90, "breakdown": {}, "reason": ""}],
                               [{"offset_s": 29, "count": 9}, {"offset_s": 100, "count": 9}], 20)
    assert far[0]["score"] == 100 and far[0]["reason"] == "Twitch chat spiked here (9 messages in 30 s)."


def test_analyze_applies_the_chat_bonus_from_the_recorded_log(monkeypatch):
    c = new_client()
    pid, _ = upload(c, make_video("chat.mp4"))
    segs = [{"start": 0, "end": 8, "text": STRONG, "words": _sentence(STRONG, 0)},
            {"start": 200, "end": 208, "text": WEAK, "words": _sentence(WEAK, 200)}]
    monkeypatch.setattr(pipeline, "scan_audio", lambda *a, **k: ([], [], (segs, "en")))
    chat.log_path("analyzeme").write_text("".join(f"{int(t)}\n" for t in
                                                  background(600) + [T0 + 210 + i * 0.5 for i in range(40)]))

    def analyze(meta):
        with SessionLocal() as db:
            p = db.get(Project, pid)
            p.settings = {**(p.settings or {}), "twitch": meta} if meta else {}
            db.query(Clip).filter(Clip.project_id == pid).delete()
            j = Job(project_id=pid, job_type="analyze", status="running", params={"min_len": 5, "max_len": 15, "count": 1})
            db.add(j)
            db.commit()
            jid = j.id
        worker.run_job(jid, {"analyze": pipeline.analyze})
        with SessionLocal() as db:
            assert db.get(Job, jid).status == "done", db.get(Job, jid).error
            return db.scalars(select(Clip).where(Clip.project_id == pid)).one()

    meta = {"login": "analyzeme", "vod_id": "1", "started_at": "2023-11-14T22:13:20Z", "duration_s": 600}
    assert chat.datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00")).timestamp() == T0
    clip = analyze(meta)
    assert clip.start >= 199 and "Twitch chat spiked here (43 messages in 30 s)" in clip.reason
    assert c.get(f"/api/clips/{clip.id}").json()["reason"] == clip.reason
    assert analyze(None).start < 10  # no chat data: unchanged behaviour
    assert analyze({**meta, "login": "nobody"}).start < 10


def test_status_shows_only_your_channels(tw):  # noqa: F811
    a, b = new_client(), new_client()
    connect(a)
    chat.log_path("me").write_text(f"{T0}\n{int(time.time()) - 60}\n")
    s = a.get("/api/chat/status").json()
    assert s["channels"] == [{"login": "me", "recording": False, "messages_24h": 1,
                              "last_message_at": s["channels"][0]["last_message_at"]}]
    assert s["channels"][0]["last_message_at"].startswith(time.strftime("%Y-", time.gmtime()))
    assert b.get("/api/chat/status").json()["channels"] == []
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/api/chat/status").status_code == 401
