"""Pick standalone, engaging clip windows from a word-timestamped transcript.

Transparent heuristic: every candidate gets a 0-100 score with a per-feature breakdown and a
plain-English reason. It is a ranking aid, not a view-count prediction. If an LLM is configured,
pipeline.py blends its judgement in on top of this.
"""
import re
import statistics

import numpy as np

DANGLING = {"and", "but", "so", "because", "which", "or", "also", "then", "anyway", "plus", "though"}
BACKREF = {"he", "she", "it", "they", "this", "that", "these", "those", "him", "her", "them"}
FILLERS = {"um", "uh", "erm", "hmm", "like", "basically", "literally"}
HOOK_WORDS = {"secret", "mistake", "never", "always", "biggest", "worst", "best", "why", "how", "truth",
              "nobody", "everyone", "stop", "wrong", "actually", "crazy", "insane", "shocking", "real"}
VALUE_WORDS = {"should", "need", "secret", "mistake", "tip", "lesson", "learned", "instead", "because",
               "habit", "rule", "trick", "step", "reason", "story", "remember", "taught", "realized", "never"}
OFFTOPIC = re.compile(r"\b(let me check|check the time|subscribe|like and|see you next|thanks for (watching|listening)|"
                      r"brings me to|my next point|moving on|before we start|sponsor|^anyway\b|^okay\b)\b", re.I)
# Reaction / punchline / banter signals and the spectral-flux impact detector below are ported from
# Clipify (MIT, Copyright (c) 2026 Louise de Sadeleer): SKILL.md Step 1A and scripts/detect_transients.py.
REACTION = re.compile(r"\b(no way|oh my (god|gosh)|are you (serious|kidding( me)?)|omg|wtf|bro|dude|"
                      r"holy (shit|crap|cow)|let'?s go+|fuck\w*|shit\w*|damn\w*|crap)\b", re.I)
SHORT_REACTION = {"what", "wait", "whoa", "wow", "oh", "yo", "huh", "no", "nope", "yes"}  # only as a short sentence
LAUGH = re.compile(r"\b(ha){2,}h?\b|\bhehe\w*|\blol\b|\blmao\b|[\[(]\s*(laugh|chuckl|gigg)\w*\s*[\])]", re.I)
WEIGHTS = {"hook": 0.22, "standalone": 0.2, "completeness": 0.14, "value": 0.16, "pacing": 0.07, "energy": 0.11,
           "reaction": 0.10}


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def sentences(segments: list[dict], max_gap: float = 1.0) -> list[dict]:
    """Regroup words into sentences (terminal punctuation or a long pause ends one)."""
    words = [w for s in segments for w in s.get("words") or []]
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w["word"].strip().endswith((".", "?", "!")) or nxt is None or nxt["start"] - w["end"] > max_gap:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": cur,
                        "text": "".join(x["word"] for x in cur).strip()})
            cur = []
    return out


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def reaction_signals(sents: list[dict]) -> dict:
    """Comedy/reaction cues: reaction words, laughter, question -> short answer, rapid short-sentence banter."""
    nwords = [len(s["words"]) for s in sents]
    hits = sum(len(REACTION.findall(s["text"])) for s in sents)
    hits += sum(n <= 3 and _norm(s["words"][0]["word"]) in SHORT_REACTION for s, n in zip(sents, nwords) if n)
    reversal = any(a["text"].endswith("?") and 0 < nb <= 4 and b["text"].endswith((".", "!"))
                   for a, b, nb in zip(sents, sents[1:], nwords[1:]))
    # ponytail: no diarization, so "back-and-forth" = 4+ consecutive short sentences with short gaps;
    # a terse monologue also matches. Use speaker turns if diarization is ever added.
    run = best = 0
    for k, s in enumerate(sents):
        quick = nwords[k] <= 6 and (k == 0 or s["start"] - sents[k - 1]["end"] <= 0.7)
        run = run + 1 if quick else 0
        best = max(best, run)
    return {"hits": hits, "laugh": any(LAUGH.search(s["text"]) for s in sents), "reversal": reversal,
            "banter": best >= 4}


