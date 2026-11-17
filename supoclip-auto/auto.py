"""Hands-off automation for a self-hosted SupoClip (github.com/FujiwaraChoki/supoclip).

Drop a video into the inbox folder, or paste links to your own YouTube videos into links.txt
-> SupoClip makes clips -> the best ones are scheduled to every connected YouTube/TikTok account
in your daily time slots. Standard library only.

    python auto.py            run forever (checks every minute)
    python auto.py --once     one pass, then exit
    python auto.py --test     self-check against a fake SupoClip (no network)
    python auto.py --twitch-login   connect a Twitch channel you own (once per channel)

Settings live in config.json next to this file (created on first run).
"""
import hashlib
import json
import mimetypes
import os
import re
import socket
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
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
    "buffer_days": 3,
    "chat_bonus": 20,                   # score points added to a clip when your Twitch chat spiked during it
    "twitch_client_id": "",             # from dev.twitch.tv; redirect URL http://localhost:3000/api/twitch/callback
    "twitch_client_secret": "",                   # start the next link only when fewer than this many days of posts are queued
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


TWITCH_REDIRECT = "http://localhost:3000/api/twitch/callback"


def twitch_vod_id(url):
    m = re.search(r"twitch\.tv/(?:[\w]+/)?videos?/(\d+)", url)
    return m.group(1) if m else None


