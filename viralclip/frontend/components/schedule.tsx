"use client";

import { CalendarClock, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { browserTz, defaultLocalSlot, localInput, timeZones, type YtAccount, type YtConfig, type YtUpload } from "@/lib/youtube";
import { Button, Card, ErrorNote, Field, Input } from "@/components/ui";

const selectCls = "h-9 w-full rounded-md border border-line bg-bg px-2 text-sm outline-none focus:border-accent";

export function TimeZoneSelect({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) {
  return (
    <select id={id} className={selectCls} value={value} onChange={(e) => onChange(e.target.value)}>
      {timeZones().map((z) => <option key={z} value={z}>{z.replaceAll("_", " ")}</option>)}
    </select>
  );
}

type Mode = "schedule" | "now" | "private" | "unlisted";
type Preview = { timezone: string; channel_title: string; warnings: string[]; slots: { clip_id: number; title: string; publish_at_local: string }[] };

/**
 * Send one or more approved, rendered clips to YouTube: publish now, upload private/unlisted,
 * or schedule (one time, or a bulk series with a preview before anything is created).
 */
export function ScheduleDialog({ clipIds, onClose, onDone }: { clipIds: number[]; onClose: () => void; onDone?: (uploads: YtUpload[]) => void }) {
  const [cfg, setCfg] = useState<YtConfig | null>(null);
  const [accounts, setAccounts] = useState<YtAccount[] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>("schedule");
  const [when, setWhen] = useState(defaultLocalSlot());
  const [tz, setTz] = useState(browserTz());
  const [interval, setInterval_] = useState("daily");
  const [hours, setHours] = useState(12);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [dupe, setDupe] = useState(false);
  const [busy, setBusy] = useState(false);
  const bulk = clipIds.length > 1 && mode === "schedule";

  useEffect(() => {
    api<YtConfig>("/api/youtube/config").then(setCfg).catch(() => {});
    api<YtAccount[]>("/api/youtube/accounts").then((a) => {
      setAccounts(a);
      setAccountId(a.find((x) => x.status === "ok")?.id ?? null);
    }).catch((e) => setError(e.message));
  }, []);

  const scheduleBody = (force = false) => ({
    clip_ids: clipIds, account_id: accountId, start: when, timezone: tz, interval,
    custom_hours: interval === "custom_hours" ? hours : null, force,
  });

  async function run(force = false) {
    setBusy(true); setError(""); setDupe(false);
    try {
      let out: YtUpload[];
      if (bulk) {
        out = await api("/api/youtube/schedule/confirm", { method: "POST", json: scheduleBody(force) });
      } else {
        out = await api("/api/youtube/uploads", {
          method: "POST",
          json: {
            clip_ids: clipIds, account_id: accountId, force,
            privacy: mode === "now" ? "public" : mode === "schedule" ? "private" : mode,
            ...(mode === "schedule" ? { publish_at: when, timezone: tz } : {}),
          },
        });
      }
      onDone?.(out);
      onClose();
    } catch (e: any) {
      setError(e.message);
      setDupe(e instanceof ApiError && e.status === 409 && !!(e.detail as any)?.duplicate_clip_id);
    } finally { setBusy(false); }
  }

  async function loadPreview() {
    setBusy(true); setError("");
    try { setPreview(await api("/api/youtube/schedule/preview", { method: "POST", json: scheduleBody() })); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  const ready = accountId != null && (mode !== "schedule" || (when && tz));
  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/60 p-4" role="dialog" aria-modal="true" aria-labelledby="sched-title">
      <Card className="w-full max-w-lg p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 id="sched-title" className="flex items-center gap-2 font-semibold"><CalendarClock size={18} /> Send {clipIds.length} clip{clipIds.length > 1 ? "s" : ""} to YouTube</h2>
          <button onClick={onClose} aria-label="Close" className="text-muted hover:text-fg"><X size={18} /></button>
        </div>

        {cfg && !cfg.configured ? (
          <p className="text-sm text-muted">YouTube isn&apos;t set up on this server yet. See <Link href="/youtube" className="text-fg underline">YouTube Studio</Link> for setup steps.</p>
        ) : accounts && accounts.length === 0 ? (
          <p className="text-sm text-muted">No channel connected. <Link href="/youtube" className="text-fg underline">Connect a channel</Link> first.</p>
        ) : !accounts ? <p className="text-sm text-muted">Loading…</p> : (
          <div className="space-y-4">
            <Field label="Channel">
              <select className={selectCls} value={accountId ?? ""} onChange={(e) => { setAccountId(Number(e.target.value)); setPreview(null); }}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id} disabled={a.status !== "ok"}>
                    {a.channel_title}{a.status !== "ok" ? " — reconnect required" : ` — ${a.uploads_today}/${a.daily_limit} uploads today`}
                  </option>
                ))}
              </select>
            </Field>

            <fieldset>
              <legend className="mb-1.5 text-xs font-medium text-muted">Visibility</legend>
              <div className="grid grid-cols-2 gap-2">
                {([["schedule", "Schedule public"], ["now", "Publish now (public)"], ["private", "Upload as private"], ["unlisted", "Upload as unlisted"]] as const).map(([m, label]) => (
                  <label key={m} className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm ${mode === m ? "border-accent bg-accent/5" : "border-line"}`}>
                    <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => { setMode(m); setPreview(null); }} className="accent-[var(--color-accent)]" />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>

            {mode === "schedule" && (
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={bulk ? "First slot" : "Publish at"}>
                    <Input type="datetime-local" value={when} onChange={(e) => { setWhen(e.target.value); setPreview(null); }} required />
                  </Field>
                  <Field label="Time zone"><TimeZoneSelect value={tz} onChange={(v) => { setTz(v); setPreview(null); }} /></Field>
                </div>
                <p className="text-xs text-muted">The time is interpreted in <span className="text-fg">{tz}</span>. Nothing is converted silently.</p>
                {bulk && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Then every">
                      <select className={selectCls} value={interval} onChange={(e) => { setInterval_(e.target.value); setPreview(null); }}>
                        <option value="daily">Day</option>
                        <option value="every_other_day">Other day</option>
                        <option value="weekly">Week</option>
                        <option value="custom_hours">Custom (hours)</option>
                      </select>
                    </Field>
                    {interval === "custom_hours" && (
                      <Field label="Hours between clips">
                        <Input type="number" min={1} max={720} value={hours} onChange={(e) => { setHours(Number(e.target.value)); setPreview(null); }} />
                      </Field>
                    )}
                  </div>
                )}
                {bulk && preview && (
                  <div className="space-y-2">
                    {preview.warnings.map((w) => <p key={w} className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{w}</p>)}
                    <ol className="max-h-56 divide-y divide-line overflow-y-auto rounded-md border border-line text-sm">
                      {preview.slots.map((s) => (
                        <li key={s.clip_id} className="flex justify-between gap-3 px-3 py-2">
                          <span className="truncate">{s.title || `Clip ${s.clip_id}`}</span>
                          <span className="shrink-0 tabular-nums text-muted">{s.publish_at_local}</span>
                        </li>
                      ))}
                    </ol>
                    <p className="text-xs text-muted">Times in {preview.timezone}, on {preview.channel_title}.</p>
                  </div>
                )}
              </div>
            )}

            <p className="text-xs text-muted">
              Videos from an unaudited Google Cloud project stay private on YouTube, even when scheduled. Uploads past the daily limit wait for the next quota reset.
            </p>
            <ErrorNote>{error}</ErrorNote>
            <div className="flex flex-wrap gap-2">
              {bulk && !preview ? (
                <Button variant="primary" disabled={!ready || busy} onClick={loadPreview}>{busy ? "Checking…" : "Preview schedule"}</Button>
              ) : (
                <Button variant="primary" disabled={!ready || busy} onClick={() => run()}>
                  {busy ? "Working…" : bulk ? `Confirm ${clipIds.length} slots` : mode === "schedule" ? "Schedule" : "Upload"}
                </Button>
              )}
              {dupe && <Button variant="secondary" disabled={busy} onClick={() => run(true)}>Upload again anyway</Button>}
              <Button variant="ghost" onClick={onClose}>Cancel</Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

/** Inline editor for a not-yet-uploaded item's publish time. */
export function EditTime({ upload, onSaved }: { upload: YtUpload; onSaved: (u: YtUpload) => void }) {
  const [open, setOpen] = useState(false);
  const [tz, setTz] = useState(upload.timezone || browserTz());
  const [when, setWhen] = useState(upload.publish_at ? localInput(upload.publish_at, upload.timezone || browserTz()) : defaultLocalSlot());
  const [error, setError] = useState("");
  if (upload.youtube_video_id) return null;
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}><CalendarClock size={16} /> {upload.publish_at ? "Change time" : "Schedule"}</Button>;
  return (
    <form className="flex w-full flex-wrap items-end gap-2" onSubmit={async (e) => {
      e.preventDefault(); setError("");
      try { onSaved(await api(`/api/youtube/uploads/${upload.id}`, { method: "PATCH", json: { publish_at: when, timezone: tz } })); setOpen(false); }
      catch (err: any) { setError(err.message); }
    }}>
      <div className="w-48"><Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} required aria-label="New publish time" /></div>
      <div className="w-56"><TimeZoneSelect value={tz} onChange={setTz} /></div>
      <Button variant="primary">Save</Button>
      <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      {error && <div className="w-full"><ErrorNote>{error}</ErrorNote></div>}
    </form>
  );
}
