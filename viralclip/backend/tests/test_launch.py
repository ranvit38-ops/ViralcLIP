"""Startup and launcher regressions (run in subprocesses: they need their own environment / module state)."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_sqlite_folder_is_created_when_data_dir_is_elsewhere(tmp_path):
    # .env.example's DATABASE_URL=sqlite:///./data/... with VC_DATA_DIR on another drive used to crash the API.
    env = {**os.environ, "VC_DATA_DIR": str(tmp_path / "media"), "DATABASE_URL": f"sqlite:///{tmp_path}/db/sub/vc.db"}
    r = subprocess.run([sys.executable, "-c", "from app.db import engine; engine.connect().close()"],
                       cwd=BACKEND, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "db" / "sub" / "vc.db").exists()


def test_stop_waits_for_the_launcher_to_exit(tmp_path):
    # `run.py --stop` used to return while every part was still shutting down, so an immediate restart saw the
    # old instance as "already running" and exited, leaving nothing running.
    import importlib.util
    import threading
    import time
    spec = importlib.util.spec_from_file_location("vc_run", BACKEND.parent / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    slow = ("import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1.5), sys.exit(0)))\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-c", slow], stdout=subprocess.PIPE, text=True)
    p.stdout.readline()
    threading.Thread(target=p.wait, daemon=True).start()  # reap it, as init reaps a detached launcher
    pidfile = tmp_path / "launcher.pid"
    pidfile.write_text(str(p.pid))
    t0 = time.time()
    run.stop_background(pidfile)
    assert time.time() - t0 >= 1.0
    assert p.poll() is not None
    assert not pidfile.exists()
