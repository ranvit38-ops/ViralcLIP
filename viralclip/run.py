#!/usr/bin/env python3
"""ViralClip launcher. No Docker needed. Run: python run.py   (or double-click start.bat / start.sh)

First run: creates the Python environment, installs packages and builds the web app (5-15 min).
Later runs: start in seconds. Re-installs/rebuilds only when the code changed.
Starts the API, 2 background workers and the web app, then opens http://localhost:3000.
Ctrl+C (or closing the window) stops everything.
"""
import argparse
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_ENV = None  # set in main(): environment for setup commands (cache locations)
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
WIN = os.name == "nt"
VENV = BACKEND / ".venv"
VPY = VENV / ("Scripts/python.exe" if WIN else "bin/python")

INSTALL_HELP = {
    "nt": "Open PowerShell and run:\n  winget install Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg\nthen close and reopen the window.",
    "darwin": "Install Homebrew (brew.sh), then run:\n  brew install python@3.12 node ffmpeg",
    "linux": "Run:\n  sudo apt install python3 python3-venv nodejs npm ffmpeg   (Node 20+ needed: see nodejs.org if yours is older)",
}


def say(msg):
    print(f"\033[1;38;5;209m▸\033[0m {msg}" if not WIN else f"> {msg}", flush=True)


def die(msg):
    print(f"\n✖ {msg}\n", flush=True)
    if WIN:
        input("Press Enter to close...")
    sys.exit(1)


def check_tools():
    if sys.version_info < (3, 11):
        die(f"Python 3.11+ is required (you have {sys.version.split()[0]}).\n" + INSTALL_HELP[platform_key()])
    missing = [t for t in ("ffmpeg", "ffprobe", "node", "npm") if not shutil.which(t)]
    if missing:
        die(f"Missing: {', '.join(missing)}.\n" + INSTALL_HELP[platform_key()])
    node_major = int(subprocess.run(["node", "-v"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0])
    if node_major < 20:
        die(f"Node 20+ is required (you have {node_major}).\n" + INSTALL_HELP[platform_key()])


def platform_key():
    return "nt" if WIN else "darwin" if sys.platform == "darwin" else "linux"


def load_env() -> dict:
    """Read viralclip/.env (KEY=value lines). Real environment variables win."""
    env = {}
    f = ROOT / ".env"
    if not f.exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", f)
        say("Created .env from .env.example (add your YouTube/Twitch keys there later).")
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split(" #")[0].strip().strip('"').strip("'")
            if v:
                env[k.strip()] = v
    return {**env, **os.environ}


def digest(*paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        for f in sorted(Path(p).rglob("*")) if Path(p).is_dir() else [Path(p)]:
            if f.is_file() and "node_modules" not in f.parts and ".next" not in f.parts:
                h.update(str(f.relative_to(ROOT)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()


def run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env or RUN_ENV)
    if r.returncode:
        die(f"Command failed: {' '.join(map(str, cmd))}")


def setup_backend():
    # A venv breaks when its folder is moved (e.g. C: -> D:). Detect that and rebuild it.
    if VPY.exists() and subprocess.run([str(VPY), "-c", "import sys"], capture_output=True).returncode != 0:
        say("Python environment was moved or is broken; recreating it...")
        shutil.rmtree(VENV, ignore_errors=True)
    if not VPY.exists():
        say("Creating Python environment (first run only)...")
        run([sys.executable, "-m", "venv", str(VENV)], BACKEND)
    stamp, want = VENV / ".installed", digest(BACKEND / "requirements.txt")
    if not stamp.exists() or stamp.read_text() != want:
        say("Installing Python packages (a few minutes the first time)...")
        run([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip"], BACKEND)
        run([str(VPY), "-m", "pip", "install", "-q", "-r", "requirements.txt"], BACKEND)
        stamp.write_text(want)


def setup_frontend(env, rebuild):
    npm = shutil.which("npm")
    if not (FRONTEND / "node_modules").exists() or rebuild:
        say("Installing web app packages (first run only)...")
        run([npm, "ci"], FRONTEND)
    stamp = FRONTEND / ".next" / ".viralclip-build"
    want = digest(FRONTEND / "app", FRONTEND / "components", FRONTEND / "lib", FRONTEND / "package-lock.json",
                  FRONTEND / "next.config.ts") + env["BACKEND_URL"]
    if rebuild or not stamp.exists() or stamp.read_text() != want:
        say("Building the web app (about a minute)...")
        run([npm, "run", "build"], FRONTEND, env)
        stamp.write_text(want)


def wait_for(url, procs, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if any(p.poll() is not None for p in procs):
            return False
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser(description="Start ViralClip")
    ap.add_argument("--port", type=int, default=3000, help="web app port (default 3000)")
    ap.add_argument("--api-port", type=int, default=8000, help="API port (default 8000)")
    ap.add_argument("--workers", type=int, default=2, help="background workers (default 2)")
    ap.add_argument("--rebuild", action="store_true", help="force reinstall/rebuild of the web app")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    check_tools()
    env = load_env()
    env["BACKEND_URL"] = f"http://127.0.0.1:{a.api_port}"
    env.setdefault("APP_URL", f"http://localhost:{a.port}")
    # Relative paths in .env are relative to backend/ (where the API and workers run).
    env["VC_DATA_DIR"] = str((BACKEND / env.get("VC_DATA_DIR", "data")).resolve())
    # Keep the speech models next to the app (same drive), not in the user's C: profile.
    env.setdefault("HF_HOME", str(Path(env["VC_DATA_DIR"]) / "models"))
    # Download caches too: everything stays on the drive ViralClip lives on.
    env.setdefault("PIP_CACHE_DIR", str(Path(env["VC_DATA_DIR"]) / "cache" / "pip"))
    env.setdefault("npm_config_cache", str(Path(env["VC_DATA_DIR"]) / "cache" / "npm"))
    env["PYTHONUNBUFFERED"] = "1"
    global RUN_ENV
    RUN_ENV = env
    setup_backend()
    setup_frontend(env, a.rebuild)

    logs = Path(env["VC_DATA_DIR"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    procs = []

    def start(name, cmd, cwd):
        f = open(logs / f"{name}.log", "a", encoding="utf-8")
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}
        procs.append(subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, **flags))

    say("Starting ViralClip...")
    # The API creates/upgrades the database; start it first so workers don't race it.
    start("api", [str(VPY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(a.api_port)], BACKEND)
    if not wait_for(f"http://127.0.0.1:{a.api_port}/api/health", procs):
        stop(procs)
        die(f"The API didn't start. See {logs / 'api.log'}")
    for i in range(a.workers):
        start(f"worker{i + 1}", [str(VPY), "-m", "app.worker"], BACKEND)
    next_bin = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
    start("web", [shutil.which("node"), str(next_bin), "start", "-H", "127.0.0.1", "-p", str(a.port)], FRONTEND)
    url = f"http://localhost:{a.port}"
    if not wait_for(url + "/api/health", procs):
        stop(procs)
        die(f"The web app didn't start. See {logs / 'web.log'}")

    print(f"\n  ViralClip is running:  {url}\n  Logs: {logs}\n  Keep this window open (automation only runs while it's open). Ctrl+C to stop.\n", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    raise RuntimeError(f"A ViralClip process stopped unexpectedly (exit {p.returncode}). See {logs}.")
            time.sleep(2)
    except KeyboardInterrupt:
        say("Stopping...")
    except RuntimeError as e:
        print(f"\n✖ {e}", flush=True)
    finally:
        stop(procs)


def stop(procs):
    for p in procs:
        if p.poll() is None:
            try:
                if WIN:
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    main()
