export type YtAccount = {
  id: number; channel_id: string; channel_title: string; thumbnail_url: string | null; status: "ok" | "reauth_required";
  created_at: string; uploads_today: number; daily_limit: number;
  rule: { enabled: boolean; time_of_day: string; timezone: string; clips_per_day: number; project_ids: number[] } | null;
};

export type YtUpload = {
  id: number; clip_id: number; project_id: number | null; clip_title: string; clip_thumbnail_url: string | null;
  account_id: number | null; channel_title: string; upload_status: string; privacy: string; schedule_status: string;
  publish_at: string | null; timezone: string | null; error: string | null; progress: number; run_after: string | null;
  youtube_video_id: string | null; youtube_url: string | null; created_at: string; uploaded_at: string | null;
};

export type YtConfig = { configured: boolean; missing: string[]; redirect_uri: string; daily_limit: number };

/** Backend timestamps are UTC; SQLite drops the offset, so a naive string means UTC. */
export function parseUtc(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
}

export function browserTz(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function timeZones(): string[] {
  const all = (Intl as any).supportedValuesOf?.("timeZone") as string[] | undefined;
  return all?.length ? (all.includes("UTC") ? all : ["UTC", ...all]) : ["UTC", browserTz()];
}

export function fmtInZone(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, { ...opts, timeZone: tz }).format(parseUtc(iso));
  } catch {
    return parseUtc(iso).toISOString();
  }
}

/** 'YYYY-MM-DD' of an instant as seen in `tz`. */
export function dayKey(d: Date, tz: string) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

/** Default for <input type="datetime-local">: tomorrow at 10:00 in the browser's zone. */
export function defaultLocalSlot() {
  const d = new Date(Date.now() + 86400000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T10:00`;
}

/** An instant as 'YYYY-MM-DDTHH:MM' wall-clock in `tz`, for prefilling <input type="datetime-local">. */
export function localInput(iso: string, tz: string) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(parseUtc(iso)).replace(" ", "T");
}

/** The zone a scheduled item was created in, and the time there. */
export function whenLabel(u: YtUpload) {
  if (u.publish_at) return `${fmtInZone(u.publish_at, u.timezone || "UTC")} (${u.timezone || "UTC"})`;
  return null;
}
