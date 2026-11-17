"""Hands-off automation for a self-hosted SupoClip (github.com/FujiwaraChoki/supoclip).

Drop a video into the inbox folder, or paste links to your own YouTube videos into links.txt
-> SupoClip makes clips -> the best ones are scheduled to every connected YouTube/TikTok account
in your daily time slots. Standard library only.

    python auto.py            run forever (checks every minute)
    python auto.py --once     one pass, then exit
    python auto.py --test     self-check against a fake SupoClip (no network)

Settings live in config.json next to this file (created on first run).
"""
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
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
    "links": str(HERE / "links.txt"),   # one link per line to videos on your own connected YouTube channel
    "post_times": ["09:00", "12:00", "15:00", "18:00", "20:00", "22:00"],  # local time; one post per slot per account
    "buffer_days": 3,                   # start the next link only when fewer than this many days of posts are queued
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
            # SupoClip only accepts [A-Za-z0-9._:-]{8,200} here, so hash the name (spaces, brackets, accents)
            "Idempotency-Key": hashlib.sha1(f"{f.name}-{f.stat().st_size}".encode()).hexdigest()})
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


def video_id(url):
    m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/))([\w-]{11})", url)
    return m.group(1) if m else None


def channel_of(vid):
    """Id of the channel that published a YouTube video, read from its public watch page (None if unreadable)."""
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={vid}",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode(errors="replace")
    m = re.search(r'"videoDetails":\{.*?"channelId":"(UC[\w-]{22})"', page)
    return m.group(1) if m else None


def submit_links(api, cfg, state):
    """Start the next link from links.txt: one video at a time, and only while the post queue is running low."""
    f = Path(cfg["links"])
    if not f.exists():
        f.write_text("# One link per line to videos on YOUR connected YouTube channel. Lines starting with # are ignored.\n")
    links = state.setdefault("links", {})
    todo = [u for line in f.read_text(encoding="utf-8").splitlines()
            if (u := line.strip()) and not u.startswith("#") and u not in links]
    if not todo or any(t["state"] == "processing" for t in state["tasks"].values()):
        return
    scheduled = api.call("GET", "/social/posts?limit=500&status=scheduled")["posts"]
    if max(Counter(p["social_account_id"] for p in scheduled).values(), default=0) \
            >= cfg["buffer_days"] * len(cfg["post_times"]):
        return
    mine = {a.get("external_account_id") for a in api.call("GET", "/social/connections")["connections"]
            if a["provider"] == "youtube"}
    for url in todo:
        vid = video_id(url)
        if not vid:
            links[url] = "skipped: not a YouTube link (download other videos of yours and drop the file in inbox)"
        elif not mine:
            log("links.txt: connect your YouTube channel in SupoClip first. Will retry.")
            return
        else:
            owner = channel_of(vid)
            tries = state.setdefault("link_tries", {})
            if owner is None and tries.get(url, 0) < 10:  # consent page, private video, YouTube hiccup: retry a while
                tries[url] = tries.get(url, 0) + 1
                log(f"links.txt: couldn't read who published {url}. Will retry.")
                return
            if owner in mine:
                task = api.call("POST", "/tasks/", {"source": {"url": f"https://www.youtube.com/watch?v={vid}"},
                                                    "clip_length": cfg["clip_length"]})
                state["tasks"][task["task_id"]] = {"name": url, "state": "processing"}
                links[url] = "started"
                log(f"SupoClip is clipping {url} (task {task['task_id']}); {len(todo) - 1} link(s) still waiting")
                save(state)
                return
            links[url] = "skipped: not published by a YouTube channel you connected in SupoClip"
        log(f"links.txt: {url} {links[url]}")
        save(state)


