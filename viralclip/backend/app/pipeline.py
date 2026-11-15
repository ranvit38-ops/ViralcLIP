"""Worker job handlers: analyze (transcribe + select clips + metadata), render, metadata."""
import logging
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select

from . import config, llm, longaudio, selection, trends
from .db import SessionLocal
from .models import Clip, Job, Project, Transcript, User
from .projects import project_dir, source_path
from .worker import Cancelled, Ctx, UserError

log = logging.getLogger("viralclip.pipeline")
SR = 16000

# ---------------------------------------------------------------- settings

DEFAULT_ANALYZE = {"min_len": 30, "max_len": 60, "count": 8, "language": None, "retranscribe": False}

CAPTION_PRESETS = {
    # Bold, all-caps, active word highlighted and popped: the short-form look.
    "bold": {"font": "Inter", "size": 82, "color": "#FFFFFF", "highlight": "#FFE600", "outline": "#000000",
             "outline_width": 6, "shadow": 2, "uppercase": True, "words": 3, "box": False, "pop": True, "bold": True},
    "karaoke": {"font": "Inter", "size": 76, "color": "#FFFFFF", "highlight": "#39FF6A", "outline": "#000000",
                "outline_width": 5, "shadow": 0, "uppercase": True, "words": 4, "box": False, "pop": False, "bold": True},
    "clean": {"font": "Inter", "size": 64, "color": "#FFFFFF", "highlight": "#FFFFFF", "outline": "#000000",
              "outline_width": 0, "shadow": 0, "uppercase": False, "words": 6, "box": True, "pop": False, "bold": True},
    "minimal": {"font": "Inter", "size": 54, "color": "#FFFFFF", "highlight": "#9AD7FF", "outline": "#000000",
                "outline_width": 3, "shadow": 1, "uppercase": False, "words": 5, "box": False, "pop": False, "bold": False},
}
DEFAULT_RENDER = {"aspect": "9:16", "layout": "auto", "crop_x": None, "captions": True, "preset": "bold",
                  "position": "lower", "style": {},
                  "remove_pauses": True,   # jump cuts: drop silences longer than PAUSE between words
                  "remove_retakes": True,  # ...and the earlier take of an immediately repeated phrase
                  "remove_fillers": True,  # ...and standalone um/uh/erm/hmm
                  "zoom": True,            # punch-in on alternating sentences (talking-head crops only)
                  "hook": True,            # title shown in a box for the first HOOK_SECONDS
                  "hook_text": None}       # None = use the clip title
PAUSE, HOOK_SECONDS = 0.6, 3.0
SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}


def render_settings(clip: Clip, user_settings: dict | None = None) -> dict:
    base = {**DEFAULT_RENDER, **((user_settings or {}).get("render") or {})}
    s = {**base, **(clip.render_settings or {})}
    s["style"] = {**CAPTION_PRESETS.get(s["preset"], CAPTION_PRESETS["bold"]), **(s.get("style") or {})}
    return s


# ---------------------------------------------------------------- audio / transcription

def decode_audio(src: Path, start: float, dur: float) -> np.ndarray:
    """A short stretch of audio (a clip) in memory. Whole sources go through scan_audio, which streams."""
    r = longaudio.run([config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn",
                       "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:"], 60 + dur, "Reading the clip's audio",
                      capture_output=True)
    if r.returncode != 0:
        raise UserError("Could not extract audio from this video.")
    return np.frombuffer(r.stdout, np.float32)


def loudness_per_second(audio: np.ndarray) -> list[float]:
    n = len(audio) // SR
    if n == 0:
        return []
    return np.sqrt(np.mean(audio[: n * SR].reshape(n, SR) ** 2, axis=1)).round(5).tolist()


# Transcription chunk length: memory no longer grows with source length (measured, tiny: 10.5 min -> +846 MB,
# 2 h -> +890 MB; was +1807 MB). Most of what remains is Whisper's batch working set, set by WHISPER_BATCH.
CHUNK_S = float(os.getenv("WHISPER_CHUNK_S", "600"))


def scan_audio(src: Path, duration: float, model: str | None, language: str | None, ctx: Ctx, lo: float, hi: float,
               chunk_s: float = CHUNK_S):
    """One streaming pass over the source's audio -> (loudness/s, impact/s, (segments, language) or None).
    Transcribes only when `model` is given (no cached transcript). Memory stays flat for any length."""
    loud, impact, duration = [], selection.ImpactStream(SR), duration or 0.0

    def blocks():
        for b in longaudio.stream(config.FFMPEG, src, SR):
            loud.extend(loudness_per_second(b))  # blocks are whole seconds, except the last
            impact.add(b)
            yield b

    tr = None
    if model:
        tr = transcribe(longaudio.chunks(blocks(), SR, chunk_s), model, language, ctx, lo, hi, duration)
    else:
        for _ in blocks():
            ctx.progress(lo + (hi - lo) * min(1.0, len(loud) / max(duration, 1)), "Analyzing audio")
    return loud, impact.result(), tr


_models: dict = {}


def whisper_model(name: str):
    if name not in _models:
        from faster_whisper import WhisperModel
        import ctranslate2
        dev = os.getenv("WHISPER_DEVICE", "auto")
        if dev == "auto":
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        try:
            _models[name] = WhisperModel(name, device=dev, compute_type="float16" if dev == "cuda" else "int8",
                                         download_root=os.getenv("WHISPER_CACHE_DIR"))
        except Exception as e:
            log.exception("Could not load whisper model %s", name)
            raise UserError(f"Could not load the '{name}' transcription model. If this is the first run it needs "
                            "to download from huggingface.co; check the network.") from e
    return _models[name]


WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]


def transcribe(chunks, model: str, language: str | None, ctx: Ctx, lo: float, hi: float, duration: float):
    """chunks: (t0, audio, keep_end) from longaudio.chunks. Each is transcribed on its own (the first one detects
    the language for the rest) and its words are stitched on by timestamp, so the overlap is neither lost nor doubled."""
    # Batched inference: same words, ~2.3x faster on CPU (measured: small, 10.5 min audio, 140 s -> 62 s).
    from faster_whisper import BatchedInferencePipeline
    pipe, words = BatchedInferencePipeline(whisper_model(model)), []
    for t0, audio, keep_end in chunks:
        segs, info = pipe.transcribe(audio, word_timestamps=True, language=language or None,
                                     batch_size=int(os.getenv("WHISPER_BATCH", "8")))
        language = language or info.language
        after = words[-1]["end"] if words else -1.0
        for s in segs:  # generator: transcription happens as we iterate, so progress is real
            words += longaudio.keep_words([{"start": round(t0 + w.start, 2), "end": round(t0 + w.end, 2), "word": w.word,
                                            "probability": round(w.probability, 3)} for w in (s.words or [])],
                                          keep_end, after)
            t = t0 + s.end
            ctx.progress(lo + (hi - lo) * min(1.0, t / max(duration, 1)),
                         f"Transcribing… {int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
                         if duration >= 3600 else f"Transcribing… {int(t // 60)}:{int(t % 60):02d}")
    # Regroup into sentences so the transcript viewer can seek to a sentence and speech coverage
    # (used for the gameplay fallback) excludes long silences.
    return [{**x, "start": round(x["start"], 2), "end": round(x["end"], 2)}
            for x in selection.sentences([{"words": words}])], language


# ---------------------------------------------------------------- analyze

def _user_prefs(db, project: Project) -> dict:
    u = db.get(User, project.user_id)
    return (u.settings or {}) if u else {}


def analyze(job_id: int, ctx: Ctx):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        p = db.get(Project, job.project_id)
        params = {**DEFAULT_ANALYZE, **(job.params or {})}
        prefs = _user_prefs(db, p)
        model = params.get("model") or prefs.get("whisper_model") or os.getenv("WHISPER_MODEL", "small")
        if model not in WHISPER_MODELS:
            raise UserError(f"Unknown transcription model '{model}'.")
        if not p.has_audio:
            raise UserError("This video has no audio track, so it can't be transcribed.")
        prev_status, p.status = p.status, "processing"
        db.commit()
        src, duration = source_path(p), p.duration
        tr = db.scalar(select(Transcript).where(Transcript.project_id == p.id))
        cached = None if (tr is None or params["retranscribe"]) else (tr.segments, tr.language)
    try:
        ctx.progress(0.02, "Extracting audio")
        loud, impact, fresh = scan_audio(src, duration, None if cached else model, params["language"], ctx, 0.05, 0.75)
        if cached:
            segments, language = cached
        else:
            segments, language = fresh
            with SessionLocal() as db:
                db.execute(delete(Transcript).where(Transcript.project_id == p.id))
                db.add(Transcript(project_id=p.id, language=language, model=model, segments=segments,
                                  text=" ".join(s["text"] for s in segments)))
                db.commit()

        ctx.progress(0.78, "Finding the best moments")
        min_len, max_len = float(params["min_len"]), float(params["max_len"])
        if not (5 <= min_len < max_len <= 600):
            raise UserError("Clip length must be between 5 seconds and 10 minutes, with min < max.")
        count = max(1, min(int(params["count"]), 50))

        with SessionLocal() as db:
            # Keep clips the user already touched; replace untouched suggestions.
            kept = db.scalars(select(Clip).where(Clip.project_id == p.id)).all()
            keep = [c for c in kept if c.review_status != "suggested" or c.render_status != "none" or c.title_edited]
            for c in kept:
                if c not in keep:
                    db.delete(c)
            db.commit()
            keep_windows = [{"start": c.start, "end": c.end} for c in keep]

        cands = selection.speech_candidates(segments, min_len, max_len, loud, impact)
        speech = sum(s["end"] - s["start"] for s in segments)
        if speech < 0.2 * duration or not cands:
            cands += selection.loudness_candidates(loud, min_len, max_len, impact)
        cands = [c for c in cands if not any(selection._overlap(c, k) for k in keep_windows)]
        until = llm.deadline()  # one time budget for the whole AI stage; past it, clips get basic metadata
        if llm.configured() and cands:
            # Only send the strongest few to the (slow) LLM.
            shortlist = selection.pick(cands, count * 3)
            ctx.progress(0.82, f"AI reviewing {len(shortlist)} candidates")
            # ponytail: fixed 40/60 split of the budget between rerank and metadata
            cands = llm.rerank(shortlist, until=llm.deadline(0.4 * llm.BUDGET))
        chosen = selection.pick(cands, count)
        if not chosen:
            raise UserError("No suitable clips found. Try a shorter clip length or a video with more speech.")

        meta_prefs = prefs.get("metadata") or {}
        ctx.progress(0.88, f"Writing titles and hashtags for {len(chosen)} clips")
        texts = [c["text"] for c in chosen]
        metas = llm.generate_metadata_batch(texts, meta_prefs, language,
                                            [similar(p.user_id, t, meta_prefs, language) for t in texts], until)
        with SessionLocal() as db:
            retry = []
            for i, (c, m) in enumerate(zip(chosen, metas)):
                if not c["text"]:
                    m = {"titles": [], "description": "", "hashtags": [], "method": "none"}
                row = Clip(project_id=p.id, start=c["start"], end=c["end"], transcript_excerpt=c["text"],
                           score=c["score"], score_breakdown={**c["breakdown"], "method": c["method"],
                                                              "metadata_method": m["method"],
                                                              **({"metadata_sources": m["sources"]} if m.get("sources") else {})},
                           reason=c["reason"], title=(m["titles"] or [f"Clip {i + 1}"])[0], title_options=m["titles"],
                           description=m["description"], hashtags=m["hashtags"])
                db.add(row)
                if m["method"].endswith("+timeout"):
                    retry.append(row)
            db.flush()
            if retry:  # the AI ran out of time: let a background metadata job retry these with the LLM
                db.add(Job(project_id=p.id, job_type="metadata", params={"clip_ids": [r.id for r in retry]},
                           message="Waiting for a worker"))
            db.commit()
        _set_project_status(p.id, "ready")
    except (Cancelled, Exception):
        _set_project_status(p.id, "ready" if prev_status == "ready" else "uploaded")
        raise


