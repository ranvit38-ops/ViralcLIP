import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="vc-test-")
os.environ["VC_DATA_DIR"] = _tmp  # must be set before the app is imported

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FIXTURES = Path(_tmp) / "fixtures"
FIXTURES.mkdir(exist_ok=True)


def make_video(name: str, seconds: float = 3, audio: bool = True, size="320x240") -> Path:
    out = FIXTURES / name
    if not out.exists():
        args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(out)]
        subprocess.run(args, check=True)
    return out


def new_client() -> TestClient:
    c = TestClient(app)
    c.email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = c.post("/api/auth/register", json={"email": c.email, "password": "password123"})
    assert r.status_code == 200, r.text
    return c


def upload(c: TestClient, path: Path, name="Test", chunk=50_000, filename=None):
    data = path.read_bytes()
    r = c.post("/api/projects", json={"name": name, "filename": filename or path.name, "size": len(data)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for off in range(0, len(data), chunk):
        r = c.put(f"/api/projects/{pid}/upload?offset={off}", content=data[off:off + chunk])
        assert r.status_code == 200, r.text
    return pid, c.post(f"/api/projects/{pid}/upload/complete")


@pytest.fixture
def client():
    return new_client()
