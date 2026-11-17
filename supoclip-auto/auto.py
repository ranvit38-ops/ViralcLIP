"""Hands-off automation for a self-hosted SupoClip (github.com/FujiwaraChoki/supoclip).

Drop a video into the inbox folder -> SupoClip makes clips -> the best ones are scheduled
to every connected YouTube/TikTok account in your daily time slots. Standard library only.

    python auto.py            run forever (checks every minute)
    python auto.py --once     one pass, then exit
    python auto.py --test     self-check against a fake SupoClip (no network)

Settings live in config.json next to this file (created on first run).
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
DEFAULTS = {
    "api_url": "http://localhost:8000",
    "api_key": "",                      # sk_... from SupoClip -> Agents & API (or env SUPOCLIP_API_KEY)
    "inbox": str(HERE / "inbox"),
    "clip_length": "auto",              # auto | short | medium | long
    "top_n": 3,                         # best clips per video to post
    "min_score": 70,                    # skip clips SupoClip scores below this (0-100)
    "post_times": ["12:00", "18:00"],   # your local time; one post per slot per account
    "providers": ["youtube", "tiktok"],
    "privacy": {},                      # e.g. {"youtube": "private"}; empty = SupoClip's default
    "hashtags": ["shorts"],
}


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(HERE / "auto.log", "a", encoding="utf-8") as f:  # the hidden autostart has no window to read
        f.write(line + "\n")


class Api:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method, path, body=None, headers=None, timeout=60):
        h = {"Authorization": f"Bearer {self.key}", **(headers or {})}
        if isinstance(body, (dict, list)):
            body, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.url + path, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300].decode(errors='replace')}") from None

    def upload(self, f: Path) -> str:
        """Streamed multipart upload (VODs are gigabytes: never read the file into memory)."""
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(f.name)[0] or "video/mp4"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{f.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()

        def parts():
            yield head
            with open(f, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    yield chunk
            yield tail

        r = self.call("POST", "/upload", parts(), timeout=3600, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(head) + f.stat().st_size + len(tail)),
            "Idempotency-Key": f"{f.name}-{f.stat().st_size}"})
        return r["video_path"]


def next_slot(after: datetime, taken: set, times: list) -> datetime:
    """First daily slot later than `after` that isn't already used by this account."""
    day = after.date()
    while True:
        for t in sorted(times):
            hh, mm = map(int, t.split(":"))
            slot = datetime(day.year, day.month, day.day, hh, mm).astimezone()
            if slot > after and slot.isoformat() not in taken:
                return slot
        day += timedelta(days=1)


def submit_inbox(api, cfg, state):
    inbox = Path(cfg["inbox"])
    (inbox / "done").mkdir(parents=True, exist_ok=True)
    for f in sorted(inbox.iterdir()):
        if f.suffix.lower() not in VIDEO_EXTS or not f.is_file():
            continue
        size = f.stat().st_size
        if state["sizes"].get(f.name) != size:  # still being copied/downloaded: wait until the size holds for a pass
            state["sizes"][f.name] = size
            continue
        log(f"Uploading {f.name} ({size / 1e6:.0f} MB)")
        path = api.upload(f)
        task = api.call("POST", "/tasks/", {"source": {"url": path, "title": f.stem}, "clip_length": cfg["clip_length"]})
        state["tasks"][task["task_id"]] = {"name": f.name, "state": "processing"}
        state["sizes"].pop(f.name, None)
        f.replace(inbox / "done" / f.name)
        log(f"SupoClip is clipping {f.name} (task {task['task_id']})")
        save(state)


def schedule_finished(api, cfg, state):
    waiting = {tid: t for tid, t in state["tasks"].items() if t["state"] == "processing"}
    if not waiting:
        return
    accounts = [a for a in api.call("GET", "/social/connections")["connections"]
                if a["provider"] in cfg["providers"] and a.get("status") != "reauth_required"]
    for tid, t in waiting.items():
        status = api.call("GET", f"/tasks/{tid}").get("status")
        if status in ("error", "cancelled", "failed"):
            t["state"] = "failed"
            log(f"{t['name']}: SupoClip couldn't clip it ({status}). Open the task in SupoClip to see why.")
        elif status == "completed":
            clips = api.call("GET", f"/tasks/{tid}/clips")["clips"]
            picks = sorted(clips, key=lambda c: -(c.get("virality_score") or 0))[:cfg["top_n"]]
            picks = [c for c in picks if (c.get("virality_score") or 0) >= cfg["min_score"]]
            if not accounts:
                log(f"{t['name']}: clips are ready but no YouTube/TikTok account is connected in SupoClip. Will retry.")
                continue
            n = sum(post_clip(api, cfg, tid, c, a) for c in picks for a in accounts)
            t["state"] = "scheduled"
            log(f"{t['name']}: {len(clips)} clips made, {len(picks)} passed (score >= {cfg['min_score']}), "
                f"{n} post(s) scheduled.")
        save(state)


