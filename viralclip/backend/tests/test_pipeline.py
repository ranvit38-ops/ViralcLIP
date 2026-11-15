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
    # sentence-level segments (batched Whisper returns ~30 s chunks; they must be regrouped)
    assert len(tr["segments"]) >= 8 and max(s["end"] - s["start"] for s in tr["segments"]) < 15

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
    # with the crop-relative threshold used for 9:16, a 7% drift re-frames instead of leaving the face off-center
    drift = [(i * 0.5, 0.5, 0.3) for i in range(4)] + [(2 + i * 0.5, 0.57, 0.3) for i in range(4)]
    assert len(pipeline.crop_track(drift, min_shift=0.15 * 606 / 1920)) == 2 and len(pipeline.crop_track(drift)) == 1


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


def _render_direct(src, layout, monkeypatch, tmp_path, words=None, end=6, **rs):
    from app import config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    clip = type("Clip", (), {"render_settings": {}})()  # captures what _render writes back
    monkeypatch.setattr(pipeline, "SessionLocal", lambda: type("S", (), {
        "__enter__": lambda s: s, "__exit__": lambda s, *a: None, "get": lambda s, *a: clip,
        "commit": lambda s: None})())
    st = pipeline.render_settings(type("C", (), {"render_settings": {"layout": layout, **rs}})())
    if words is None:
        words = [{"start": 0.3, "end": 0.8, "word": "Hello"}, {"start": 0.9, "end": 1.4, "word": "there."},
                 {"start": 3.4, "end": 3.9, "word": "Big"}, {"start": 4.0, "end": 4.6, "word": "moment."}]
    pipeline._render(_Ctx(), src, 1, 1, 0, end, 1920, 1080, 30, st, [{"words": words}], [], "My hook title", True)
    _render_direct.settings = clip.render_settings
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


def test_job_dead_too_often_releases_clip_and_project():
    from datetime import timedelta
    from app.db import SessionLocal
    from app.models import Clip, Job, Project, now
    c = new_client()
    pid, _ = upload(c, speech_video() if shutil.which("espeak-ng") else __import__("tests.conftest").conftest.make_video("a.mp4"))
    with SessionLocal() as db:
        p = db.get(Project, pid)
        p.status = "processing"
        clip = Clip(project_id=pid, start=0, end=5, render_status="rendering")
        db.add(clip)
        db.flush()
        stale = now() - timedelta(hours=1)
        db.add_all([Job(project_id=pid, job_type="analyze", status="running", attempts=3, heartbeat_at=stale),
                    Job(project_id=pid, clip_id=clip.id, job_type="render", status="running", attempts=3, heartbeat_at=stale)])
        db.commit()
        cid = clip.id
    worker.requeue_stale()
    with SessionLocal() as db:
        assert db.get(Clip, cid).render_status == "failed"
        assert db.get(Project, pid).status == "ready"


# ---------------------------------------------------------------- retakes, fillers, cut snapping, active speaker

def _words(text, t0=0.0, step=0.4):
    return [{"start": round(t0 + i * step, 2), "end": round(t0 + i * step + 0.3, 2), "word": w}
            for i, w in enumerate(text.split())]


def test_retakes_and_fillers():
    w = _words("I want to show, I want to show you this.")
    assert pipeline.find_retakes(w) == [(0, 3)]  # the earlier take is cut, the later one kept
    assert pipeline.find_retakes(_words("No no no, that is not it.")) == []  # one-word run = emphasis
    assert pipeline.find_retakes(_words("We tried it and it worked for us.")) == []
    assert pipeline.find_retakes(_words("it was it was")) == [(0, 1)]  # two words is the minimum
    um = _words("I think, um, I think so.")
    fill = pipeline.find_fillers(um)
    assert fill == {2} and pipeline.find_retakes(um, fill) == [(0, 1)]  # a filler doesn't hide a retake
    assert pipeline.find_fillers(_words("Um, so like, uh, I mean Hmm... erm umm")) == {0, 3, 6, 7, 8}
    assert pipeline.find_fillers(_words("umbrella uh-oh hum")) == set()


def test_keep_segments_cut_retakes_and_fillers():
    w = _words("I want to show, I want to show you this.")  # 0.4 s per word, no pauses
    keep = pipeline.keep_segments(w, 4.0, drop={0, 1, 2, 3})
    assert len(keep) == 1 and w[3]["end"] <= keep[0][0] <= w[4]["start"]  # starts after the cut take
    w = _words("So, um, this works.")
    keep = pipeline.keep_segments(w, 2.0, drop={1})
    assert len(keep) == 2 and keep[0][1] <= w[1]["start"] and keep[1][0] >= w[1]["end"]
    assert keep[0][0] == 0.0 and keep[1][1] <= 2.0
    # captions: the cut word is gone, the rest move up on the new timeline
    lines = pipeline.remap_lines(pipeline.caption_lines(w, 6), keep)
    assert [x["word"] for ln in lines for x in ln["words"]] == ["So,", "this", "works."]
    assert " ".join(ln["text"] for ln in lines) == "So, this works."
    assert lines[-1]["words"][-2]["start"] == pytest.approx(w[2]["start"] - (keep[1][0] - keep[0][1]))
    assert pipeline.keep_segments(w, 2.0, drop={0, 1, 2, 3}) == [(0.0, 2.0)]  # nothing left: keep everything


