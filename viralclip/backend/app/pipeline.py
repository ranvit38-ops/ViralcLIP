"""Worker job handlers: analyze (transcribe + select clips + metadata), render, metadata."""
import logging
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select

from . import config, llm, selection
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
                  "position": "lower", "style": {}}
SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}


def render_settings(clip: Clip, user_settings: dict | None = None) -> dict:
    base = {**DEFAULT_RENDER, **((user_settings or {}).get("render") or {})}
    s = {**base, **(clip.render_settings or {})}
    s["style"] = {**CAPTION_PRESETS.get(s["preset"], CAPTION_PRESETS["bold"]), **(s.get("style") or {})}
    return s


# ---------------------------------------------------------------- audio / transcription

def decode_audio(src: Path) -> np.ndarray:
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "f32le", "pipe:"], capture_output=True, timeout=3600)
    if r.returncode != 0:
        raise UserError("Could not extract audio from this video.")
    return np.frombuffer(r.stdout, np.float32)


def loudness_per_second(audio: np.ndarray) -> list[float]:
    n = len(audio) // SR
    if n == 0:
        return []
    return np.sqrt(np.mean(audio[: n * SR].reshape(n, SR) ** 2, axis=1)).round(5).tolist()


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


def transcribe(audio: np.ndarray, model: str, language: str | None, ctx: Ctx, lo: float, hi: float):
    duration = len(audio) / SR
    segs, info = whisper_model(model).transcribe(audio, word_timestamps=True, vad_filter=True, language=language or None)
    out = []
    for s in segs:  # generator: transcription happens as we iterate, so progress is real
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                    "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word,
                               "probability": round(w.probability, 3)} for w in (s.words or [])]})
        ctx.progress(lo + (hi - lo) * min(1.0, s.end / max(duration, 1)), f"Transcribing… {int(s.end // 60)}:{int(s.end % 60):02d}")
    return out, info.language


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
        audio = decode_audio(src)
        loud = loudness_per_second(audio)
        if cached:
            segments, language = cached
        else:
            segments, language = transcribe(audio, model, params["language"], ctx, 0.05, 0.75)
            with SessionLocal() as db:
                db.execute(delete(Transcript).where(Transcript.project_id == p.id))
                db.add(Transcript(project_id=p.id, language=language, model=model, segments=segments,
                                  text=" ".join(s["text"] for s in segments)))
                db.commit()
        del audio

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

        cands = selection.speech_candidates(segments, min_len, max_len, loud)
        speech = sum(s["end"] - s["start"] for s in segments)
        if speech < 0.2 * duration or not cands:
            cands += selection.loudness_candidates(loud, min_len, max_len)
        cands = [c for c in cands if not any(selection._overlap(c, k) for k in keep_windows)]
        if llm.configured() and cands:
            # Only send the strongest few to the (slow) LLM.
            shortlist = selection.pick(cands, count * 3)
            ctx.progress(0.82, f"AI reviewing {len(shortlist)} candidates")
            cands = llm.rerank(shortlist)
        chosen = selection.pick(cands, count)
        if not chosen:
            raise UserError("No suitable clips found. Try a shorter clip length or a video with more speech.")

        meta_prefs = prefs.get("metadata") or {}
        with SessionLocal() as db:
            for i, c in enumerate(chosen):
                ctx.progress(0.88 + 0.1 * i / len(chosen), f"Writing titles and hashtags ({i + 1}/{len(chosen)})")
                m = llm.generate_metadata(c["text"], meta_prefs, language) if c["text"] else \
                    {"titles": [], "description": "", "hashtags": [], "method": "none"}
                db.add(Clip(project_id=p.id, start=c["start"], end=c["end"], transcript_excerpt=c["text"],
                            score=c["score"], score_breakdown={**c["breakdown"], "method": c["method"],
                                                               "metadata_method": m["method"]},
                            reason=c["reason"], title=(m["titles"] or [f"Clip {i + 1}"])[0], title_options=m["titles"],
                            description=m["description"], hashtags=m["hashtags"]))
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
        tr = db.scalar(select(Transcript).where(Transcript.project_id == p.id))
        for i, c in enumerate(clips):
            ctx.progress(i / max(1, len(clips)), f"Clip {i + 1}/{len(clips)}")
            apply_metadata(c, llm.generate_metadata(c.transcript_excerpt, prefs, tr.language if tr else None), fields)
            db.commit()


