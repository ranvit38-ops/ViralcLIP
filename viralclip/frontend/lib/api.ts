export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

/** JSON fetch against the FastAPI backend (same origin via Next rewrites). */
export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const res = await fetch(path, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json", ...rest.headers } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : d?.message ?? (Array.isArray(d) ? d[0]?.msg : null) ?? `Request failed (${res.status})`;
    if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    }
    throw new ApiError(res.status, msg, d);
  }
  return body as T;
}

export function fmtDuration(s?: number | null) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtBytes(n?: number | null) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  // Backend stores UTC; SQLite drops the offset, so treat naive timestamps as UTC.
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export type Job = {
  id: number; project_id: number; job_type: string; status: string; progress: number;
  message: string; error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
};

export type Project = {
  id: number; name: string; status: string; archived: boolean; error: string | null; settings: Record<string, any>;
  source_filename: string | null; upload_size: number | null; bytes_received: number;
  duration: number | null; width: number | null; height: number | null; fps: number | null;
  video_codec: string | null; audio_codec: string | null; has_audio: boolean | null;
  created_at: string; updated_at: string; thumbnail_url: string | null; clip_count?: number; jobs?: Job[];
};

export type CaptionStyle = {
  font: string; size: number; color: string; highlight: string; outline: string; outline_width: number;
  shadow: number; uppercase: boolean; words: number; box: boolean; pop: boolean; bold: boolean;
};

export type RenderSettings = {
  aspect: string; layout: string; crop_x: number | null; captions: boolean; preset: string; position: string;
  style: CaptionStyle; remove_pauses: boolean; zoom: boolean; hook: boolean; hook_text: string | null;
  _rendered_layout?: string; _rendered_duration?: number; _cuts?: number;
};

export type CaptionLine = { start: number; end: number; text: string };

export type Clip = {
  id: number; project_id: number; project_name: string; start: number; end: number; duration: number;
  transcript_excerpt: string; score: number; score_breakdown: Record<string, any>; reason: string;
  review_status: "suggested" | "approved" | "rejected"; title: string; title_options: string[]; title_edited: boolean;
  description: string; hashtags: string[]; render_status: string; caption_overrides: CaptionLine[];
  render_settings: RenderSettings; video_url: string | null; thumbnail_url: string; created_at: string; job: Job | null;
};

export type SystemInfo = {
  whisper_models: string[]; whisper_downloaded: string[]; whisper_default: string; whisper_device: string;
  llm_configured: boolean; llm_model: string | null; caption_presets: Record<string, CaptionStyle>; fonts: string[];
  disk_free: number; disk_total: number; storage_used: number; job_running: boolean;
};

export const ACTIVE_JOB = ["queued", "running", "cancel_requested"];

/** POST JSON, receive a file, and hand it to the browser as a download. */
export async function downloadPost(path: string, json: unknown, filename: string) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(json) });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, typeof body?.detail === "string" ? body.detail : `Download failed (${res.status})`);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** m:ss.s for clip boundaries. */
export function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}
