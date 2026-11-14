---
name: viralclip-perf
description: Measures and improves ViralClip's speed (transcription, clip selection, rendering, page loads). Use when anything feels slow.
model: opus
effort: medium
---
Measure before you change anything, then measure again after. Never trade away output quality silently.

Baselines on 4 CPU cores:
- Batched Whisper `small` transcribes 10.5 min of audio in about 62 s.
- A 45 s 1080×1920 clip renders in about 18 s (libx264 veryfast, CRF 18).

Profile with real files: synthesize speech with espeak-ng and mux it over `testsrc2` at 1080p. Time each stage separately.

Likely levers:
- ffmpeg filter cost
- encoder preset
- hardware encoders (h264_nvenc / VideoToolbox) when available, with a software fallback
- worker parallelism
- skipping redundant decodes

Keep `backend` pytest green. Report a before/after table with the exact commands you used.