def post_clip(api, cfg, tid, clip, account) -> int:
    # SupoClip is the source of truth for what's already posted/scheduled, so a crash here never double-posts.
    def posts(query):
        return [p for p in api.call("GET", f"/social/posts?limit=500&{query}")["posts"]
                if p.get("social_account_id") == account["id"]]
    if any(p.get("status") != "cancelled" for p in posts(f"clip_id={clip['id']}")):
        return 0
    # ponytail: reads up to 500 scheduled posts (SupoClip's page cap); page through if you ever queue more.
    taken = {datetime.fromisoformat(p["scheduled_for"].replace("Z", "+00:00")).astimezone().isoformat()
             for p in posts("status=scheduled") if p.get("scheduled_for")}
    when = next_slot(datetime.now().astimezone() + timedelta(minutes=5), taken, cfg["post_times"])
    body = {"task_id": tid, "clip_id": clip["id"], "social_account_id": account["id"],
            "hashtags": cfg["hashtags"], "scheduled_for": when.isoformat()}
    if cfg["privacy"].get(account["provider"]):
        body["privacy_level"] = cfg["privacy"][account["provider"]]
    try:  # AI caption needs ANTHROPIC_API_KEY on the SupoClip server; without it SupoClip uses the clip's hook title
        body["caption"] = api.call("POST", "/social/captions", {
            "task_id": tid, "clip_id": clip["id"], "provider": account["provider"]})["caption"]
    except RuntimeError:
        pass
    try:
        api.call("POST", "/social/posts", body)
    except RuntimeError as e:
        log(f"Couldn't schedule a clip to {account['provider']} ({account.get('display_name')}): {e}")
        return 0
    log(f"  -> {account['provider']} {account.get('display_name') or ''} at {when:%a %d %b %H:%M}")
    return 1


STATE = HERE / "state.json"


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def load_config():
    f = HERE / "config.json"
    if not f.exists():
        f.write_text(json.dumps(DEFAULTS, indent=2))
        log(f"Created {f}. Put your SupoClip API key in it, then run this again.")
        sys.exit(0)
    cfg = {**DEFAULTS, **json.loads(f.read_text())}
    cfg["api_key"] = os.getenv("SUPOCLIP_API_KEY") or cfg["api_key"]
    if not cfg["api_key"]:
        sys.exit(f"No API key. Create one in SupoClip (Agents & API) and put it in {f} as \"api_key\".")
    return cfg


def run_once(api, cfg, state):
    for step in (submit_inbox, schedule_finished):
        try:
            step(api, cfg, state)
        except (RuntimeError, OSError) as e:  # SupoClip down, disk hiccup: say so and try again next pass
            log(f"{step.__name__} failed, will retry: {e}")
    save(state)


def main():
    cfg = load_config()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"tasks": {}, "sizes": {}}
    api = Api(cfg["api_url"], cfg["api_key"])
    log(f"Watching {cfg['inbox']} -> {cfg['api_url']}")
    while True:
        run_once(api, cfg, state)
        if "--once" in sys.argv:
            break
        time.sleep(60)


def self_test():
    """Fake SupoClip on a local port; one video goes inbox -> task -> scheduled posts, with no duplicates."""
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    global STATE
    posts, calls = [], []

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer sk_test"
            if self.path == "/social/connections":
                self.reply({"connections": [{"id": "yt", "provider": "youtube", "status": "active"},
                                            {"id": "tt", "provider": "tiktok", "status": "active"},
                                            {"id": "ig", "provider": "instagram", "status": "active"}]})
            elif self.path == "/tasks/T1":
                self.reply({"status": "completed"})
            elif self.path == "/tasks/T1/clips":
                self.reply({"clips": [{"id": f"c{s}", "virality_score": s} for s in (95, 40, 80, 75, 72)]})
            elif self.path.startswith("/social/posts?"):
                q = dict(kv.split("=") for kv in self.path.split("?")[1].split("&"))
                self.reply({"posts": [p for p in posts if all(p.get(k) == v for k, v in q.items() if k != "limit")]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            if self.path == "/upload":
                assert b"FAKEVIDEO" in raw and b'name="video"' in raw
                self.reply({"video_path": "upload://x.mp4"})
            elif self.path == "/tasks/":
                assert json.loads(raw)["source"]["url"] == "upload://x.mp4"
                self.reply({"task_id": "T1"})
            elif self.path == "/social/captions":
                self.send_error(503)
            elif self.path == "/social/posts":
                posts.append({**json.loads(raw), "status": "scheduled"})
                self.reply({"post": {}})

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as d:
        STATE = Path(d) / "state.json"
        (Path(d) / "in").mkdir()
        (Path(d) / "in" / "vod.mp4").write_bytes(b"FAKEVIDEO")
        cfg = {**DEFAULTS, "inbox": str(Path(d) / "in"), "privacy": {"youtube": "private"}}
        api, state = Api(f"http://127.0.0.1:{srv.server_port}", "sk_test"), {"tasks": {}, "sizes": {}}
        run_once(api, cfg, state)                       # pass 1: file seen, size recorded, not uploaded yet
        assert "/upload" not in calls
        for _ in range(3):                              # pass 2 uploads + schedules; later passes change nothing
            run_once(api, cfg, state)
        assert calls.count("/upload") == 1 and (Path(d) / "in" / "done" / "vod.mp4").exists()
        assert len(posts) == 6, posts                   # top 3 (95, 80, 75) x youtube + tiktok, never instagram
        assert {p["clip_id"] for p in posts} == {"c95", "c80", "c75"}
        for acc in ("yt", "tt"):
            slots = [p["scheduled_for"] for p in posts if p["social_account_id"] == acc]
            assert len(set(slots)) == 3, slots          # one post per slot per account
        assert all(p.get("privacy_level") == "private" for p in posts if p["social_account_id"] == "yt")
        assert state["tasks"]["T1"]["state"] == "scheduled"
    now = datetime(2026, 1, 1, 13, 0).astimezone()
    assert next_slot(now, set(), ["12:00", "18:00"]).hour == 18
    assert next_slot(now, {next_slot(now, set(), ["12:00", "18:00"]).isoformat()}, ["12:00", "18:00"]).day == 2
    print("self-test passed")


if __name__ == "__main__":
    self_test() if "--test" in sys.argv else main()