def apply_metadata(c: Clip, m: dict, fields: set):
    if "titles" in fields and m["titles"]:
        c.title_options = m["titles"]
        if not c.title_edited:  # never overwrite a title the user wrote
            c.title = m["titles"][0]
    if "description" in fields:
        c.description = m["description"]
    if "hashtags" in fields:
        c.hashtags = m["hashtags"]
    c.score_breakdown = {**(c.score_breakdown or {}), "metadata_method": m["method"]}


# ---------------------------------------------------------------- reframing

def _faces(src: Path, start: float, dur: float, w: int, h: int, fps: float = 2.0):
    """Largest face per sampled frame -> [(t, cx_fraction, face_h_fraction) | (t, None, 0)]."""
    import cv2
    sw = 480
    sh = int(round(h * sw / w / 2) * 2)
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                        "-vf", f"fps={fps},scale={sw}:{sh},format=gray", "-f", "rawvideo", "pipe:"],
                       capture_output=True, timeout=600)
    frames = np.frombuffer(r.stdout, np.uint8)
    n = len(frames) // (sw * sh)
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out = []
    for i in range(n):
        f = frames[i * sw * sh:(i + 1) * sw * sh].reshape(sh, sw)
        faces = det.detectMultiScale(f, scaleFactor=1.1, minNeighbors=6, minSize=(sh // 12, sh // 12))
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda b: b[2] * b[3])
            out.append((i / fps, (x + fw / 2) / sw, fh / sh))
        else:
            out.append((i / fps, None, 0.0))
    return out


def crop_track(samples, min_shift: float = 0.12, hold: int = 2) -> list[tuple[float, float]]:
    """Turn noisy face centers into a few stable camera positions: only move when the subject
    has clearly moved (> min_shift of frame width) for `hold` consecutive samples.
    ponytail: hard cuts between positions; add eased pans if hard cuts look jarring."""
    seen = [(t, x) for t, x, _ in samples if x is not None]
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
    return [{"start": w["start"] - start, "end": w["end"] - start, "word": w["word"].strip()}
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


def build_ass(lines: list[dict], W: int, H: int, st: dict, position: str) -> str:
    align, margin_v = {"top": (8, int(H * 0.12)), "middle": (5, 0), "lower": (2, int(H * 0.24)),
                       "bottom": (2, int(H * 0.08))}.get(position, (2, int(H * 0.24)))
    scale = W / 1080
    size = int(st["size"] * scale)
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

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hl = _ass_color(st["highlight"])
    events = []
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
        for line in proc.stdout:
            if line.startswith("out_time_us=") and line.strip() != "out_time_us=N/A":
                done = int(line.split("=")[1]) / 1e6
                ctx.progress(lo + (hi - lo) * min(1.0, done / dur), "Rendering")
    except Cancelled:
        proc.kill()
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
        overrides, fps, cid = clip.caption_overrides, p.fps, clip.id
    try:
        _render(ctx, src, p.id, cid, start, end, sw, sh, fps, st, segments, overrides)
    except BaseException:
        with SessionLocal() as db:
            c = db.get(Clip, cid)
            if c:
                c.render_status = "failed"
                db.commit()
        raise


def _render(ctx, src, pid, cid, start, end, sw, sh, fps, st, segments, overrides):
    dur = end - start
    W, H = SIZES.get(st["aspect"], SIZES["9:16"])
    out_dir = project_dir(pid) / "clips"
    out_dir.mkdir(exist_ok=True)
    tmp, final = out_dir / f"{cid}.tmp.mp4", out_dir / f"{cid}.mp4"

    layout = st["layout"]
    src_ar, dst_ar = sw / sh, W / H
    crop_w = int(sh * dst_ar) // 2 * 2
    can_crop = src_ar > dst_ar + 0.01
    track = [(0.0, 0.5)]
    if can_crop and layout in ("auto", "crop"):
        if st.get("crop_x") is not None:
            track = [(0.0, float(st["crop_x"]))]
            layout = "crop"
        else:
            ctx.progress(0.03, "Finding faces")
            samples = _faces(src, start, dur, sw, sh)
            hits = [s for s in samples if s[1] is not None]
            big = hits and float(np.median([s[2] for s in hits])) >= 0.12
            if layout == "auto":
                # Talking heads: follow the face. Gameplay/screens (or a small facecam): show the whole frame.
                layout = "crop" if len(hits) >= 0.3 * max(1, len(samples)) and big else "fit"
            if layout == "crop":
                track = crop_track(samples)
    elif layout == "auto":
        layout = "fit" if abs(src_ar - dst_ar) > 0.01 else "crop"

    if layout == "crop" and can_crop:
        vf = f"[0:v]crop=w={crop_w}:h={sh // 2 * 2}:x='{_crop_x_expr(track, sw, crop_w)}':y=0,scale={W}:{H}:flags=lanczos,setsar=1"
    elif layout == "crop":  # source narrower than target: crop height instead (centered)
        ch = int(sw / dst_ar) // 2 * 2
        vf = f"[0:v]crop=w={sw // 2 * 2}:h={ch}:x=0:y={(sh - ch) // 2},scale={W}:{H}:flags=lanczos,setsar=1"
    else:
        vf = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"gblur=sigma=30,eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")

    if st["captions"]:
        lines = lines_from_overrides(overrides) if overrides else caption_lines(clip_words(segments, start, end), int(st["style"]["words"]))
        if lines:
            (out_dir / f"{cid}.ass").write_text(build_ass(lines, W, H, st["style"], st["position"]), encoding="utf-8")
            vf += f",ass={cid}.ass"  # relative path via cwd: no filter-path escaping needed
    vf += "[v]"

    out_fps = min(fps or 30, 60)
    try:
        _encode(ctx, src, start, dur, vf, out_fps, tmp, out_dir)
    finally:
        tmp.unlink(missing_ok=True)  # partial output from a failed/cancelled render
        (out_dir / f"{cid}.ass").unlink(missing_ok=True)
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-ss", f"{min(1.0, dur / 2):.2f}", "-i", str(final),
                    "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "3", str(out_dir / f"{cid}.jpg")], timeout=120)
    with SessionLocal() as db:
        c = db.get(Clip, cid)
        if c:
            c.render_status, c.render_path = "done", f"clips/{cid}.mp4"
            c.thumbnail_path = f"clips/{cid}.jpg" if (out_dir / f"{cid}.jpg").exists() else None
            c.render_settings = {**(c.render_settings or {}), "_rendered_layout": layout}
            db.commit()


def _encode(ctx, src, start, dur, vf, out_fps, tmp, out_dir):
    _run_ffmpeg(["-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", vf,
                 "-map", "[v]", "-map", "0:a?", "-r", f"{out_fps:.3f}",
                 "-c:v", "libx264", "-preset", os.getenv("RENDER_PRESET", "medium"), "-crf", os.getenv("RENDER_CRF", "18"),
                 "-profile:v", "high", "-pix_fmt", "yuv420p",
                 "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                 "-movflags", "+faststart", tmp.name], dur, ctx, out_dir, 0.08, 0.97)
    tmp.replace(out_dir / tmp.name.replace(".tmp.mp4", ".mp4"))