def score_window(sents: list[dict], loud: list[float] | None, loud_ref: float,
                 impact: list[float] | None = None, impact_ref: float = 0.0) -> tuple[float, dict, str]:
    first, last = sents[0], sents[-1]
    words = [_norm(w["word"]) for s in sents for w in s["words"]]
    fw = [_norm(w["word"]) for w in first["words"]]
    dur = last["end"] - first["start"]
    text = " ".join(s["text"] for s in sents)

    hook = 0.3
    hook += 0.25 if first["text"].endswith("?") else 0
    hook += 0.15 if {"you", "your"} & set(fw) else 0
    hook += 0.2 if HOOK_WORDS & set(fw) else 0
    hook += 0.1 if any(ch.isdigit() for ch in first["text"]) else 0
    hook += 0.1 if len(fw) <= 14 else -0.1

    standalone = 1.0
    if fw and fw[0] in DANGLING:
        standalone -= 0.6
    if fw and fw[0] in BACKREF:
        standalone -= 0.35
    if OFFTOPIC.search(first["text"]):
        standalone -= 0.5
    # Housekeeping/transition lines anywhere inside mean the window spans two topics.
    standalone -= 0.45 * sum(bool(OFFTOPIC.search(s["text"])) for s in sents[1:])

    completeness = 1.0 if last["text"].endswith((".", "!")) else 0.35 if last["text"].endswith("?") else 0.6
    if OFFTOPIC.search(last["text"]):
        completeness -= 0.4

    value = _clamp(0.15 + 0.12 * len(VALUE_WORDS & set(words)) + (0.15 if "?" in text[:-1] else 0))
    value -= 0.5 * len(OFFTOPIC.findall(text)) / max(1, len(sents))

    wps = len(words) / max(dur, 0.1)
    pacing = _clamp(1 - abs(wps - 2.8) / 2.5)
    pacing -= sum(w in FILLERS for w in words) / max(1, len(words)) * 3

    energy = 0.5
    if loud and loud_ref > 0:
        win = loud[int(first["start"]):max(int(first["start"]) + 1, int(last["end"]))]
        if win:
            energy = _clamp(0.5 + (statistics.fmean(win) / loud_ref - 1) * 1.5)
    if impact and impact_ref > 0:  # bonus for above-average impact density (gunshots, explosions, hype)
        win = impact[int(first["start"]):max(int(first["start"]) + 1, int(last["end"]))]
        if win:
            energy += 0.3 * _clamp((statistics.fmean(win) / impact_ref - 1) / 2)

    r = reaction_signals(sents)
    reaction = 0.25 * min(r["hits"], 3) + 0.4 * r["laugh"] + 0.35 * r["reversal"] + 0.4 * r["banter"]

    b = {k: round(_clamp(v), 3) for k, v in
         dict(hook=hook, standalone=standalone, completeness=completeness, value=value, pacing=pacing, energy=energy,
              reaction=reaction).items()}
    total = round(100 * sum(WEIGHTS[k] * b[k] for k in WEIGHTS), 1)

    good = [n for n, k in (("strong opening", "hook"), ("makes sense on its own", "standalone"),
                           ("ends on a complete thought", "completeness"), ("useful or story-driven content", "value"),
                           ("tight pacing", "pacing"), ("high vocal energy", "energy")) if b[k] >= 0.7]
    good += [n for n, on in (("big reaction", r["hits"] > 0), ("laughter", r["laugh"]),
                             ("setup and punchline", r["reversal"]), ("quick back-and-forth", r["banter"])) if on]
    weak = [n for n, k in (("weak opening", "hook"), ("may need earlier context", "standalone"),
                           ("ends abruptly", "completeness")) if b[k] < 0.45]
    reason = ("Strengths: " + ", ".join(good) + "." if good else "No standout strengths.") + \
             (" Watch out: " + ", ".join(weak) + "." if weak else "")
    return total, b, reason


