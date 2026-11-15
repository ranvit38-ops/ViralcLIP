"""Long sources (multi-hour VODs) without loading them whole: audio streamed from one ffmpeg pipe block by
block, regrouped into transcription chunks cut at silences, plus a watchdog that kills a hung ffmpeg."""
import math
import subprocess
import threading
import time

import numpy as np

from .worker import UserError


class Watchdog:
    """Kills `proc` once an armed deadline passes; leaving the `with` block then raises UserError.
    arm(s) sets a deadline s seconds from now, disarm() clears it (e.g. arm around each pipe read)."""

    def __init__(self, proc, what: str):
        self.proc, self.what, self.deadline, self.limit, self.fired = proc, what, None, 0.0, False
        self._stop = threading.Event()

    def arm(self, seconds: float):
        self.limit, self.deadline = seconds, time.monotonic() + seconds

    def disarm(self):
        self.deadline = None

    def _watch(self):
        while not self._stop.wait(0.2):
            if self.deadline is not None and time.monotonic() > self.deadline:
                self.fired = True
                self.proc.kill()
                return

    def __enter__(self):
        threading.Thread(target=self._watch, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self.fired:
            raise UserError(timeout_message(self.what, self.limit))


def timeout_message(what: str, seconds: float) -> str:
    return (f"{what} took longer than {seconds:.0f} s and was stopped. The file may be damaged or the disk too "
            "slow; try again, or re-upload the video.")


def run(args: list[str], timeout: float, what: str, **kw) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout that becomes a UserError (subprocess.run kills the child)."""
    try:
        return subprocess.run(args, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise UserError(timeout_message(what, timeout)) from e


def stream(ffmpeg: str, src, sr: int, block_s: int = 60, stall: float = 120.0):
    """The source's audio as mono float32 blocks of exactly block_s seconds (the last one shorter), read
    incrementally from one ffmpeg pipe. A read that gets nothing for `stall` s kills ffmpeg (UserError)."""
    proc = subprocess.Popen([ffmpeg, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
                             "-f", "f32le", "pipe:"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = block_s * sr * 4
    try:
        with Watchdog(proc, "Reading the audio") as wd:
            while True:
                wd.arm(stall)
                buf = proc.stdout.read(n)
                wd.disarm()
                if len(buf) >= 4:
                    yield np.frombuffer(buf, np.float32, len(buf) // 4)
                if len(buf) < n:
                    break
        if proc.wait() != 0:
            raise UserError("Could not extract audio from this video.")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def chunks(blocks, sr: int, chunk_s: float = 900, overlap_s: float = 2.0, search_s: float = 30.0):
    """Regroup contiguous audio blocks into transcription chunks of ~chunk_s seconds. Each boundary is the
    quietest 100 ms in the last search_s seconds before the nominal end, and each chunk carries overlap_s
    of context on both sides. Yields (t0, audio, keep_end): audio starts at t0 s; the words that belong to
    this chunk are those whose midpoint falls before keep_end s (see keep_words). Holds ~chunk_s of audio."""
    chunk, over, search, win = int(chunk_s * sr), int(overlap_s * sr), int(min(search_s, chunk_s / 2) * sr), sr // 10
    parts, have, off, keep_from = [], 0, 0, 0  # buffered blocks, their length, sample index of buffer[0], last cut
    for b in blocks:
        parts.append(b)
        have += len(b)
        while off + have >= keep_from + chunk + over:
            buf = np.concatenate(parts)
            a = keep_from + chunk - search - off
            n = search // win
            rms = (buf[a:a + n * win].astype(np.float64).reshape(n, win) ** 2).mean(axis=1)
            cut = off + a + int(np.argmin(rms)) * win + win // 2
            yield off / sr, buf[:cut + over - off], cut / sr
            start = max(off, cut - over)
            parts, have, off, keep_from = [buf[start - off:].copy()], off + have - start, start, cut
    if have:
        yield off / sr, np.concatenate(parts), math.inf


def keep_words(words: list[dict], keep_end: float, after: float) -> list[dict]:
    """Stitch one chunk's words (absolute times) onto the transcript so far: keep those whose midpoint is after
    the last kept word's end (`after`; drops the overlap's duplicates) and before keep_end (the next chunk
    owns the rest, so a word straddling the cut is kept by exactly one side)."""
    return [w for w in words if after < (w["start"] + w["end"]) / 2 < keep_end]