def _set_project_status(pid: int, status: str):
    with SessionLocal() as db:
        p = db.get(Project, pid)
        if p:
            p.status = status
            db.commit()


def metadata(job_id: int, ctx: Ctx):
    """Regenerate titles/descriptions/hashtags for every clip of a project (or job.params.clip_ids)."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        p = db.get(Project, job.project_id)
        prefs = (_user_prefs(db, p).get("metadata") or {})
        q = select(Clip).where(Clip.project_id == p.id)
        if job.params.get("clip_ids"):
            q = q.where(Clip.id.in_(job.params["clip_ids"]))
        clips = db.scalars(q).all()
        fields = set(job.params.get("fields") or ["titles", "description", "hashtags"])
        lang = (db.scalar(select(Transcript.language).where(Transcript.project_id == p.id)))
        texts = [c.transcript_excerpt or "" for c in clips]
        ctx.progress(0.05, f"Writing titles and hashtags for {len(clips)} clips")
        exs = [similar(p.user_id, t, prefs, lang) if fields & RESEARCHED else None for t in texts]
        # ponytail: budget scales with clip count (one analyze's worth per 8 clips)
        metas = llm.generate_metadata_batch(texts, prefs, lang, exs, llm.deadline(llm.BUDGET * max(1, len(clips) / 8)))
        for c, m in zip(clips, metas):
            apply_metadata(c, m, fields)
        db.commit()


RESEARCHED = {"titles", "hashtags"}  # fields similar-Shorts research can influence


def similar(user_id: int, text: str, prefs: dict, language: str | None) -> dict | None:
    """Style examples {"query", "items", "sources"} for one clip's metadata (own winners + similar Shorts), or None."""
    try:
        items = trends.examples_for(user_id, text, prefs, language) if text else []
        if not items:
            return None
        src = trends.sources(items, text, prefs)
        return {"query": src["query"], "items": items, "sources": src}
    except Exception as e:  # noqa: BLE001 - examples are optional
        log.warning("Metadata examples failed, generating without them: %s", e)
        return None


def apply_metadata(c: Clip, m: dict, fields: set):
    if "titles" in fields and m["titles"]:
        c.title_options = m["titles"]
        if not c.title_edited:  # never overwrite a title the user wrote
            c.title = m["titles"][0]
    if "description" in fields:
        c.description = m["description"]
    if "hashtags" in fields:
        c.hashtags = m["hashtags"]
    sb = {**(c.score_breakdown or {}), "metadata_method": m["method"]}
    if fields & RESEARCHED:  # provenance follows the fields it shaped
        sb.pop("metadata_sources", None)
        if m.get("sources"):
            sb["metadata_sources"] = m["sources"]
    c.score_breakdown = sb


# ---------------------------------------------------------------- reframing

MOTION_FPS, DETECT_EVERY, CELL = 10, 5, 8  # motion at 10 fps, faces on every 5th frame (2 fps), 8 px grid