def _overlap(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def pick(candidates: list[dict], count: int) -> list[dict]:
    """Best-first, never overlapping (overlapping windows are near-duplicates of each other)."""
    chosen = []
    for c in sorted(candidates, key=lambda c: -c["score"]):
        if not any(_overlap(c, x) for x in chosen):
            chosen.append(c)
            if len(chosen) == count:
                break
    return sorted(chosen, key=lambda c: c["start"])


def speech_candidates(segments, min_len, max_len, loud=None, impact=None) -> list[dict]:
    sents = sentences(segments)
    ref = statistics.median(loud) if loud else 0
    iref = statistics.fmean(impact) if impact else 0
    out = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_len:
                break
            if dur >= min_len:
                window = sents[i:j + 1]
                score, breakdown, reason = score_window(window, loud, ref, impact, iref)
                out.append({"start": round(max(0, window[0]["start"] - 0.15), 2), "end": round(window[-1]["end"] + 0.25, 2),
                            "text": " ".join(s["text"] for s in window), "score": score,
                            "breakdown": breakdown, "reason": reason, "method": "speech"})
    return out


def impact_per_second(audio: np.ndarray, sr: int = 16000, n_fft: int = 256, hop_s: float = 0.01,
                      band: tuple[int, int] = (1000, 6000), chunk_frames: int = 6000) -> list[float]:
    """Per-second density of sharp transients (hits, gunshots, explosions) via spectral flux on the 1-6 kHz band.
    Each peak counts 1-4 by its strength over the threshold. Steady tones and stationary noise give ~nothing.
    Processed in 60 s chunks so a 2 h file never materializes a full spectrogram."""
    f = ImpactStream(sr, n_fft, hop_s, band, chunk_frames)
    f.add(audio)
    return f.result()


class ImpactStream:
    """impact_per_second fed piece by piece (any sizes; same result as all at once), for streamed audio."""

    def __init__(self, sr: int = 16000, n_fft: int = 256, hop_s: float = 0.01, band: tuple[int, int] = (1000, 6000),
                 chunk_frames: int = 6000):
        self.sr, self.n_fft, self.hop, self.chunk_frames = sr, n_fft, int(sr * hop_s), chunk_frames
        self.win = np.hanning(n_fft).astype(np.float32)
        self.lo, self.hi = max(1, band[0] * n_fft // sr), min(n_fft // 2 + 1, band[1] * n_fft // sr)
        self.tail, self.prev, self.flux, self.samples = np.zeros(0, np.float32), None, [], 0

    def add(self, audio: np.ndarray):
        audio = np.asarray(audio, np.float32)
        self.samples += len(audio)
        buf = np.concatenate([self.tail, audio]) if len(self.tail) else audio
        n = (len(buf) - self.n_fft) // self.hop + 1 if len(buf) >= self.n_fft else 0
        frames = np.lib.stride_tricks.sliding_window_view(buf, self.n_fft)[::self.hop] if n else None  # a view
        for f0 in range(0, n, self.chunk_frames):
            mag = np.abs(np.fft.rfft(frames[f0:f0 + self.chunk_frames] * self.win, axis=1)[:, self.lo:self.hi])
            d = np.diff(mag if self.prev is None else np.vstack([self.prev, mag]), axis=0)
            self.flux.append(np.maximum(d, 0).sum(axis=1))
            self.prev = mag[-1:]
        self.tail = buf[n * self.hop:].copy()  # samples the next frames still need

    def result(self) -> list[float]:
        sr, hop = self.sr, self.hop
        n_sec = self.samples // sr
        if n_sec == 0 or self.samples < self.n_fft + 4 * hop:
            return [0.0] * n_sec
        flux = np.concatenate(self.flux)  # flux[i]: frame i+1 vs frame i
        # ponytail: one global threshold (4x median flux); use a rolling median if loudness varies a lot within a video.
        thr = max(4 * float(np.median(flux)), 0.5)
        local_max = np.lib.stride_tricks.sliding_window_view(np.pad(flux, 3), 7).max(axis=1)  # +-30 ms min gap
        peaks = np.flatnonzero((flux >= thr) & (flux == local_max))
        sec = (peaks + 1) * hop // sr
        keep = sec < n_sec
        strength = np.minimum(flux[peaks[keep]] / thr, 4.0)
        return np.bincount(sec[keep], weights=strength, minlength=n_sec)[:n_sec].round(3).tolist()


def loudness_candidates(loud: list[float], min_len: float, max_len: float, impact: list[float] | None = None) -> list[dict]:
    """For footage with little or no speech (e.g. gameplay): windows around the loudest / most impact-dense moments.
    ponytail: audio-only; add scene-cut density / a vision model if gameplay picks are weak."""
    if not loud:
        return []
    length = (min_len + max_len) / 2
    ref = statistics.median(loud) or 1e-6
    peak = max(loud) or 1e-6
    imp = list(impact or [])[:len(loud)]
    imp += [0.0] * (len(loud) - len(imp))
    imp_trigger = max(2.0, 2 * statistics.median(imp))
    windows = []
    for t in range(len(loud)):
        # only build windows around moments clearly louder than usual, or with a burst of sharp impacts
        if loud[t] < ref * 1.5 and imp[t] < imp_trigger:
            continue
        start = max(0.0, t - length * 0.6)
        end = min(len(loud), start + length)
        if end - start >= min_len:
            windows.append((t, start, end, sum(imp[int(start):int(end)])))
    best_imp = max((w[3] for w in windows), default=0) or 1e-6
    out = []
    for t, start, end, imp_sum in windows:
        win = loud[int(start):int(end)]
        e = _clamp((statistics.fmean(win) - ref) / (peak - ref + 1e-6) * 2)
        p = _clamp((loud[t] - ref) / (peak - ref + 1e-6))
        i = _clamp(imp_sum / best_imp) if impact else 0.0
        score = round(100 * ((0.3 * p + 0.2 * e + 0.5 * i) if impact else (0.6 * p + 0.4 * e)), 1)
        reason = ("Picked from a burst of sharp impacts in the audio (hits, gunshots, hype moments; little or no "
                  "speech detected)." if i >= 0.5 else
                  "Picked from a loud moment in the audio (little or no speech detected).")
        out.append({"start": round(start, 2), "end": round(end, 2), "text": "", "score": score,
                    "breakdown": {"peak_loudness": round(p, 3), "sustained_energy": round(e, 3),
                                  **({"impact_density": round(i, 3)} if impact else {})},
                    "reason": reason, "method": "loudness"})
    return out


def chat_boost(cands: list[dict], spikes: list[dict], bonus: float, pad: float = 10) -> list[dict]:
    """+bonus (clamped to 100) and a reason for every window overlapping a 30 s chat spike (+-pad s).
    spikes: chat.spikes() output, [{offset_s, count}]. No spikes = candidates unchanged."""
    for c in cands:
        hit = [s["count"] for s in spikes if s["offset_s"] - pad < c["end"] and c["start"] < s["offset_s"] + 30 + pad]
        if hit:
            c["score"] = round(min(100.0, c["score"] + bonus), 1)
            c["breakdown"] = {**c["breakdown"], "chat_spike": max(hit)}
            c["reason"] = f"{c['reason']} Twitch chat spiked here ({max(hit)} messages in 30 s).".strip()
    return cands


def select_clips(segments, duration, min_len, max_len, count, loud=None, impact=None, chat_spikes=None,
                 chat_bonus: float = 20) -> list[dict]:
    speech = sum(s["end"] - s["start"] for s in segments)
    cands = speech_candidates(segments, min_len, max_len, loud, impact)
    if speech < 0.2 * duration or not cands:
        cands += loudness_candidates(loud or [], min_len, max_len, impact)
    return pick(chat_boost(cands, chat_spikes or [], chat_bonus), count)