def test_snap_cuts_to_quiet_point():
    import numpy as np
    sr = pipeline.SR
    t = np.arange(int(3 * sr)) / sr
    audio = (0.5 * np.sin(2 * np.pi * 250 * t)).astype(np.float32)
    audio[int(1.05 * sr):int(1.08 * sr)] = 0  # a 30 ms quiet gap 50-80 ms after the planned cut
    words = [{"start": 0.2, "end": 0.9, "word": "a"}, {"start": 1.3, "end": 2.0, "word": "b"}]
    keep = [(0.0, 1.0), (1.25, 3.0)]
    out = pipeline.snap_cuts(keep, words, audio, sr)
    assert 1.05 <= out[0][1] <= 1.08  # moved into the quiet gap
    assert out[1][0] == pytest.approx(1.25, abs=0.003)  # evenly loud (whole 250 Hz periods): nearest the original
    # never into a word body: a quiet gap inside word "a" is ignored
    audio2 = (0.5 * np.sin(2 * np.pi * 250 * t)).astype(np.float32)
    audio2[int(0.85 * sr):int(0.88 * sr)] = 0
    assert pipeline.snap_cuts(keep, words, audio2, sr)[0][1] >= 0.9


def test_speaker_switching_with_hysteresis():
    import numpy as np
    fps, n = 10, 80
    rng = np.random.default_rng(0)
    a = 0.3 + 0.1 * rng.random(n)
    b = 0.3 + 0.1 * rng.random(n)
    a[:40] += 2.0  # A talks for 4 s, then B
    b[40:] += 2.0
    b[15:19] += 3.0  # B's 0.4 s interjection while A talks: too short to cut to
    segs = pipeline.speaker_segments(np.stack([a, b]), fps)
    assert [p for *_, p in segs] == [0, 1] and 3.8 <= segs[1][0] <= 4.4
    # similar levels (within MARGIN): no flapping
    flat = np.stack([1.0 + 0.05 * rng.random(n), 1.0 + 0.05 * rng.random(n)])
    assert len(pipeline.speaker_segments(flat, fps)) == 1
    # someone who never talks doesn't win on normalized noise
    quiet = np.stack([2.0 + rng.random(n), 0.05 + 0.02 * rng.random(n)])
    assert [p for *_, p in pipeline.speaker_segments(quiet, fps)] == [0]
    # two clusters of faces -> two people; a one-off false detection is ignored
    dets = [(i / 2, [(0.2, 0.4, 0.1, 0.18), (0.7, 0.4, 0.1, 0.18)] + ([(0.45, 0.1, 0.05, 0.08)] if i == 3 else []))
            for i in range(10)]
    ppl = pipeline.people(dets)
    assert len(ppl) == 2 and ppl[0][0] == pytest.approx(0.2) and ppl[1][0] == pytest.approx(0.7)
    assert pipeline.speaker_track([(0, [(0.4, 0.4, 0.1, 0.18)])] * 4, np.zeros((20, 33, 60))) is None  # one person


def _two_person_video():
    # Lena on the left, mirrored Lena on the right; a patch of changing noise over the left mouth for 0-3 s,
    # then over the right mouth for 3-6 s.
    return _face_video("twoperson.mp4",
                       "[0:v]drawbox=c=0x606060:t=fill[bg];[1:v]scale=520:520,split[a][b];[b]hflip[bm];"
                       "[bg][a]overlay=180:280[t1];[t1][bm]overlay=1220:280[t2];"
                       "color=c=0x806060:s=90x40:r=30,noise=alls=100:allf=t+u,split[n1][n2];"
                       "[t2][n1]overlay=405:615:enable='lt(t,3)'[t3];[t3][n2]overlay=1425:615:enable='gte(t,3)'[v]")


def _gray(path, t, vf=""):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        "-vf", f"{vf}scale=54:96,format=gray", "-f", "rawvideo", "pipe:"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(float)


def test_two_person_crop_follows_active_speaker(monkeypatch, tmp_path):
    src = _two_person_video()
    out = _render_direct(src, "auto", monkeypatch, tmp_path, words=[], captions=False, hook=False)
    assert _render_direct.settings["_rendered_layout"] == "crop"
    # a 9:16 crop of 1080p is 606 px wide; the faces are centred at ~x=490 (left) and ~x=1430 (right)
    left, right = "crop=606:1080:186:0,", "crop=606:1080:1131:0,"
    for t, want in ((1.5, left), (4.5, right)):
        got = _gray(out, t)
        err = {v: float(((got - _gray(src, t, v)) ** 2).mean()) for v in (left, right)}
        assert min(err, key=err.get) == want and err[want] < 0.25 * max(err.values()), (t, err)


def test_render_cuts_retakes_and_fillers(monkeypatch, tmp_path):
    src = _face_video("talkinghead.mp4", "[1:v]scale=-2:700[f];[0:v][f]overlay=x=700:y=190:shortest=1[v]")
    words = _words("So I want to show, I want to show you, um, this.", t0=0.2)  # 12 words, 0.2-4.9 s
    out = _render_direct(src, "auto", monkeypatch, tmp_path, words=words)
    rs = _render_direct.settings
    assert rs["_retakes_cut"] == 1 and rs["_fillers_cut"] == 1 and rs["_cuts"] == 2
    # the earlier take (4 words, 1.6 s) and "um," are gone: ~3.3 s instead of ~5.3 s
    assert 3.0 < float(probe(out)["format"]["duration"]) < 3.6
