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
    rendered = got["render_settings"]["_rendered_duration"]
    assert abs(float(info["format"]["duration"]) - rendered) < 0.3
    assert rendered <= got["duration"]  # jump cuts only ever shorten
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


def test_llm_output_is_sanitized(monkeypatch):
    from app import llm
    monkeypatch.setenv("LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "chat_json", lambda s, u, timeout=180: {
        "titles": ["<title 1>", "?Why savings fail", "为什么储蓄失败", "Start small today"],
        "description": "A clip.", "hashtags": ["#Money Tips", "money tips", "saving", "#"]})
    m = llm.generate_metadata("Here is the biggest mistake people make with savings. Start small.", {"hashtag_count": 4}, "en")
    assert m["titles"][:2] == ["Why savings fail", "Start small today"]
    assert all("<" not in t and not llm.CJK.search(t) for t in m["titles"])
    assert m["hashtags"][:2] == ["moneytips", "saving"] and len(m["hashtags"]) <= 4
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert llm.generate_metadata("Some clip text that is long enough here.", {}, "en")["method"] == "basic"


def test_jump_cuts_and_remap():
    words = [{"start": 0.5, "end": 1.0, "word": "a"}, {"start": 1.1, "end": 1.5, "word": "b"},
             {"start": 3.5, "end": 4.0, "word": "c."}]
    keep = pipeline.keep_segments(words, 5.0)
    assert len(keep) == 2 and keep[0][1] < 2.0 and keep[1][0] > 3.0  # the 2 s pause is cut
    assert pipeline.remap(0.5, keep) == 0.5  # short lead-in kept
    assert abs(pipeline.remap(3.5, keep) - (keep[0][1] - keep[0][0] + 0.08)) < 1e-6
    assert pipeline.remap(2.5, keep) == pytest.approx(pipeline.remap(keep[1][0], keep))  # inside a cut -> snaps forward
    assert pipeline.keep_segments([], 7.0) == [(0.0, 7.0)]


def test_facecam_detection():
    cam = [(i * 0.5, 0.85, 0.08, 0.8) for i in range(10)]  # small, fixed, bottom-right: facecam
    box = pipeline.facecam_box(cam, 1920, 1080, 1080 / 768)
    assert box and box[0] > 1000 and box[1] > 500 and box[0] + box[2] <= 1920 and box[1] + box[3] <= 1080
    talking = [(i * 0.5, 0.5, 0.3, 0.4) for i in range(10)]  # big face: not a facecam
    assert pipeline.facecam_box(talking, 1920, 1080, 1.4) is None
    assert pipeline.facecam_box([(0, None, 0, None)] * 10, 1920, 1080, 1.4) is None


class _Ctx:
    def progress(self, *a, **k):
        pass


def _face_video(name, overlay_filter):
    import urllib.request
    face = FIXTURES / "face.jpg"
    if not face.exists():
        try:
            urllib.request.urlretrieve("https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg", face)
        except OSError:
            pytest.skip("no network for face fixture")
    out = FIXTURES / name
    if not out.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=6",
                        "-loop", "1", "-i", str(face), "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                        "-filter_complex", overlay_filter, "-map", "[v]", "-map", "2:a", "-t", "6",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)], check=True)
    return out


def _render_direct(src, layout, monkeypatch, tmp_path):
    from app import config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "SessionLocal", lambda: type("S", (), {
        "__enter__": lambda s: s, "__exit__": lambda s, *a: None, "get": lambda s, *a: None})())
    st = pipeline.render_settings(type("C", (), {"render_settings": {"layout": layout}})())
    words = [{"start": 0.3, "end": 0.8, "word": "Hello"}, {"start": 0.9, "end": 1.4, "word": "there."},
             {"start": 3.4, "end": 3.9, "word": "Big"}, {"start": 4.0, "end": 4.6, "word": "moment."}]
    pipeline._render(_Ctx(), src, 1, 1, 0, 6, 1920, 1080, 30, st, [{"words": words}], [], "My hook title", True)
    return tmp_path / "projects" / "1" / "clips" / "1.mp4"


def test_stream_layout_detects_facecam(monkeypatch, tmp_path):
    src = _face_video("facecam.mp4", "[1:v]scale=-2:300[f];[0:v][f]overlay=x=1560:y=740:shortest=1[v]")
    out = _render_direct(src, "auto", monkeypatch, tmp_path)
    info = probe(out)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == (1080, 1920)
    assert float(info["format"]["duration"]) < 5.5  # the 2 s pause was cut


def test_talking_head_crop_with_zoom(monkeypatch, tmp_path):
    src = _face_video("talkinghead.mp4", "[1:v]scale=-2:700[f];[0:v][f]overlay=x=700:y=190:shortest=1[v]")
    out = _render_direct(src, "auto", monkeypatch, tmp_path)
    vs = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == (1080, 1920)