def write_status(api, cfg, state):
    """status.txt: the one file to glance at each week."""
    links = state.get("links", {})
    f = Path(cfg["links"])
    waiting = [u for line in (f.read_text(encoding="utf-8").splitlines() if f.exists() else [])
               if (u := line.strip()) and not u.startswith("#") and u not in links]
    posts = api.call("GET", "/social/posts?limit=500&status=scheduled")["posts"]
    last = max((p["scheduled_for"] for p in posts if p.get("scheduled_for")), default=None)
    lines = [f"Updated {datetime.now():%a %d %b %H:%M}",
             f"Links waiting: {len(waiting)}",
             f"Videos being clipped: {sum(t['state'] == 'processing' for t in state['tasks'].values())}",
             f"Posts scheduled: {len(posts)}" + (f" (last one {last[:16].replace('T', ' ')} UTC)" if last else ""),
             "ADD MORE LINKS" if not waiting and len(posts) < 2 * len(cfg["post_times"]) else "",
             "", "Skipped links:"] + [f"  {u}  {why}" for u, why in links.items() if why.startswith("skipped")] \
        + ["", "Failed videos:"] + [f"  {t['name']}" for t in state["tasks"].values() if t["state"] == "failed"]
    (HERE / "status.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
            done = [post_clip(api, cfg, tid, c, a) for c in picks for a in accounts]
            t["tries"] = t.get("tries", 0) + 1
            # ponytail: a refused post is retried every pass, then dropped after 30 passes (~30 min) to stop log spam
            if all(done) or t["tries"] >= 30:
                t["state"] = "scheduled"
                log(f"{t['name']}: {len(clips)} clips made, {len(picks)} passed (score >= {cfg['min_score']}), "
                    f"{sum(done)} of {len(done)} post(s) scheduled.")
            else:
                log(f"{t['name']}: {done.count(False)} post(s) not scheduled yet. Will retry.")
        save(state)


def post_clip(api, cfg, tid, clip, account) -> bool:
    """True once this clip is scheduled (now or on an earlier pass) for this account."""
    # SupoClip is the source of truth for what's already posted/scheduled, so a crash here never double-posts.
    def posts(query):
        return [p for p in api.call("GET", f"/social/posts?limit=500&{query}")["posts"]
                if p.get("social_account_id") == account["id"]]
    if any(p.get("status") != "cancelled" for p in posts(f"clip_id={clip['id']}")):
        return True
    # ponytail: reads up to 500 scheduled posts (SupoClip's page cap); page through if you ever queue more.
    taken = {datetime.fromisoformat(p["scheduled_for"].replace("Z", "+00:00")).astimezone().isoformat()
             for p in posts("status=scheduled") if p.get("scheduled_for")}
    when = next_slot(datetime.now().astimezone() + timedelta(minutes=5), taken, cfg["post_times"])
    body = {"task_id": tid, "clip_id": clip["id"], "social_account_id": account["id"],
            "hashtags": cfg["hashtags"], "scheduled_for": when.isoformat()}
    if cfg["privacy"].get(account["provider"]):
        body["privacy_level"] = cfg["privacy"][account["provider"]]
    try:  # AI caption needs ANTHROPIC_API_KEY on the SupoClip server; without it the caption is the clip's transcript
        body["caption"] = api.call("POST", "/social/captions", {
            "task_id": tid, "clip_id": clip["id"], "provider": account["provider"]})["caption"]
    except RuntimeError:
        pass
    try:
        api.call("POST", "/social/posts", body)
    except RuntimeError as e:
        log(f"Couldn't schedule a clip to {account['provider']} ({account.get('display_name')}): {e}")
        return False
    log(f"  -> {account['provider']} {account.get('display_name') or ''} at {when:%a %d %b %H:%M}")
    return True


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
    for step in (submit_inbox, submit_links, schedule_finished, write_status):
        try:
            step(api, cfg, state)
        except (RuntimeError, OSError) as e:  # SupoClip down, disk hiccup: say so and try again next pass
            log(f"{step.__name__} failed, will retry: {e}")
    save(state)


def main():
    cfg = load_config()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"tasks": {}, "sizes": {}}
    api = Api(cfg["api_url"], cfg["api_key"])
    log(f"Watching {cfg['inbox']} and {cfg['links']} -> {cfg['api_url']}")
    while True:
        run_once(api, cfg, state)
        if "--once" in sys.argv:
            break
        time.sleep(60)