def _faces(src: Path, start: float, dur: float, w: int, h: int):
    """One decode at MOTION_FPS (480 px wide, gray). Returns
    - samples: largest face per detection frame -> [(t, cx, face_h, cy) as fractions | (t, None, 0, None)]
    - dets: every face per detection frame -> [(t, [(x, y, w, h) as fractions], ...)]
    - motion: per motion frame, mean |frame - previous frame| on a CELL-px grid (float16, T x gh x gw), so
      mouth energy can be read for any box afterwards without keeping the frames (600 s clip ~ 50 MB)."""
    import cv2
    sw = 480
    sh = int(round(h * sw / w / 2) * 2)
    gh, gw = sh // CELL, sw // CELL
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                             "-vf", f"fps={MOTION_FPS},scale={sw}:{sh},format=gray", "-f", "rawvideo", "pipe:"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    samples, dets, motion, prev, i = [], [], [], None, 0
    try:
        with longaudio.Watchdog(proc, "Finding faces") as wd:
            wd.arm(60 + 3 * dur)  # measured 0.05x realtime (45 s of 1080p in 2.2 s); without a limit a hung decode never ends
            while len(buf := proc.stdout.read(sw * sh)) == sw * sh:
                f = np.frombuffer(buf, np.uint8).reshape(sh, sw)
                cur = f[:gh * CELL, :gw * CELL].astype(np.int16)
                motion.append(np.zeros((gh, gw), np.float16) if prev is None else
                              np.abs(cur - prev).reshape(gh, CELL, gw, CELL).mean(axis=(1, 3)).astype(np.float16))
                prev = cur
                if i % DETECT_EVERY == 0:
                    t = i / MOTION_FPS
                    faces = det.detectMultiScale(f, scaleFactor=1.1, minNeighbors=6, minSize=(sh // 12, sh // 12))
                    dets.append((t, [(x / sw, y / sh, fw / sw, fh / sh) for x, y, fw, fh in faces]))
                    if len(faces):
                        x, y, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                        samples.append((t, (x + fw / 2) / sw, fh / sh, (y + fh / 2) / sh))
                    else:
                        samples.append((t, None, 0.0, None))
                i += 1
    finally:
        proc.kill()
        proc.wait()
    if len(motion) > 1:
        motion[0] = motion[1]  # the first frame has nothing to diff against
    return samples, dets, (np.stack(motion) if motion else np.zeros((0, gh, gw), np.float16))


def people(dets, min_share: float = 0.3, gap: float = 0.06) -> list[tuple[float, float, float, float]]:
    """Cluster face boxes into stable people by horizontal position: sort centers, split where two
    neighbours are > `gap` of the frame width apart, keep clusters seen in >= `min_share` of frames.
    Returns each person's median box (x, y, w, h as fractions), left to right.
    ponytail: one static box per person; track the box over time if people walk around a lot."""
    boxes = sorted((b for _, bs in dets for b in bs), key=lambda b: b[0] + b[2] / 2)
    groups, cur = [], []
    for b in boxes:
        if cur and b[0] + b[2] / 2 - (cur[-1][0] + cur[-1][2] / 2) > gap:
            groups.append(cur)
            cur = []
        cur.append(b)
    if cur:
        groups.append(cur)
    need = min_share * max(1, len(dets))
    return [tuple(float(np.median([b[k] for b in g])) for k in range(4)) for g in groups if len(g) >= need]


def mouth_energy(motion: np.ndarray, box) -> np.ndarray:
    """Frame-difference energy over the lower 40% of a face box (mouth + chin), per motion frame."""
    x, y, w, h = box
    gh, gw = motion.shape[1:]
    r0, r1 = int((y + 0.6 * h) * gh), int(np.ceil((y + h) * gh))
    c0, c1 = int((x + 0.15 * w) * gw), int(np.ceil((x + 0.85 * w) * gw))
    r0, c0 = min(r0, gh - 1), min(c0, gw - 1)
    return motion[:, r0:max(r1, r0 + 1), c0:max(c1, c0 + 1)].astype(np.float32).mean(axis=(1, 2))


# Speaker switching ported from clipify scripts/analyze.py (MIT, (c) 2026 Louise de Sadeleer):
# normalize each person's motion, moving-average smooth, switch only when another person beats the
# current one by MARGIN, then fold segments shorter than MIN_DWELL into the previous speaker.
# Generalized from 2 hand-drawn ROIs to N detected people; see THIRD_PARTY_NOTICES.md.
MARGIN, MIN_DWELL, SMOOTH_S = 1.15, 1.0, 0.5


def speaker_segments(energy: np.ndarray, fps: float) -> list[tuple[float, float, int]]:
    """energy: people x frames -> [(start, end, person)] with hysteresis and minimum dwell."""
    e = np.asarray(energy, np.float64)
    means = e.mean(axis=1)
    # Per-person normalization (clipify divides by the mean). Floor the divisor at half the average
    # so someone who never talks doesn't get their noise blown up to the speaker's level.
    e = e / np.maximum(means, 0.5 * means.mean() + 1e-9)[:, None]
    win = max(1, int(round(SMOOTH_S * fps)) | 1)
    k = np.ones(win)
    s = np.array([np.convolve(r, k, "same") for r in e]) / np.convolve(np.ones(e.shape[1]), k, "same")
    cur, labels = int(np.argmax(s[:, 0])), []
    for i in range(s.shape[1]):
        best = int(np.argmax(s[:, i]))
        if best != cur and s[best, i] > s[cur, i] * MARGIN:
            cur = best
        labels.append(cur)
    segs = []
    for i, p in enumerate(labels):
        if segs and segs[-1][2] == p:
            segs[-1][1] = (i + 1) / fps
        else:
            segs.append([i / fps, (i + 1) / fps, p])
    merged = []
    for seg in segs:  # short interjections become part of whoever spoke before
        if merged and seg[1] - seg[0] < MIN_DWELL:
            merged[-1][1] = seg[1]
        elif merged and merged[-1][2] == seg[2]:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    return [(round(a, 2), round(b, 2), p) for a, b, p in merged]


def speaker_track(dets, motion) -> list[tuple[float, float]] | None:
    """Crop track that follows whoever is talking, or None when fewer than two stable people are on screen."""
    ppl = people(dets)
    if len(ppl) < 2 or len(motion) < 2:
        return None
    segs = speaker_segments(np.stack([mouth_energy(motion, b) for b in ppl]), MOTION_FPS)
    return [(a, ppl[p][0] + ppl[p][2] / 2) for a, _, p in segs]


def crop_track(samples, min_shift: float = 0.12, hold: int = 2) -> list[tuple[float, float]]:
    """Turn noisy face centers into a few stable camera positions: only move when the subject
    has clearly moved (> min_shift of frame width) for `hold` consecutive samples.
    ponytail: hard cuts between positions; add eased pans if hard cuts look jarring."""
    seen = [(t, x) for t, x, *_ in samples if x is not None]
    if not seen:
        return [(0.0, 0.5)]
    track = [(0.0, float(np.median([x for _, x in seen[:4]])))]
    pending = []
    for t, x in seen:
        if abs(x - track[-1][1]) > min_shift:
            pending.append((t, x))
            if len(pending) >= hold:
                track.append((pending[0][0], float(np.median([p[1] for p in pending]))))
                pending = []
        else:
            pending = []
    return track


def _crop_x_expr(track, src_w: int, crop_w: int) -> str:
    def px(frac):
        return int(max(0, min(src_w - crop_w, frac * src_w - crop_w / 2)) // 2 * 2)
    # nested if: if(lt(t,t1),x0,if(lt(t,t2),x1,x2))
    expr = str(px(track[-1][1]))
    for i in range(len(track) - 1, 0, -1):
        expr = f"if(lt(t,{track[i][0]:.2f}),{px(track[i - 1][1])},{expr})"
    return expr


# ---------------------------------------------------------------- captions (ASS)

def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip_words(segments: list[dict], start: float, end: float) -> list[dict]:
    """Words overlapping [start, end), timed relative to the clip and clamped inside it
    (a trim can land mid-word; negative times would break caption editing)."""
    return [{"start": round(max(0.0, w["start"] - start), 2), "end": round(min(end - start, w["end"] - start), 2),
             "word": w["word"].strip()}
            for s in segments for w in s.get("words") or [] if w["end"] > start and w["start"] < end and w["word"].strip()]


def caption_lines(words: list[dict], per_line: int) -> list[dict]:
    """Group words into on-screen lines, breaking at sentence ends and pauses."""
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if len(cur) >= per_line or w["word"].endswith((".", "?", "!", ",")) or nxt is None or nxt["start"] - w["end"] > 0.6:
            lines.append({"start": round(cur[0]["start"], 2), "end": round(cur[-1]["end"], 2),
                          "text": " ".join(x["word"] for x in cur), "words": cur})
            cur = []
    return lines


def lines_from_overrides(overrides: list[dict]) -> list[dict]:
    """User-edited lines carry no word timings: spread the words evenly across the line."""
    out = []
    for ln in overrides:
        ws = ln["text"].split()
        if not ws:
            continue
        step = (ln["end"] - ln["start"]) / len(ws)
        out.append({"start": ln["start"], "end": ln["end"], "text": ln["text"],
                    "words": [{"start": ln["start"] + i * step, "end": ln["start"] + (i + 1) * step, "word": w}
                              for i, w in enumerate(ws)]})
    return out


def build_ass(lines: list[dict], W: int, H: int, st: dict, position, hook: str = "") -> str:
    scale = W / 1080
    size = int(st["size"] * scale)
    if isinstance(position, tuple):  # ("seam", y): centered on the split-screen seam
        align, margin_v = 8, max(0, position[1] - int(size * 0.7))
    else:
        align, margin_v = {"top": (8, int(H * 0.12)), "middle": (5, 0), "lower": (2, int(H * 0.24)),
                           "bottom": (2, int(H * 0.08))}.get(position, (2, int(H * 0.24)))
    if st["box"]:
        border, outline, back = 3, max(6, int(14 * scale)), _ass_color("#000000", 0x60)
    else:
        border, outline, back = 1, int(st["outline_width"] * scale), _ass_color(st["outline"], 0x80)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{size},{_ass_color(st['color'])},{_ass_color(st['highlight'])},{_ass_color('#000000' if st['box'] else st['outline'], 0x60 if st['box'] else 0)},{back},{-1 if st['bold'] else 0},0,0,0,100,100,0,0,{border},{outline},{int(st['shadow'] * scale)},{align},{int(60 * scale)},{int(60 * scale)},{margin_v},1
Style: Hook,{st['font']},{int(58 * scale)},&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,{int(18 * scale)},0,8,{int(110 * scale)},{int(110 * scale)},{int(H * 0.09)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hl = _ass_color(st["highlight"])
    events = []
    if hook:
        # Hook: the clip's title in a white box for the first seconds, like Shorts editors do.
        events.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(HOOK_SECONDS)},Hook,,0,0,0,,{{\\fad(120,250)}}{_ass_escape(hook[:100])}")
    for ln in lines:
        ws = ln["words"]
        for i, w in enumerate(ws):
            # One event per active word: whole line visible, current word highlighted (and popped).
            t0 = w["start"] if i else ln["start"]
            t1 = ws[i + 1]["start"] if i + 1 < len(ws) else max(ln["end"], w["end"])
            if t1 <= t0:
                continue
            parts = []
            for k, x in enumerate(ws):
                txt = _ass_escape(x["word"].upper() if st["uppercase"] else x["word"])
                if k == i and st["highlight"].lower() != st["color"].lower():
                    pop = r"\fscx112\fscy112" if st["pop"] else ""
                    parts.append(f"{{\\c{hl}{pop}}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            events.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Cap,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(events) + "\n"


# ---------------------------------------------------------------- render

def _run_ffmpeg(args: list[str], dur: float, ctx: Ctx, cwd: Path, lo: float, hi: float):
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-nostats", "-progress", "pipe:1", *args], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with longaudio.Watchdog(proc, "Rendering") as wd:
            wd.arm(120 + 10 * dur)  # measured ~0.36x realtime (45 s 9:16 clip in 16 s on 4 cores)
            for line in proc.stdout:
                if line.startswith("out_time_us=") and line.strip() != "out_time_us=N/A":
                    done = int(line.split("=")[1]) / 1e6
                    ctx.progress(lo + (hi - lo) * min(1.0, done / dur), "Rendering")
    except BaseException:  # cancelled or timed out
        proc.kill()
        proc.wait()
        raise
    err = proc.stderr.read()
    if proc.wait() != 0:
        log.error("ffmpeg failed: %s", err[-2000:])
        raise UserError("Rendering failed in ffmpeg. See worker logs for details.")


def render(job_id: int, ctx: Ctx):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        clip = db.get(Clip, job.clip_id)
        p = db.get(Project, clip.project_id)
        st = render_settings(clip, _user_prefs(db, p))
        tr = db.scalar(select(Transcript).where(Transcript.project_id == p.id))
        segments = tr.segments if tr else []
        clip.render_status = "rendering"
        db.commit()
        src, start, end, sw, sh = source_path(p), clip.start, clip.end, p.width, p.height
        overrides, fps, cid, title, has_audio = clip.caption_overrides, p.fps, clip.id, clip.title, bool(p.has_audio)
    try:
        _render(ctx, src, p.id, cid, start, end, sw, sh, fps, st, segments, overrides, title, has_audio)
    except BaseException:
        with SessionLocal() as db:
            c = db.get(Clip, cid)
            if c:
                c.render_status = "failed"
                db.commit()
        raise


# ---------------------------------------------------------------- jump cuts / time remapping

def _norm_word(w: str) -> str:
    return re.sub(r"[\W_]+", "", w.lower())


# Standalone hesitations as Whisper spells them ("Um,", "uhh", "Hmm...", "erm"). Not "like"/"so": those carry meaning.
FILLER = re.compile(r"u+m+|u+h+|e+r+m*|h+m+|m{2,}")


def find_fillers(words: list[dict]) -> set[int]:
    return {i for i, w in enumerate(words) if FILLER.fullmatch(_norm_word(w["word"]))}


MAX_RETAKE_WORDS = 12


def find_retakes(words: list[dict], skip: set[int] = frozenset()) -> list[tuple[int, int]]:
    """Ported from MakeMyClip editor/src/timeline/transcript.ts findRetakes (MIT, (c) 2026 MakeMyClip):
    a phrase of 2..12 words said again immediately ("I want to show, I want to show you") -> the EARLIER
    take as an inclusive word-index range, longest match first. A run of one word ("no no no") is
    emphasis and kept. Words in `skip` (fillers being cut) are ignored, so "I think, um, I think" matches."""
    idx = [i for i in range(len(words)) if i not in skip]
    norm = [_norm_word(words[i]["word"]) for i in idx]
    out, i = [], 0
    while i < len(idx):
        matched = 0
        for n in range(min(MAX_RETAKE_WORDS, (len(idx) - i) // 2), 1, -1):
            a, b = norm[i:i + n], norm[i + n:i + 2 * n]
            if a == b and "" not in a and len(set(a)) > 1:
                matched = n
                break
        if matched:
            out.append((idx[i], idx[i + matched - 1]))
            i += matched
        else:
            i += 1
    return out


def keep_segments(words: list[dict], dur: float, pause: float = PAUSE,
                  drop: set[int] = frozenset()) -> list[tuple[float, float]]:
    """Intervals (clip-relative) to keep so that silences longer than `pause` and the words in `drop`
    (retakes, fillers) are cut out. Padding around kept words never reaches into a dropped word."""
    kept = [(i, w) for i, w in enumerate(words) if i not in drop]
    if not kept:
        return [(0.0, dur)]
    pad_after, pad_before = 0.15, 0.08
    i0, w0 = kept[0]
    lead = 0.0 if w0["start"] <= pause and i0 == 0 else w0["start"] - pad_before
    if i0 > 0:
        lead = max(lead, words[i0 - 1]["end"])
    segs = [[max(0.0, lead), None]]
    for (pi, prev), (ni, nxt) in zip(kept, kept[1:]):
        if ni > pi + 1:  # dropped words in between: cut from the end of prev to the start of nxt
            segs[-1][1] = min(prev["end"] + pad_after, words[pi + 1]["start"])
            segs.append([max(nxt["start"] - pad_before, words[ni - 1]["end"]), None])
        elif nxt["start"] - prev["end"] > pause:
            segs[-1][1] = prev["end"] + pad_after
            segs.append([nxt["start"] - pad_before, None])
    il, wl = kept[-1]
    segs[-1][1] = min(dur, wl["end"] + 0.35, words[il + 1]["start"] if il + 1 < len(words) else dur)
    return [(round(a, 3), round(b, 3)) for a, b in segs if b - a > 0.05]


SNAP_RANGE, SNAP_WIN = 0.12, 0.02


def snap_cuts(keep: list[tuple[float, float]], words: list[dict], audio: np.ndarray, sr: int = SR):
    """Cut check, after MakeMyClip editor/src/timeline/review.ts (MIT, (c) 2026 MakeMyClip): that review
    flags joins whose edge audio peaks above -30 dBFS; here we fix them instead. Each interior join edge
    moves to the quietest 20 ms point within +-120 ms, never into a word's body (Whisper timings)."""
    if len(keep) < 2 or not len(audio):
        return keep
    sq = np.concatenate([[0.0], np.cumsum(audio.astype(np.float64) ** 2)])
    half, step = int(SNAP_WIN * sr / 2), int(0.005 * sr)
    bodies = sorted((w["start"], w["end"]) for w in words)

    def snap(t: float) -> float:
        lo, hi = t - SNAP_RANGE, t + SNAP_RANGE
        for a, b in bodies:
            if a < t < b:
                return t  # already inside a word (overlapping timings): leave it alone
            if b <= t:
                lo = max(lo, b)
            elif a >= t:
                hi = min(hi, a)
                break
        c = np.arange(int(max(lo * sr, half)), int(min(hi * sr, len(audio) - half)) + 1, step)
        if not len(c):
            return t
        energy = (sq[c + half] - sq[c - half]) / (2 * half)
        # quietest window; near-ties (within 10%, or digital silence) go to the one nearest the original cut
        near = c[energy <= energy.min() * 1.1 + 1e-8]
        return round(float(near[np.argmin(np.abs(near - t * sr))]) / sr, 3)

    out = [list(k) for k in keep]
    for i in range(len(out) - 1):
        b, a = snap(out[i][1]), snap(out[i + 1][0])
        if out[i][0] < b < a < out[i + 1][1]:
            out[i][1], out[i + 1][0] = b, a
    return [tuple(k) for k in out]


def remap(t: float, keep: list[tuple[float, float]]) -> float:
    """Clip-relative time -> time on the jump-cut timeline (times inside a cut snap to the next kept frame)."""
    acc = 0.0
    for a, b in keep:
        if t < a:
            return acc
        if t <= b:
            return acc + t - a
        acc += b - a
    return acc


def is_kept(w: dict, keep) -> bool:
    mid = (w["start"] + w["end"]) / 2
    return any(a <= mid <= b for a, b in keep)


def remap_lines(lines: list[dict], keep) -> list[dict]:
    """Move lines onto the jump-cut timeline; words that were cut out (retakes, fillers) disappear."""
    out = []
    for ln in lines:
        ws = [{**w, "start": remap(w["start"], keep), "end": remap(w["end"], keep)}
              for w in ln["words"] if is_kept(w, keep)]
        if ws:
            out.append({**ln, "start": ws[0]["start"], "end": ws[-1]["end"],
                        "text": " ".join(w["word"] for w in ws), "words": ws})
    return out


# ---------------------------------------------------------------- layouts

def facecam_box(samples, sw: int, sh: int, panel_ar: float):
    """A streamer facecam: small face that stays put. Returns (x, y, w, h) in source px, or None."""
    hits = [s for s in samples if s[1] is not None]
    if len(hits) < 0.3 * max(1, len(samples)):
        return None
    cx, fh, cy = (float(np.median([h[i] for h in hits])) for i in (1, 2, 3))
    if fh >= 0.12 or np.std([h[1] for h in hits]) > 0.05 or np.std([h[3] for h in hits]) > 0.05:
        return None  # big or moving face: a talking head, not a facecam
    bh = min(sh, fh * sh * 3.2)
    bw = min(sw, bh * panel_ar)
    bh = bw / panel_ar
    x = min(max(0, cx * sw - bw / 2), sw - bw)
    y = min(max(0, cy * sh - bh * 0.45), sh - bh)
    return int(x) // 2 * 2, int(y) // 2 * 2, int(bw) // 2 * 2, int(bh) // 2 * 2


def sentence_spans(lines: list[dict]) -> list[tuple[float, float]]:
    spans, cur = [], None
    for ln in lines:
        cur = cur or ln["start"]
        if ln["text"].rstrip().endswith((".", "?", "!")):
            spans.append((cur, ln["end"]))
            cur = None
    if cur is not None and lines:
        spans.append((cur, lines[-1]["end"]))
    return spans


# ---------------------------------------------------------------- render

def _render(ctx, src, pid, cid, start, end, sw, sh, fps, st, segments, overrides, title="", has_audio=True):
    dur = end - start
    W, H = SIZES.get(st["aspect"], SIZES["9:16"])
    out_dir = project_dir(pid) / "clips"
    out_dir.mkdir(exist_ok=True)
    tmp, final = out_dir / f"{cid}.tmp.mp4", out_dir / f"{cid}.mp4"
    out_fps = min(fps or 30, 60)

    # --- timeline: jump cuts from word timings (pauses, then retakes and fillers)
    words = clip_words(segments, start, end)
    keep, retakes, fillers = [(0.0, dur)], [], set()
    if st.get("remove_pauses"):
        fillers = find_fillers(words) if st.get("remove_fillers") else set()
        retakes = find_retakes(words, fillers) if st.get("remove_retakes") else []
        keep = keep_segments(words, dur, drop=fillers | {i for a, b in retakes for i in range(a, b + 1)})
        if has_audio and len(keep) > 1:
            keep = snap_cuts(keep, words, decode_audio(src, start, dur))
    new_dur = sum(b - a for a, b in keep)

    # --- framing
    layout = st["layout"]
    src_ar, dst_ar = sw / sh, W / H
    crop_w = int(sh * dst_ar) // 2 * 2
    can_crop = src_ar > dst_ar + 0.01
    track, cam = [(0.0, 0.5)], None
    top_h = int(H * 0.4) // 2 * 2
    if can_crop and layout in ("auto", "crop", "stream"):
        if st.get("crop_x") is not None and layout != "stream":
            track, layout = [(0.0, float(st["crop_x"]))], "crop"
        else:
            ctx.progress(0.03, "Finding faces")
            samples, dets, motion = _faces(src, start, dur, sw, sh)
            hits = [s for s in samples if s[1] is not None]
            big = bool(hits) and float(np.median([s[2] for s in hits])) >= 0.12
            if dst_ar < 1:
                cam = facecam_box(samples, sw, sh, W / top_h)
            if layout == "auto":
                # Talking head -> follow the face. Streamer facecam -> split screen. Otherwise -> whole frame.
                layout = ("crop" if len(hits) >= 0.3 * max(1, len(samples)) and big
                          else "stream" if cam else "fit")
            if layout == "stream" and not cam:
                layout = "fit"  # asked for split screen but no facecam found
            if layout == "crop":
                # Re-frame once the face drifts ~15% of the *output* width (a 9:16 crop is ~1/3 of the source).
                # Two or more people on screen -> follow whoever is talking; one person -> follow the face.
                speakers = speaker_track(dets, motion)
                track = [(remap(t, keep), x) for t, x in
                         speakers or crop_track(samples, min_shift=max(0.04, 0.15 * crop_w / sw))]
    elif layout in ("auto", "stream"):
        layout = "fit" if abs(src_ar - dst_ar) > 0.01 else "crop"

    # --- captions / hook (times on the new timeline)
    lines = []
    if st["captions"]:
        lines = lines_from_overrides(overrides) if overrides else \
            caption_lines([w for w in words if is_kept(w, keep)], int(st["style"]["words"]))
        lines = remap_lines(lines, keep)
    hook = (st.get("hook_text") or title or "").strip() if st.get("hook") else ""
    position = st["position"]
    if layout == "stream" and position == "lower":
        position = ("seam", top_h)  # captions sit on the split, like streamer shorts
    ass = build_ass(lines, W, H, st["style"], position, hook=hook) if (lines or hook) else None

    # --- filter graph
    n = len(keep)
    parts = [f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)) if n > 1 else "[0:v]null[vs0]"]
    if has_audio:
        parts.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)) if n > 1 else "[0:a]anull[as0]")
    for i, (a, b) in enumerate(keep):
        parts.append(f"[vs{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            fade = min(0.02, (b - a) / 4)
            parts.append(f"[as{i}]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0, b - a - fade):.3f}:d={fade:.3f}[a{i}]")
    if n > 1:
        parts.append("".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(n))
                     + f"concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
    else:
        parts.append("[v0]null[vc]" + (";[a0]anull[ac]" if has_audio else ""))

    if layout == "crop" and can_crop:
        v = f"[vc]crop=w={crop_w}:h={sh // 2 * 2}:x='{_crop_x_expr(track, sw, crop_w)}':y=0,scale={W}:{H}:flags=lanczos"
        spans = sentence_spans(lines)[1::2] if st.get("zoom") else []
        if spans:
            cond = "+".join(f"between(it,{a:.2f},{b:.2f})" for a, b in spans)
            v += (f",zoompan=z='if({cond},1.1,1)':x='iw/2-(iw/zoom/2)':y='ih/2.4-(ih/zoom/2.4)'"
                  f":d=1:s={W}x{H}:fps={out_fps:.3f}")
    elif layout == "crop":  # source narrower than target: crop height (centered)
        ch = int(sw / dst_ar) // 2 * 2
        v = f"[vc]crop=w={sw // 2 * 2}:h={ch}:x=0:y={(sh - ch) // 2},scale={W}:{H}:flags=lanczos"
    elif layout == "stream":
        fx, fy, fw, fh = cam
        bot_h = H - top_h
        gw = min(sw, int(sh * W / bot_h)) // 2 * 2
        gx = int((sw - gw) * (st["crop_x"] if st.get("crop_x") is not None else 0.5)) // 2 * 2
        v = (f"[vc]split[cam][game];[cam]crop={fw}:{fh}:{fx}:{fy},scale={W}:{top_h}:flags=lanczos[top];"
             f"[game]crop={gw}:{sh // 2 * 2}:{gx}:0,scale={W}:{bot_h}:flags=lanczos[bot];[top][bot]vstack")
    else:
        # Background blurred at quarter resolution then upscaled: looks identical, ~45% faster render.
        v = (f"[vc]split[a][b];[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
             f"gblur=sigma=8,eq=brightness=-0.12,scale={W}:{H}[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    v += ",setsar=1"
    if ass:
        (out_dir / f"{cid}.ass").write_text(ass, encoding="utf-8")
        v += f",ass={cid}.ass"  # relative path via cwd: no filter-path escaping needed
    parts.append(v + "[v]")
    if has_audio:
        parts.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    graph = ";".join(parts)

    try:
        _run_ffmpeg(["-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", graph,
                     "-map", "[v]", *(["-map", "[a]"] if has_audio else []), "-r", f"{out_fps:.3f}",
                     "-c:v", "libx264", "-preset", os.getenv("RENDER_PRESET", "veryfast"), "-crf", os.getenv("RENDER_CRF", "18"),
                     "-profile:v", "high", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-movflags", "+faststart", tmp.name], new_dur, ctx, out_dir, 0.08, 0.97)
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)  # partial output from a failed/cancelled render
        (out_dir / f"{cid}.ass").unlink(missing_ok=True)
    longaudio.run([config.FFMPEG, "-v", "error", "-y", "-ss", f"{min(1.0, new_dur / 2):.2f}", "-i", str(final),
                   "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "3", str(out_dir / f"{cid}.jpg")], 120, "Making the thumbnail")
    with SessionLocal() as db:
        c = db.get(Clip, cid)
        if c:
            c.render_status, c.render_path = "done", f"clips/{cid}.mp4"
            c.thumbnail_path = f"clips/{cid}.jpg" if (out_dir / f"{cid}.jpg").exists() else None
            c.render_settings = {**(c.render_settings or {}), "_rendered_layout": layout,
                                 "_rendered_duration": round(new_dur, 2), "_cuts": len(keep) - 1,
                                 "_retakes_cut": len(retakes), "_fillers_cut": len(fillers)}
            db.commit()