def twitch_get(cfg, state, path):
    """Twitch Helix GET with an app token (client credentials), refreshed when it expires."""
    tok = state.get("twitch_app_token") or {}
    if tok.get("expires", 0) < time.time() + 300:
        body = urllib.parse.urlencode({"client_id": cfg["twitch_client_id"], "client_secret": cfg["twitch_client_secret"],
                                       "grant_type": "client_credentials"}).encode()
        with urllib.request.urlopen("https://id.twitch.tv/oauth2/token", body, timeout=30) as r:
            t = json.loads(r.read())
        tok = state["twitch_app_token"] = {"token": t["access_token"], "expires": time.time() + t["expires_in"]}
    req = urllib.request.Request("https://api.twitch.tv/helix/" + path, headers={
        "Client-Id": cfg["twitch_client_id"], "Authorization": f"Bearer {tok['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["data"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            state.pop("twitch_app_token", None)
        raise RuntimeError(f"Twitch {path.split('?')[0]} -> {e.code}") from None


CHAT = HERE / "chat"


def record_chat(login):
    """Logs the time of every chat message in your channel (read-only, anonymous). Runs while auto.py runs."""
    CHAT.mkdir(exist_ok=True)
    while True:
        try:
            raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=360)
            with ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv") as sock, \
                    sock.makefile("r", encoding="utf-8", errors="replace") as lines, \
                    open(CHAT / f"{login}.txt", "a", encoding="utf-8") as out:
                sock.sendall(f"NICK justinfan{uuid.uuid4().int % 90000 + 10000}\r\nJOIN #{login}\r\n".encode())
                for line in lines:
                    if line.startswith("PING"):
                        sock.sendall(line.replace("PING", "PONG", 1).encode())
                    elif " PRIVMSG " in line:
                        out.write(f"{time.time():.0f}\n")
                        out.flush()
        except OSError as e:
            log(f"Twitch chat for {login} disconnected ({e}); reconnecting in 30 s")
        time.sleep(30)


def hms(seconds):
    return f"{int(seconds) // 3600}:{int(seconds) % 3600 // 60:02d}:{int(seconds) % 60:02d}"


def chat_spikes(stamps, start, length, window=30, top=8):
    """Offsets (s into the VOD) of the busiest chat moments: windows well above the stream's normal chat rate."""
    counts = [0] * (int(length // window) + 1)
    for t in stamps:
        if start <= t < start + length:
            counts[int((t - start) // window)] += 1
    if sum(counts) < 50:  # too little chat to tell a spike from noise
        return []
    bar = statistics.mean(counts) + 2 * statistics.pstdev(counts)
    hot = sorted((i for i, c in enumerate(counts) if c >= bar and c >= 5), key=lambda i: -counts[i])
    picked = []
    for i in hot:  # one spike per 2 minutes: a single moment fills several windows
        if all(abs(i - j) * window >= 120 for j in picked):
            picked.append(i)
    # ponytail: chat reacts a few seconds after the moment; window start is close enough for clip matching
    return sorted(i * window for i in picked[:top])


def vod_spikes(login, video):
    f = CHAT / f"{login}.txt"
    if not f.exists():
        return []
    start = datetime.fromisoformat(video["created_at"].replace("Z", "+00:00")).timestamp()
    length = sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([hms])", video["duration"]))
    stamps = [float(x) for x in f.read_text().split()]
    return chat_spikes(stamps, start, length)


def clip_seconds(value):
    """SupoClip clip times are "MM:SS" (or "H:MM:SS"); returns seconds."""
    total = 0
    for part in str(value).split(":"):
        total = total * 60 + float(part)
    return total


def twitch_login(cfg, state):
    """Proves you own a Twitch channel: you sign in to Twitch, Twitch tells us which channel that is."""
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    if not (cfg["twitch_client_id"] and cfg["twitch_client_secret"]):
        sys.exit("Put twitch_client_id and twitch_client_secret in config.json first.")
    csrf, got = uuid.uuid4().hex, {}

    class Back(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state") == [csrf] and q.get("code"):
                got["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done. You can close this tab.".encode() if got else b"Twitch login failed or was cancelled.")

    class DualStack(HTTPServer):  # "localhost" may resolve to ::1 or 127.0.0.1 depending on the browser
        address_family = socket.AF_INET6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    srv = DualStack(("::", 3000), Back)
    url = "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": cfg["twitch_client_id"], "redirect_uri": TWITCH_REDIRECT, "response_type": "code",
        "scope": "", "state": csrf, "force_verify": "true"})
    print(f"Sign in to the Twitch channel you want to clip. If no browser opens, visit:\n{url}")
    webbrowser.open(url)
    while "code" not in got:
        srv.handle_request()
    body = urllib.parse.urlencode({"client_id": cfg["twitch_client_id"], "client_secret": cfg["twitch_client_secret"],
                                   "code": got["code"], "grant_type": "authorization_code",
                                   "redirect_uri": TWITCH_REDIRECT}).encode()
    with urllib.request.urlopen("https://id.twitch.tv/oauth2/token", body, timeout=30) as r:
        user_token = json.loads(r.read())["access_token"]
    req = urllib.request.Request("https://api.twitch.tv/helix/users", headers={
        "Client-Id": cfg["twitch_client_id"], "Authorization": f"Bearer {user_token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        me = json.loads(r.read())["data"][0]
    state.setdefault("twitch_users", {})[me["id"]] = me["login"]
    state.setdefault("twitch_since", datetime.now().astimezone().isoformat())
    save(state)
    print(f"Connected Twitch channel {me['login']}. Its new streams will be clipped after they end.")


def watch_twitch(api, cfg, state):
    """Every 10 min: each finished stream (VOD) of your connected Twitch channels joins the link queue."""
    users = state.get("twitch_users") or {}
    if not users or time.time() - state.get("twitch_checked", 0) < 600:
        return
    state["twitch_checked"] = time.time()
    since = state["twitch_since"]
    seen = state.setdefault("twitch_seen", [])
    for uid, login in users.items():
        live = {s.get("id") for s in twitch_get(cfg, state, f"streams?user_id={uid}")}
        for v in twitch_get(cfg, state, f"videos?user_id={uid}&type=archive&first=10"):
            created = datetime.fromisoformat(v["created_at"].replace("Z", "+00:00"))
            if v["id"] in seen or created < datetime.fromisoformat(since) or v.get("stream_id") in live:
                continue  # already queued, older than your Twitch login, or still live (clipped once it ends)
            seen.append(v["id"])
            with open(cfg["links"], "a", encoding="utf-8") as f:
                f.write(f"https://www.twitch.tv/videos/{v['id']}\n")
            log(f"New {login} stream \"{v.get('title', '')}\" ({v.get('duration')}) added to the link queue")
    save(state)


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
        vid, tv = video_id(url), twitch_vod_id(url)
        if tv:
            users = state.get("twitch_users") or {}
            if not users:  # wait for the login, but don't hold up the YouTube links behind it
                if not state.get("warned_twitch"):
                    state["warned_twitch"] = True
                    log("links.txt: run  python auto.py --twitch-login  to connect your Twitch channel. "
                        "Twitch links wait until then.")
                continue
            found = twitch_get(cfg, state, f"videos?id={tv}")
            if found and found[0]["user_id"] in users:
                spikes = vod_spikes(users[found[0]["user_id"]], found[0])
                body = {"source": {"url": f"https://www.twitch.tv/videos/{tv}"}, "clip_length": cfg["clip_length"]}
                try:  # SupoClip's AI reads this as editorial guidance; the pick below doesn't depend on it
                    hint = ("Chat exploded at " + ", ".join(map(hms, spikes)) + ". Favor clips that include these moments.")
                    task = api.call("POST", "/tasks/", {**body, "instructions": hint[:500]} if spikes else body)
                except RuntimeError:
                    task = api.call("POST", "/tasks/", body)
                if spikes:
                    log(f"Chat spikes in {url}: {', '.join(map(hms, spikes))}")
                state["tasks"][task["task_id"]] = {"name": url, "state": "processing", "spikes": spikes}
                links[url] = "started"
                log(f"SupoClip is clipping {url} (task {task['task_id']}); {len(todo) - 1} link(s) still waiting")
                save(state)
                return
            links[url] = ("skipped: not a stream of a Twitch channel you connected" if found
                          else "skipped: Twitch says this VOD doesn't exist (deleted or expired)")
        elif not vid:
            links[url] = "skipped: not a YouTube or Twitch VOD link (download other videos of yours into inbox)"
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
            def score(c):  # SupoClip's virality score, +chat_bonus if chat spiked during the clip
                hit = any(clip_seconds(c["start_time"]) - 10 <= s <= clip_seconds(c["end_time"]) + 10
                          for s in t.get("spikes", [])) if c.get("start_time") else False
                return (c.get("virality_score") or 0) + (cfg["chat_bonus"] if hit else 0)
            picks = [c for c in sorted(clips, key=lambda c: -score(c))[:cfg["top_n"]] if score(c) >= cfg["min_score"]]
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
    for step in (submit_inbox, watch_twitch, submit_links, schedule_finished, write_status):
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
    for login in (state.get("twitch_users") or {}).values():
        threading.Thread(target=record_chat, args=(login,), daemon=True).start()
        log(f"Recording Twitch chat activity for {login}")
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
    t3_done = False

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
            elif self.path == "/tasks/T3":
                self.reply({"id": "T3", "status": "completed" if t3_done else "processing", "progress": 10})
            elif self.path == "/tasks/T3/clips":  # x2 scores lower but chat spiked during it (at 10:00)
                self.reply({"task_id": "T3", "clips": [
                    {"id": "x1", "virality_score": 90, "start_time": "00:00", "end_time": "00:30"},
                    {"id": "x2", "virality_score": 75, "start_time": "09:50", "end_time": "10:20"}]})
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
                assert url in ("upload://x.mp4", "https://www.youtube.com/watch?v=" + "a" * 11,
                               "https://www.twitch.tv/videos/1"), url
                self.reply({"task_id": {"u": "T1", "h": "T2"}[url[0]] if "twitch" not in url else "T3"})
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
        assert state["links"][other].startswith("skipped") and len(state["links"]) == 2  # Twitch link waits for login
        assert {p["clip_id"] for p in posts} == {"c95", "c80", "c75", "L1"} and len(posts) == 8
        # Twitch: a finished stream of the connected channel is queued and clipped; a live one and others' VODs aren't
        global twitch_get
        vods = {"1": "me", "2": "me", "3": "other"}
        def fake_twitch(cfg, state, path):
            if path.startswith("streams"):
                return [{"id": "s2"}]
            if path.startswith("videos?user_id"):
                return [{"id": i, "user_id": "me", "stream_id": "s" + i, "title": "t", "duration": "3h",
                         "created_at": "2099-01-01T00:00:00Z"} for i in ("1", "2")]
            vid = path.split("id=")[1]
            return [{"id": vid, "user_id": vods[vid]}] if vid in vods else []
        twitch_get = fake_twitch
        state.update(twitch_users={"me": "mychannel"}, twitch_since="2026-01-01T00:00:00+00:00")
        Path(cfg["links"]).write_text("https://www.twitch.tv/videos/3\n")  # replaces the file: video 123 is gone
        before = calls.count("/tasks/")
        for _ in range(3):
            run_once(api, cfg, state)
        assert state["twitch_seen"] == ["1"], state["twitch_seen"]   # VOD 2 is still live
        assert state["links"]["https://www.twitch.tv/videos/3"].startswith("skipped")
        assert state["links"]["https://www.twitch.tv/videos/1"] == "started" and calls.count("/tasks/") == before + 1
        assert twitch_vod_id("https://www.twitch.tv/me/video/123") == "123"
        nonlocal_cfg = {**cfg, "top_n": 1}
        state["tasks"]["T3"]["spikes"] = [600]
        t3_done = True
        schedule_finished(api, nonlocal_cfg, state)
        assert "x2" in {p["clip_id"] for p in posts} and "x1" not in {p["clip_id"] for p in posts}
        cfg["buffer_days"] = 0                          # queue "full": a new link waits
        Path(cfg["links"]).write_text("https://youtu.be/" + "a" * 10 + "c\n")
        state["tasks"]["T3"]["state"] = "failed"      # nothing processing, so only the full queue holds it back
        n = calls.count("/tasks/")
        run_once(api, cfg, state)
        assert calls.count("/tasks/") == n
    # chat spikes: a quiet stream with two bursts finds exactly those two moments
    stamps = [1000 + i * 20 for i in range(540)] + [1000 + 1800 + i * 0.2 for i in range(100)] \
        + [1000 + 7200 + i * 0.3 for i in range(80)]
    assert chat_spikes(stamps, 1000, 3 * 3600) == [1800, 7200], chat_spikes(stamps, 1000, 3 * 3600)
    assert chat_spikes(stamps[:30], 1000, 3600) == []
    assert clip_seconds("75:30") == 4530 and clip_seconds("1:02:03") == 3723 and hms(3723) == "1:02:03"
    assert video_id("https://www.youtube.com/shorts/abcdefghijk?x=1") == "abcdefghijk"
    assert video_id("https://www.youtube.com/watch?t=5&v=abcdefghijk") == "abcdefghijk" and not video_id("https://x.com/a")
    now = datetime(2026, 1, 1, 13, 0).astimezone()
    assert next_slot(now, set(), ["12:00", "18:00"]).hour == 18
    assert next_slot(now, {next_slot(now, set(), ["12:00", "18:00"]).isoformat()}, ["12:00", "18:00"]).day == 2
    print("self-test passed")


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
    elif "--twitch-login" in sys.argv:
        twitch_login(load_config(), json.loads(STATE.read_text()) if STATE.exists() else {"tasks": {}, "sizes": {}})
    else:
        main()