def self_test():
    """Fake SupoClip on a local port; one video goes inbox -> task -> scheduled posts, with no duplicates.
    Paths, fields and refusals copy what a real SupoClip returned (checked against a running one, Nov 2026)."""
    import re
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    global STATE
    posts, calls, refuse_once = [], [], {"tt"}

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
                self.reply({"total": 3, "connections": [
                    {"id": i, "provider": p, "display_name": p, "status": "active", "revoked_at": None,
                     "external_account_id": "UC" + "m" * 22}
                    for i, p in (("tt", "tiktok"), ("yt", "youtube"), ("ig", "instagram"))]})
            elif self.path in ("/tasks/T1", "/tasks/T2"):
                self.reply({"id": self.path[-2:], "status": "completed", "progress": 100})
            elif self.path == "/tasks/T1/clips":
                self.reply({"task_id": "T1", "total_clips": 5,
                            "clips": [{"id": f"c{s}", "virality_score": s} for s in (95, 40, 80, 75, 72)]})
            elif self.path == "/tasks/T2/clips":
                self.reply({"task_id": "T2", "total_clips": 1, "clips": [{"id": "L1", "virality_score": 90}]})
            elif self.path.startswith("/social/posts?"):
                q = dict(kv.split("=") for kv in self.path.split("?")[1].split("&"))
                found = [p for p in posts if all(p.get(k) == v for k, v in q.items() if k != "limit")]
                self.reply({"posts": found, "total": len(found)})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            if self.path == "/upload":
                assert b"FAKEVIDEO" in raw and b'name="video"' in raw
                assert re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", self.headers["Idempotency-Key"])
                self.reply({"message": "Video uploaded successfully", "video_path": "upload://x.mp4"})
            elif self.path == "/tasks/":
                url = json.loads(raw)["source"]["url"]
                assert url in ("upload://x.mp4", "https://www.youtube.com/watch?v=" + "a" * 11), url
                self.reply({"task_id": "T1" if url.startswith("upload") else "T2"})
            elif self.path == "/social/captions":
                self.send_error(503)
            elif self.path == "/social/posts":
                post = {**json.loads(raw), "status": "scheduled"}
                if post["social_account_id"] in refuse_once:  # e.g. "TikTok publishing is paused"
                    refuse_once.clear()
                    return self.send_error(503)
                posts.append(post)
                self.reply({"post": post})

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as d:
        STATE = Path(d) / "state.json"
        (Path(d) / "in").mkdir()
        (Path(d) / "in" / "my vod (1).mp4").write_bytes(b"FAKEVIDEO")
        cfg = {**DEFAULTS, "inbox": str(Path(d) / "in"), "privacy": {"youtube": "private"},
               "links": str(Path(d) / "links.txt")}
        api, state = Api(f"http://127.0.0.1:{srv.server_port}", "sk_test"), {"tasks": {}, "sizes": {}}
        run_once(api, cfg, state)                       # pass 1: file seen, size recorded, not uploaded yet
        assert "/upload" not in calls
        run_once(api, cfg, state)                       # pass 2 uploads + schedules; one post is refused
        assert len(posts) == 5 and state["tasks"]["T1"]["state"] == "processing"
        for _ in range(3):                              # pass 3 retries just that one; later passes change nothing
            run_once(api, cfg, state)
        assert calls.count("/upload") == 1 and (Path(d) / "in" / "done" / "my vod (1).mp4").exists()
        assert len(posts) == 6, posts                   # top 3 (95, 80, 75) x youtube + tiktok, never instagram
        assert {p["clip_id"] for p in posts} == {"c95", "c80", "c75"}
        for acc in ("yt", "tt"):
            slots = [p["scheduled_for"] for p in posts if p["social_account_id"] == acc]
            assert len(set(slots)) == 3, slots          # one post per slot per account
        assert all(p.get("privacy_level") == "private" for p in posts if p["social_account_id"] == "yt")
        assert state["tasks"]["T1"]["state"] == "scheduled"
        # links.txt: only a video published by the connected channel is clipped; everything else is skipped
        global channel_of
        channel_of = lambda vid: "UC" + ("m" if vid[0] == "a" else "x") * 22  # noqa: E731
        own, other = "https://youtu.be/" + "a" * 11, "https://www.youtube.com/watch?v=" + "b" * 11
        Path(cfg["links"]).write_text(f"# mine\nhttps://www.twitch.tv/videos/123\n{other}\n{own}\n")
        for _ in range(3):
            run_once(api, cfg, state)
        assert calls.count("/tasks/") == 2 and state["links"][own] == "started"
        assert state["links"][other].startswith("skipped") and len(state["links"]) == 3
        assert {p["clip_id"] for p in posts} == {"c95", "c80", "c75", "L1"} and len(posts) == 8
        cfg["buffer_days"] = 0                          # queue "full": a new link waits
        Path(cfg["links"]).write_text("https://youtu.be/" + "a" * 10 + "c\n")
        run_once(api, cfg, state)
        assert calls.count("/tasks/") == 2
    assert video_id("https://www.youtube.com/shorts/abcdefghijk?x=1") == "abcdefghijk"
    assert video_id("https://www.youtube.com/watch?t=5&v=abcdefghijk") == "abcdefghijk" and not video_id("https://x.com/a")
    now = datetime(2026, 1, 1, 13, 0).astimezone()
    assert next_slot(now, set(), ["12:00", "18:00"]).hour == 18
    assert next_slot(now, {next_slot(now, set(), ["12:00", "18:00"]).isoformat()}, ["12:00", "18:00"]).day == 2
    print("self-test passed")


if __name__ == "__main__":
    self_test() if "--test" in sys.argv else main()
