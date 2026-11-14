"""Pipeline tests. The end-to-end ones run real ffmpeg + faster-whisper (tiny) on synthesized speech."""
import json
import shutil
import subprocess

import pytest

from app import pipeline, selection, worker
from tests.conftest import FIXTURES, new_client, upload

TALK = """Here is the biggest mistake most people make with their savings. They wait. They wait for the perfect moment,
and the perfect moment never comes. So what should you do instead? Start small. Put away ten dollars every week,
and automate it so you never have to think about it. And that brings me to my next point.
Why do most diets fail? Because people try to change everything at once. You cannot rebuild your whole life
on a Monday morning. The secret is boring. Pick one habit, make it tiny, and repeat it until it becomes automatic."""

needs_tts = pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")


def speech_video():
    out = FIXTURES / "talk.mp4"
    if not out.exists():
        wav = FIXTURES / "talk.wav"
        subprocess.run(["espeak-ng", "-s", "150", "-w", str(wav), TALK.replace("\n", " ")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
                        "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def drain():
    while worker.run_once():
        pass


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@needs_tts
def test_end_to_end_analyze_and_render():
    c = new_client()
    pid, r = upload(c, speech_video(), chunk=1_000_000)
    assert r.status_code == 200
    j = c.post(f"/api/projects/{pid}/analyze", json={"min_len": 10, "max_len": 30, "count": 3, "model": "tiny"})
    assert j.status_code == 202, j.text
    # duplicate submission is refused while the first is pending
    assert c.post(f"/api/projects/{pid}/analyze", json={"min_len": 10, "max_len": 30, "model": "tiny"}).status_code == 409
    drain()
    job = c.get(f"/api/projects/{pid}").json()["jobs"][0]
    assert job["status"] == "done", job
    assert c.get(f"/api/projects/{pid}").json()["status"] == "ready"

    tr = c.get(f"/api/projects/{pid}/transcript").json()
    assert "mistake" in tr["text"].lower() and tr["language"] == "en"

    clips = c.get(f"/api/clips?project_id={pid}").json()
    assert 1 <= len(clips) <= 3
    for a in clips:
        assert 10 <= a["duration"] <= 31, a
        assert a["title"] and a["hashtags"] and a["reason"]
        assert a["transcript_excerpt"].rstrip().endswith((".", "!", "?"))  # no mid-sentence cuts
    for a, b in zip(clips, clips[1:]):
        assert a["end"] <= b["start"]  # no overlapping near-duplicates
    texts = " ".join(x["transcript_excerpt"].lower() for x in clips)
    assert "next point" not in texts  # the transition line is never selected

    clip = clips[0]
    assert c.patch(f"/api/clips/{clip['id']}", json={"review_status": "approved", "title": "My title",
                                                      "render_settings": {"preset": "karaoke"}}).status_code == 200
    assert c.post(f"/api/clips/{clip['id']}/render").status_code == 202
    drain()
    got = c.get(f"/api/clips/{clip['id']}").json()
    assert got["render_status"] == "done", got["job"]
    assert got["title"] == "My title" and got["title_edited"]

    v = c.get(f"/api/clips/{clip['id']}/video")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4"
    out = FIXTURES / "rendered.mp4"
    out.write_bytes(v.content)
    info = probe(out)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == (1080, 1920) and vs["codec_name"] == "h264"
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - got["duration"]) < 0.3
    assert got["render_settings"]["_rendered_layout"] == "fit"  # no faces in test pattern -> full frame, blurred bg

    # regenerating metadata never overwrites an edited title
    assert c.post(f"/api/clips/{clip['id']}/metadata", json={"fields": ["titles", "hashtags"]}).json()["title"] == "My title"
    # changing settings marks the render outdated
    assert c.patch(f"/api/clips/{clip['id']}", json={"render_settings": {"aspect": "1:1"}}).json()["render_status"] == "outdated"
    # zip download
    z = c.post("/api/clips/download", json={"clip_ids": [clip["id"]]})
    assert z.status_code == 200 and z.content[:2] == b"PK"

    # re-analyze keeps the approved clip, replaces untouched suggestions
    c.post(f"/api/projects/{pid}/analyze", json={"min_len": 10, "max_len": 30, "count": 3, "model": "tiny"})
    drain()
    ids = [x["id"] for x in c.get(f"/api/clips?project_id={pid}").json()]
    assert clip["id"] in ids

    # another user can't touch any of it
    other = new_client()
    for url in [f"/api/clips/{clip['id']}", f"/api/clips/{clip['id']}/video", f"/api/projects/{pid}/transcript"]:
        assert other.get(url).status_code == 404
    assert other.post(f"/api/clips/{clip['id']}/render").status_code == 404
    assert other.get("/api/clips").json() == []


def test_cancel_and_retry_queued_job(client):
    pid, _ = upload(client, speech_video() if shutil.which("espeak-ng") else __import__("tests.conftest").conftest.make_video("a.mp4"))
    j = client.post(f"/api/projects/{pid}/analyze", json={"min_len": 10, "max_len": 30, "model": "tiny"}).json()
    assert client.post(f"/api/jobs/{j['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{j['id']}/retry").status_code == 202
    jobs = client.get("/api/jobs?active=true").json()
    for x in jobs:
        client.post(f"/api/jobs/{x['id']}/cancel")


def test_crop_track_is_stable():
    # jittery face around 0.3 then a real move to 0.7
    samples = [(i * 0.5, 0.3 + (0.03 if i % 2 else -0.03), 0.3) for i in range(10)]
    samples += [(5 + i * 0.5, 0.7, 0.3) for i in range(6)]
    samples += [(8.0, None, 0.0)]
    track = pipeline.crop_track(samples)
    assert len(track) == 2 and abs(track[0][1] - 0.3) < 0.05 and abs(track[1][1] - 0.7) < 0.01
    assert track[1][0] == 5.0
    expr = pipeline._crop_x_expr(track, 1920, 606)
    assert expr.startswith("if(lt(t,5.00),") and "," in expr
    assert pipeline.crop_track([(0, None, 0)]) == [(0.0, 0.5)]


def test_ass_captions_highlight_each_word():
    words = [{"start": 0.0, "end": 0.4, "word": "Hello"}, {"start": 0.5, "end": 0.9, "word": "world."},
             {"start": 1.0, "end": 1.4, "word": "Next{bad}"}]
    lines = pipeline.caption_lines(words, 3)
    assert [ln["text"] for ln in lines] == ["Hello world.", "Next{bad}"]
    ass = pipeline.build_ass(lines, 1080, 1920, pipeline.CAPTION_PRESETS["bold"], "lower")
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    assert len(events) == 3 and "HELLO" in events[0] and "{\\c&H0000E6FF" in events[0]
    assert "{bad}" not in ass  # override-tag injection neutralised
    edited = pipeline.lines_from_overrides([{"start": 0, "end": 2, "text": "two words"}])
    assert edited[0]["words"][1]["start"] == 1.0


def test_loudness_fallback_for_gameplay():
    loud = [0.01] * 120
    loud[80:86] = [0.5] * 6
    clips = selection.select_clips([], 120, 15, 30, 2, loud)
    assert len(clips) == 1 and clips[0]["method"] == "loudness" and clips[0]["start"] <= 80 <= clips[0]["end"]
    assert selection.select_clips([], 120, 15, 30, 2, [0.1] * 120) == []  # flat audio: nothing worth clipping
