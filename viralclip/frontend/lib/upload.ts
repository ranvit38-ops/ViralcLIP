"use client";

import { api, ApiError } from "./api";

const CHUNK = 8 * 1024 * 1024; // must stay <= backend MAX_CHUNK_BYTES (16 MB)
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Resumable upload: asks the server for its current offset, then sends chunks from there.
 * Network blips back off and resync with the server; a 409 means the server has a different
 * offset (e.g. a retried chunk already landed), so we jump to it. Works across page reloads.
 */
export async function uploadFile(pid: number, file: File, onProgress: (sent: number) => void, signal: AbortSignal) {
  let { offset } = await api<{ offset: number }>(`/api/projects/${pid}/upload`, { signal });
  let failures = 0;
  onProgress(offset);
  while (offset < file.size) {
    signal.throwIfAborted();
    try {
      const res = await fetch(`/api/projects/${pid}/upload?offset=${offset}`, {
        method: "PUT", body: file.slice(offset, offset + CHUNK), signal,
        headers: { "Content-Type": "application/octet-stream" },
      });
      const body = await res.json().catch(() => null);
      if (res.status === 409 && typeof body?.detail?.offset === "number") { offset = body.detail.offset; continue; }
      if (!res.ok) throw new ApiError(res.status, body?.detail?.message ?? body?.detail ?? `Upload failed (${res.status})`);
      offset = body.offset;
      failures = 0;
      onProgress(offset);
    } catch (e) {
      if (signal.aborted || (e instanceof ApiError && e.status < 500)) throw e;
      if (++failures > 6) throw new Error("Upload kept failing. Check your connection and resume later; progress is saved.");
      await sleep(Math.min(30000, 500 * 2 ** failures));
      offset = (await api<{ offset: number }>(`/api/projects/${pid}/upload`, { signal })).offset;
    }
  }
  return api(`/api/projects/${pid}/upload/complete`, { method: "POST", signal });
}

import { useRef, useState } from "react";

/** React state around uploadFile: progress, error, cancel. */
export function useUpload() {
  const [sent, setSent] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ctrl = useRef<AbortController | null>(null);

  async function start(pid: number, file: File) {
    ctrl.current = new AbortController();
    setBusy(true); setError("");
    try {
      return await uploadFile(pid, file, setSent, ctrl.current.signal);
    } catch (e: any) {
      if (!ctrl.current.signal.aborted) setError(e.message ?? "Upload failed");
      return null;
    } finally { setBusy(false); }
  }
  return { sent, busy, error, start, abort: () => ctrl.current?.abort() };
}
