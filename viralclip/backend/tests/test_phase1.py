from app import config
from tests.conftest import make_video, new_client, upload


def test_auth_flow(client):
    assert client.get("/api/auth/me").json()["email"].endswith("@example.com")
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": client.email, "password": "password123"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/login", json={"email": client.email, "password": "nope-nope"}).status_code == 401


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"email": "bad", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_upload_extracts_metadata(client):
    pid, r = upload(client, make_video("a.mp4", seconds=3))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "uploaded"
    assert abs(p["duration"] - 3) < 0.2
    assert (p["width"], p["height"]) == (320, 240)
    assert p["fps"] == 25 and p["has_audio"] and p["video_codec"] == "h264"
    assert client.get(f"/api/projects/{pid}/thumbnail").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/projects/{pid}/source").status_code == 200
    # idempotent complete
    assert client.post(f"/api/projects/{pid}/upload/complete").json()["status"] == "uploaded"


def test_resume_and_duplicate_chunk(client):
    data = make_video("b.mp4").read_bytes()
    pid = client.post("/api/projects", json={"name": "x", "filename": "b.mp4", "size": len(data)}).json()["id"]
    assert client.put(f"/api/projects/{pid}/upload?offset=0", content=data[:1000]).status_code == 200
    # replaying the same chunk (network retry) is rejected and reports where to resume
    r = client.put(f"/api/projects/{pid}/upload?offset=0", content=data[:1000])
    assert r.status_code == 409 and r.json()["detail"]["offset"] == 1000
    assert client.get(f"/api/projects/{pid}/upload").json()["offset"] == 1000
    # complete before all bytes arrive is refused
    assert client.post(f"/api/projects/{pid}/upload/complete").status_code == 409
    assert client.put(f"/api/projects/{pid}/upload?offset=1000", content=data[1000:]).status_code == 200
    assert client.post(f"/api/projects/{pid}/upload/complete").status_code == 200


def test_rejects_bad_files(client):
    assert client.post("/api/projects", json={"name": "x", "filename": "evil.exe", "size": 10}).status_code == 422
    big = config.MAX_UPLOAD_BYTES + 1
    assert client.post("/api/projects", json={"name": "x", "filename": "a.mp4", "size": big}).status_code == 413
    bad = make_video("c.mp4").parent / "corrupt.mp4"
    bad.write_bytes(b"\x00not a video" * 500)
    pid, r = upload(client, bad)
    assert r.status_code == 422
    assert client.get(f"/api/projects/{pid}").json()["status"] == "failed"


def test_no_audio_flagged(client):
    _, r = upload(client, make_video("silent.mp4", audio=False))
    assert r.status_code == 200 and r.json()["has_audio"] is False and "no audio" in r.json()["error"]


def test_webm_accepted(client):
    src = make_video("a.mp4")
    import subprocess
    webm = src.parent / "a.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libvpx-vp9", "-b:v", "200k",
                    "-c:a", "libopus", str(webm)], check=True)
    _, r = upload(client, webm)
    assert r.status_code == 200 and r.json()["video_codec"] == "vp9"


def test_user_isolation():
    a, b = new_client(), new_client()
    pid, _ = upload(a, make_video("a.mp4"))
    for method, url in [("get", f"/api/projects/{pid}"), ("get", f"/api/projects/{pid}/source"),
                        ("get", f"/api/projects/{pid}/thumbnail"), ("delete", f"/api/projects/{pid}"),
                        ("patch", f"/api/projects/{pid}")]:
        kw = {"json": {"name": "pwned"}} if method == "patch" else {}
        assert getattr(b, method)(url, **kw).status_code == 404, url
    assert b.get("/api/projects").json() == []
    assert a.get(f"/api/projects/{pid}").json()["name"] == "Test"


def test_rename_archive_delete(client):
    pid, _ = upload(client, make_video("a.mp4"))
    assert client.patch(f"/api/projects/{pid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    client.patch(f"/api/projects/{pid}", json={"archived": True})
    assert client.get("/api/projects").json() == []
    assert len(client.get("/api/projects?archived=true").json()) == 1
    assert client.get("/api/dashboard").json()["projects"] == 0
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert not (config.DATA_DIR / "projects" / str(pid)).exists()


def test_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)
    for url in ["/api/projects", "/api/dashboard", "/api/projects/1"]:
        assert anon.get(url).status_code == 401


def test_login_throttled_after_repeated_failures():
    from app import auth
    c = new_client()
    auth._failures.clear()
    for _ in range(auth.MAX_FAILURES):
        assert c.post("/api/auth/login", json={"email": c.email, "password": "wrong-pass"}).status_code == 401
    # even the right password is refused while locked out
    assert c.post("/api/auth/login", json={"email": c.email, "password": "password123"}).status_code == 429
    auth._failures.clear()
