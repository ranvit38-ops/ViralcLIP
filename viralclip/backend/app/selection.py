"""Pick standalone, engaging clip windows from a word-timestamped transcript.

Transparent heuristic: every candidate gets a 0-100 score with a per-feature breakdown and a
plain-English reason. It is a ranking aid, not a view-count prediction. If an LLM is configured,
pipeline.py blends its judgement in on top of this.
"""
import re
import statistics

DANGLING = {"and", "but", "so", "because", "which", "or", "also", "then", "anyway", "plus", "though"}
BACKREF = {"he", "she", "it", "they", "this", "that", "these", "those", "him", "her", "them"}
FILLERS = {"um", "uh", "erm", "hmm", "like", "basically", "literally"}
HOOK_WORDS = {"secret", "mistake", "never", "always", "biggest", "worst", "best", "why", "how", "truth",
              "nobody", "everyone", "stop", "wrong", "actually", "crazy", "insane", "shocking", "real"}
VALUE_WORDS = {"should", "need", "secret", "mistake", "tip", "lesson", "learned", "instead", "because",
               "habit", "rule", "trick", "step", "reason", "story", "remember", "taught", "realized", "never"}
OFFTOPIC = re.compile(r"\b(let me check|check the time|subscribe|like and|see you next|thanks for (watching|listening)|"
                      r"brings me to|my next point|moving on|before we start|sponsor|^anyway\b|^okay\b)\b", re.I)
WEIGHTS = {"hook": 0.25, "standalone": 0.2, "completeness": 0.15, "value": 0.2, "pacing": 0.08, "energy": 0.12}


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


def score_window(sents: list[dict], loud: list[float] | None, loud_ref: float) -> tuple[float, dict, str]:
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

    b = {k: round(_clamp(v), 3) for k, v in
         dict(hook=hook, standalone=standalone, completeness=completeness, value=value, pacing=pacing, energy=energy).items()}
    total = round(100 * sum(WEIGHTS[k] * b[k] for k in WEIGHTS), 1)

    good = [n for n, k in (("strong opening", "hook"), ("makes sense on its own", "standalone"),
                           ("ends on a complete thought", "completeness"), ("useful or story-driven content", "value"),
                           ("tight pacing", "pacing"), ("high vocal energy", "energy")) if b[k] >= 0.7]
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


def speech_candidates(segments, min_len, max_len, loud=None) -> list[dict]:
    sents = sentences(segments)
    ref = statistics.median(loud) if loud else 0
    out = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_len:
                break
            if dur >= min_len:
                window = sents[i:j + 1]
                score, breakdown, reason = score_window(window, loud, ref)
                out.append({"start": round(max(0, window[0]["start"] - 0.15), 2), "end": round(window[-1]["end"] + 0.25, 2),
                            "text": " ".join(s["text"] for s in window), "score": score,
                            "breakdown": breakdown, "reason": reason, "method": "speech"})
    return out


def loudness_candidates(loud: list[float], min_len: float, max_len: float) -> list[dict]:
    """For footage with little or no speech (e.g. gameplay): windows around the loudest moments.
    ponytail: loudness-only; add scene-cut density / a vision model if gameplay picks are weak."""
    if not loud:
        return []
    length = (min_len + max_len) / 2
    ref = statistics.median(loud) or 1e-6
    peak = max(loud) or 1e-6
    out = []
    for t in range(len(loud)):
        if loud[t] < ref * 1.5:  # only build windows around moments clearly louder than usual
            continue
        start = max(0.0, t - length * 0.6)
        end = min(len(loud), start + length)
        if end - start < min_len:
            continue
        win = loud[int(start):int(end)]
        e = _clamp((statistics.fmean(win) - ref) / (peak - ref + 1e-6) * 2)
        p = _clamp((loud[t] - ref) / (peak - ref + 1e-6))
        score = round(100 * (0.6 * p + 0.4 * e), 1)
        out.append({"start": round(start, 2), "end": round(end, 2), "text": "", "score": score,
                    "breakdown": {"peak_loudness": round(p, 3), "sustained_energy": round(e, 3)},
                    "reason": "Picked from a loud moment in the audio (little or no speech detected).",
                    "method": "loudness"})
    return out


def select_clips(segments, duration, min_len, max_len, count, loud=None) -> list[dict]:
    speech = sum(s["end"] - s["start"] for s in segments)
    cands = speech_candidates(segments, min_len, max_len, loud)
    if speech < 0.2 * duration or not cands:
        cands += loudness_candidates(loud or [], min_len, max_len)
    return pick(cands, count)
