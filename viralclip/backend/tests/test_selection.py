"""Selection heuristics: reaction/banter scoring and spectral-flux impact detection (ported from Clipify)."""
import numpy as np

from app import pipeline, selection

SR = 16000


def segs(lines, gap=0.3, wdur=0.3, t=0.0):
    """Word-timestamped segments from sentences, `gap` seconds of silence between sentences."""
    words = []
    for line in lines:
        for w in line.split():
            words.append({"start": round(t, 2), "end": round(t + wdur, 2), "word": " " + w})
            t += wdur
        t += gap
    return [{"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(lines), "words": words}]


def window_score(lines, **kw):
    sents = selection.sentences(segs(lines, **kw))
    return selection.score_window(sents, None, 0)


def test_banter_and_laughter_outrank_a_flat_window():
    flat = ["The meeting is scheduled for the afternoon in the second room.",
            "We will review the quarterly numbers and the budget for the office.",
            "After that the team will discuss the plan for the next months."]
    banter = ["Did you really eat the whole thing?", "Bro, no way.", "Haha I did.", "Are you serious?",
              "Every single bite.", "Oh my god."]
    fs, fb, _ = window_score(flat)
    bs, bb, reason = window_score(banter)
    assert fb["reaction"] == 0 and bb["reaction"] == 1.0
    assert bs > fs
    assert "big reaction" in reason and "quick back-and-forth" in reason and "laughter" in reason
    assert abs(sum(selection.WEIGHTS.values()) - 1) < 1e-9
    # Whisper-style laughter tokens
    assert selection.reaction_signals(selection.sentences(segs(["[laughter] That was close."])))["laugh"]
    # "what" inside a normal sentence is not a reaction; a bare "What?" is
    assert selection.reaction_signals(selection.sentences(segs(["So what should you do about your savings?"])))["hits"] == 0
    assert selection.reaction_signals(selection.sentences(segs(["What?"])))["hits"] == 1


def test_setup_question_then_short_answer_is_a_reversal():
    sig = selection.reaction_signals(selection.sentences(segs(
        ["How did you finally fix the production outage last night?", "I didn't.", "It fixed itself somehow."])))
    assert sig["reversal"] and not sig["banter"]
    sig = selection.reaction_signals(selection.sentences(segs(
        ["How did you fix it?", "We rolled back the deploy and then rebuilt the cache from scratch."])))
    assert not sig["reversal"]


def noisy(seconds, rng):
    return (rng.standard_normal(seconds * SR) * 0.01).astype(np.float32)


def test_transients_found_in_noise_and_steady_tones_ignored():
    rng = np.random.default_rng(0)
    x = noisy(60, rng)
    t = np.arange(len(x)) / SR
    tone = slice(10 * SR, 30 * SR)
    x[tone] += 0.3 * np.sin(2 * np.pi * 2000 * t[tone])  # in-band steady tone
    for c in np.arange(40.25, 45, 0.5):  # ten 20 ms noise bursts
        i = int(c * SR)
        x[i:i + 320] += rng.standard_normal(320) * 0.5
    imp = selection.impact_per_second(x, SR)
    assert len(imp) == 60
    assert all(v >= 2 for v in imp[40:45])  # each burst second has hits
    assert sum(imp[12:29]) == 0  # the steady part of the tone is nothing
    assert sum(imp[:9]) == 0 and sum(imp[46:]) == 0  # plain noise is nothing
    assert selection.impact_per_second(np.zeros(100, np.float32), SR) == []


def test_gameplay_fallback_prefers_impact_burst_over_long_loud_tone():
    rng = np.random.default_rng(1)
    n = 150
    x = noisy(n, rng)
    t = np.arange(len(x)) / SR
    x[20 * SR:50 * SR] += 0.3 * np.sin(2 * np.pi * 2000 * t[20 * SR:50 * SR])  # 30 s drone, loudest thing per second
    for c in np.arange(100, 108, 0.3):  # gunfire: 30 ms bursts, ~3 per second
        i = int(c * SR)
        x[i:i + 480] += rng.standard_normal(480) * 0.6
    loud, imp = pipeline.loudness_per_second(x), selection.impact_per_second(x, SR)
    old = selection.select_clips([], n, 15, 30, 1, loud)  # loudness only: the drone wins
    assert old[0]["start"] < 50
    new = selection.select_clips([], n, 15, 30, 1, loud, imp)
    assert len(new) == 1 and new[0]["start"] <= 100 and new[0]["end"] >= 107, new
    assert new[0]["breakdown"]["impact_density"] == 1.0 and "impacts" in new[0]["reason"]


def test_impact_density_lifts_speech_energy():
    lines = ["Here is the plan for today.", "We push the left side now.", "Then we hold the bridge until they come."]
    sents = selection.sentences(segs(lines))
    impact = [0.0] * 60
    _, calm, _ = selection.score_window(sents, None, 0, impact, 1.0)
    impact[1:6] = [8.0] * 5
    _, hype, _ = selection.score_window(sents, None, 0, impact, 1.0)
    assert hype["energy"] > calm["energy"]
