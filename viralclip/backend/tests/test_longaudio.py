"""Long-VOD handling: streamed audio, chunked transcription stitched at silences, ffmpeg timeouts."""
import difflib
import time

import numpy as np
import pytest

from app import config, longaudio, pipeline, selection
from app.worker import UserError
from tests.test_pipeline import needs_tts, speech_video

SR = pipeline.SR


class Ctx:
    def __init__(self):
        self.calls = []

    def progress(self, f, m=None):
        self.calls.append(f)


def test_impact_stream_matches_whole_array():
    rng = np.random.default_rng(3)
    x = (rng.standard_normal(SR * 20) * 0.02).astype(np.float32)
    for c in np.arange(5.1, 15, 0.7):
        x[int(c * SR):int(c * SR) + 400] += rng.standard_normal(400).astype(np.float32) * 0.5
    s, i = selection.ImpactStream(SR), 0
    for n in [SR, 7, 255, 3 * SR + 13, SR // 3, len(x)]:  # odd sizes, splits mid-frame
        s.add(x[i:i + n])
        i += n
    assert s.result() == selection.impact_per_second(x, SR) and sum(s.result()) > 0


def test_chunks_cut_at_silence_and_tile_the_audio():
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(SR * 50) * 0.3).astype(np.float32)
    x[int(8.3 * SR):int(8.6 * SR)] = 0  # the only quiet spots near each nominal boundary (10 s, 20 s, ...)
    x[int(17.0 * SR):int(17.4 * SR)] = 0
    blocks = (x[i:i + SR] for i in range(0, len(x), SR))
    out = list(longaudio.chunks(blocks, SR, chunk_s=10, overlap_s=1, search_s=4))
    cuts = [k for _, _, k in out]
    assert 8.3 < cuts[0] < 8.6 and 17.0 < cuts[1] < 17.4 and cuts[-1] == float("inf")
    prev = 0.0
    for t0, a, keep_end in out:
        i = int(round(t0 * SR))
        assert np.array_equal(a, x[i:i + len(a)])  # each chunk is the exact source audio at its offset
        assert t0 <= max(0.0, prev - 1) + 1e-9  # starts at least overlap_s before its own range
        assert len(a) <= 13 * SR  # never more than chunk + 2 * overlap + a block
        if keep_end != float("inf"):
            assert t0 + len(a) / SR >= keep_end + 1 - 1e-9  # context after the cut
        prev = keep_end


def test_keep_words_no_loss_or_duplicate_at_a_boundary():
    w = lambda a, b, s: {"start": a, "end": b, "word": s}  # noqa: E731
    # chunk 1 (cut at 10.0) and chunk 2 both hear the overlap, with slightly different timings
    c1 = [w(8.0, 8.5, "one"), w(9.0, 9.6, "two"), w(9.8, 10.3, "three"), w(10.4, 10.9, "four"), w(11.0, 11.4, "fi")]
    c2 = [w(8.1, 8.5, "one"), w(9.0, 9.5, "two"), w(9.7, 10.25, "three"), w(10.35, 10.9, "four"), w(11.0, 11.5, "five")]
    got = longaudio.keep_words(c1, 10.0, -1)
    got += longaudio.keep_words(c2, float("inf"), got[-1]["end"])
    assert [x["word"] for x in got] == ["one", "two", "three", "four", "five"]
    # the straddling word timed just past the cut in chunk 1 and just before it in chunk 2: kept once, not lost
    c1[2] = w(9.95, 10.3, "three")
    c2[2] = w(9.7, 10.2, "three")
    got = longaudio.keep_words(c1, 10.0, -1)
    got += longaudio.keep_words(c2, float("inf"), got[-1]["end"])
    assert [x["word"] for x in got] == ["one", "two", "three", "four", "five"]


@needs_tts
def test_chunked_transcript_matches_whole(monkeypatch):
    """Forced to ~8 s chunks the talk fixture (~38 s) is cut 5 times; compared with one pass over the whole
    file, no word is lost or doubled at the cuts (tiny may hear a word differently with less context)."""
    src = speech_video()
    cuts = []
    real = longaudio.chunks

    def spy(blocks, sr, chunk_s):
        for c in real(blocks, sr, chunk_s, overlap_s=2, search_s=3):
            cuts.append(c[2])
            yield c
    monkeypatch.setattr(longaudio, "chunks", spy)
    loud, imp, (segs, lang) = pipeline.scan_audio(src, 38, "tiny", None, Ctx(), 0, 1, chunk_s=8)
    monkeypatch.setattr(longaudio, "chunks", real)
    loud2, imp2, (whole, _) = pipeline.scan_audio(src, 38, "tiny", None, Ctx(), 0, 1, chunk_s=3600)
    assert len(cuts) >= 5 and lang == "en" and (loud, imp) == (loud2, imp2)
    got, ref = ([w for s in x for w in s["words"]] for x in (segs, whole))
    sm = difflib.SequenceMatcher(a=[pipeline._norm_word(w["word"]) for w in ref],
                                 b=[pipeline._norm_word(w["word"]) for w in got], autojunk=False)
    assert abs(len(got) - len(ref)) <= 3 and sm.ratio() > 0.9, sm.get_opcodes()
    # nothing doubled: every word starts after the previous one's midpoint (and in order)
    assert all(b["start"] >= (a["start"] + a["end"]) / 2 for a, b in zip(got, got[1:]))
    # nothing lost: every one-pass word within 2 s of a cut overlaps a chunked word in time
    near = [r for r in ref if any(abs(r["start"] - c) < 2 for c in cuts)]
    assert len(near) >= 15
    assert all(any(g["start"] < r["end"] and r["start"] < g["end"] for g in got) for r in near)


def test_scan_streams_in_blocks_never_whole(monkeypatch):
    """The whole source is never decoded into one array: blocks of block_s, chunks bounded by chunk_s."""
    from tests.conftest import make_video
    src = make_video("tone60.mp4", seconds=60)
    monkeypatch.setattr(pipeline, "decode_audio", lambda *a: pytest.fail("whole-file decode"))
    blocks, chunk_lens = [], []
    real_stream = longaudio.stream

    def stream(*a, **k):
        for b in real_stream(*a, **{**k, "block_s": 5}):
            blocks.append(len(b))
            yield b
    monkeypatch.setattr(longaudio, "stream", stream)

    def fake_transcribe(chunks, model, language, ctx, lo, hi, duration):
        for _, a, _ in chunks:
            chunk_lens.append(len(a))
        return [], "en"
    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    ctx = Ctx()
    loud, imp, tr = pipeline.scan_audio(src, 60, "tiny", None, ctx, 0, 1, chunk_s=10)
    assert len(blocks) in (12, 13) and max(blocks) == 5 * SR and len(loud) == len(imp) == 60  # AAC pads a few ms
    assert len(chunk_lens) >= 6 and max(chunk_lens) <= (10 + 2 * 2 + 5) * SR
    # cached transcript: features only, still streamed, progress real and monotonic
    blocks.clear()
    assert pipeline.scan_audio(src, 60, None, None, ctx, 0, 1)[2] is None
    assert len(blocks) in (12, 13) and ctx.calls == sorted(ctx.calls) and ctx.calls[-1] == 1.0


@pytest.fixture
def hung_ffmpeg(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(config, "FFMPEG", str(fake))
    arm = longaudio.Watchdog.arm
    monkeypatch.setattr(longaudio.Watchdog, "arm", lambda self, s: arm(self, 0.5))  # every limit -> 0.5 s


def test_hung_ffmpeg_is_killed_with_a_clear_error(hung_ffmpeg, tmp_path):
    for call in [lambda: list(longaudio.stream(config.FFMPEG, "x.mp4", SR)),
                 lambda: pipeline._faces("x.mp4", 0, 30, 1920, 1080),
                 lambda: pipeline._run_ffmpeg(["x"], 30, Ctx(), tmp_path, 0, 1)]:
        t = time.time()
        with pytest.raises(UserError, match="took longer than .* and was stopped"):
            call()
        assert time.time() - t < 5


def test_run_timeout_becomes_user_error():
    with pytest.raises(UserError, match="Making the thumbnail took longer than"):
        longaudio.run(["sleep", "5"], 0.2, "Making the thumbnail")
