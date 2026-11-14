"use client";

import { AlertTriangle, MonitorPlay, Music2, RotateCcw, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, fmtDate } from "@/lib/api";
import { browserTz, defaultLocalSlot, fmtInZone } from "@/lib/youtube";
import { privacyLabel, type TtAccount, type TtConfig, type TtCreatorInfo, type TtMode, type TtPost, type TtStats } from "@/lib/tiktok";
import { selectCls, TimeZoneSelect } from "@/components/schedule";
import { Button, Card, ErrorNote, Field, Input, Loading, StatusBadge } from "@/components/ui";

export function PlatformBadge({ tiktok }: { tiktok: boolean }) {
  return tiktok
    ? <span className="inline-flex items-center gap-1 rounded bg-pink-500/10 px-1.5 py-0.5 text-[11px] font-medium text-pink-300 ring-1 ring-inset ring-pink-500/25"><Music2 size={11} /> TikTok</span>
    : <span className="inline-flex items-center gap-1 rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] font-medium text-red-300 ring-1 ring-inset ring-red-500/25"><MonitorPlay size={11} /> YouTube</span>;
}

/** TikTok has no scheduling API: ViralClip's worker posts at the chosen time. */
export const SCHEDULE_WARNING = "TikTok has no scheduling API, so scheduled TikTok posts are made by ViralClip itself. They only go out while ViralClip is running; if it's off at that time, the post goes out when it starts again.";

export const NOT_AUDITED = "TikTok hasn't reviewed your app yet, so posts can only be private (Only me) or sent to your TikTok inbox as drafts you finish in the app.";

type Preview = { slots: { clip_id: number; clip_title: string; publish_at_utc: string; local: string }[] };

/**
 * TikTok tab of the Publish dialog. Follows TikTok's Content Posting UX rules: show the creator,
 * no privacy preselected, interaction toggles off by default (and locked when the creator disabled them),
 * commercial content disclosure, and the Music Usage Confirmation line.
 */
export function TikTokPublish({ clipIds, onClose, onDone }: { clipIds: number[]; onClose: () => void; onDone?: (posts: TtPost[]) => void }) {
  const [cfg, setCfg] = useState<TtConfig | null>(null);
  const [accounts, setAccounts] = useState<TtAccount[] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [info, setInfo] = useState<TtCreatorInfo | null>(null);
  const [infoError, setInfoError] = useState("");
  const [modeChoice, setMode] = useState<TtMode | null>(null);
  const [privacy, setPrivacy] = useState("");
  const [allow, setAllow] = useState({ comment: false, duet: false, stitch: false });
  const [disclose, setDisclose] = useState(false);
  const [yourBrand, setYourBrand] = useState(false);
  const [branded, setBranded] = useState(false);
  const [timing, setTiming] = useState<"now" | "schedule">("now");
  const [at, setAt] = useState(defaultLocalSlot());
  const [tz, setTz] = useState(browserTz());
  const [interval, setInterval_] = useState("daily");
  const [hours, setHours] = useState(12);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [dupe, setDupe] = useState(false);
  const [busy, setBusy] = useState(false);

  const mode: TtMode = modeChoice ?? (cfg?.audited ? "direct" : "draft");
  const direct = mode === "direct";
  const bulk = clipIds.length > 1 && timing === "schedule";

  useEffect(() => {
    api<TtConfig>("/api/tiktok/config").then(setCfg).catch((e) => setError(e.message));
    api<TtAccount[]>("/api/tiktok/accounts").then((a) => {
      setAccounts(a);
      setAccountId(a.find((x) => x.status === "ok")?.id ?? null);
    }).catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    if (accountId == null) return;
    let live = true;
    api<TtCreatorInfo>(`/api/tiktok/accounts/${accountId}/creator_info`)
      .then((i) => { if (live) { setInfo(i); setInfoError(""); } })
      .catch((e) => { if (live) setInfoError(e.message); });
    return () => { live = false; };
  }, [accountId]);

  const reset = () => setPreview(null);
  const toggles = direct ? {
    privacy_level: privacy, disable_comment: !allow.comment, disable_duet: !allow.duet, disable_stitch: !allow.stitch,
    brand_content_toggle: disclose && branded, brand_organic_toggle: disclose && yourBrand,
  } : {};
  const scheduleBody = (force = false) => ({
    clip_ids: clipIds, account_id: accountId, start: at, timezone: tz, interval,
    custom_hours: interval === "custom_hours" ? hours : null, mode, ...toggles, force,
  });

  async function run(force = false) {
    setBusy(true); setError(""); setDupe(false);
    try {
      const out = bulk
        ? await api<TtPost[]>("/api/tiktok/schedule/confirm", { method: "POST", json: scheduleBody(force) })
        : await api<TtPost[]>("/api/tiktok/posts", {
          method: "POST",
          json: { clip_ids: clipIds, account_id: accountId, mode, ...toggles, ...(timing === "schedule" ? { publish_at: at, timezone: tz } : {}), force },
        });
      onDone?.(out);
      onClose();
    } catch (e: any) {
      setError(e.message);
      setDupe(e instanceof ApiError && e.status === 409 && !!(e.detail as any)?.duplicate_clip_id);
    } finally { setBusy(false); }
  }

  async function loadPreview() {
    setBusy(true); setError("");
    try { setPreview(await api("/api/tiktok/schedule/preview", { method: "POST", json: scheduleBody() })); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  if (cfg && !cfg.configured) {
    return <p className="text-sm text-muted">TikTok setup needed: it isn&apos;t configured on this server yet. See <Link href="/tiktok" className="text-fg underline">the TikTok page</Link> for setup steps.</p>;
  }
  if (accounts && accounts.length === 0) {
    return <p className="text-sm text-muted">No TikTok account connected. <Link href="/tiktok" className="text-fg underline">Connect an account</Link> first.</p>;
  }
  if (!accounts || !cfg) return <><Loading /><ErrorNote>{error}</ErrorNote></>;

  const privacyOk = !direct || (privacy && (!disclose || yourBrand || branded));
  const ready = accountId != null && (!direct || info) && privacyOk && (timing === "now" || (at && tz));
  const lock = { comment: !!info?.comment_disabled, duet: !!info?.duet_disabled, stitch: !!info?.stitch_disabled };
  const verb = direct ? "Post" : "Send to inbox";

  return (
    <div className="space-y-4">
      {!cfg.audited && <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{NOT_AUDITED}</p>}
      <Field label="TikTok account">
        <select className={selectCls} value={accountId ?? ""} onChange={(e) => { setAccountId(Number(e.target.value)); setInfo(null); setPrivacy(""); reset(); }}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id} disabled={a.status !== "ok"}>
              {a.display_name}{a.status !== "ok" ? " — reconnect required" : ` — ${a.posts_today}/${a.daily_limit} posts today`}
            </option>
          ))}
        </select>
      </Field>
      {info ? <p className="text-sm">Posting as <span className="font-medium">{info.creator_nickname}</span></p>
        : infoError ? <ErrorNote>Couldn&apos;t load this account&apos;s TikTok settings: {infoError}</ErrorNote>
          : accountId != null && <p className="text-xs text-muted">Loading account settings…</p>}

      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-muted">How to post</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {([["draft", "Draft to inbox", "Finish and post it in the TikTok app"], ["direct", "Post directly", "ViralClip posts it for you"]] as const).map(([m, label, hint]) => (
            <label key={m} className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${mode === m ? "border-accent bg-accent/5" : "border-line"}`}>
              <input type="radio" name="tt-mode" checked={mode === m} onChange={() => { setMode(m); reset(); }} className="mt-1 accent-[var(--color-accent)]" />
              <span>{label}<span className="block text-xs text-muted">{hint}</span></span>
            </label>
          ))}
        </div>
      </fieldset>

      {direct && (
        <div className="space-y-3">
          <Field label="Who can view this video">
            <select className={selectCls} value={privacy} onChange={(e) => setPrivacy(e.target.value)} required>
              <option value="" disabled>Choose who can view…</option>
              {(info?.privacy_level_options ?? []).map((p) => (
                <option key={p} value={p} disabled={p === "SELF_ONLY" && disclose && branded}>
                  {privacyLabel(p)}{p === "SELF_ONLY" && disclose && branded ? " (not allowed for branded content)" : ""}
                </option>
              ))}
            </select>
          </Field>
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium text-muted">Allow users to</legend>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {(["comment", "duet", "stitch"] as const).map((k) => (
                <label key={k} className={`flex items-center gap-2 text-sm capitalize ${lock[k] ? "text-muted" : ""}`}>
                  <input type="checkbox" checked={allow[k] && !lock[k]} disabled={lock[k]} onChange={(e) => setAllow({ ...allow, [k]: e.target.checked })} className="accent-[var(--color-accent)]" />
                  {k}{lock[k] && <span className="text-xs normal-case">(off in your TikTok settings)</span>}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-2 rounded-md border border-line p-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={disclose} onChange={(e) => setDisclose(e.target.checked)} className="accent-[var(--color-accent)]" />
              Disclose video content
            </label>
            <p className="text-xs text-muted">Turn on if this video promotes yourself, a third party, or both.</p>
            {disclose && (
              <div className="space-y-2 pl-6">
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" checked={yourBrand} onChange={(e) => setYourBrand(e.target.checked)} className="mt-1 accent-[var(--color-accent)]" />
                  <span>Your brand<span className="block text-xs text-muted">You&apos;re promoting yourself or your own business.</span></span>
                </label>
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" checked={branded} disabled={privacy === "SELF_ONLY"} onChange={(e) => setBranded(e.target.checked)} className="mt-1 accent-[var(--color-accent)]" />
                  <span>Branded content<span className="block text-xs text-muted">{privacy === "SELF_ONLY" ? "Branded content can't be private. Pick another audience first." : "You're promoting another brand or a third party."}</span></span>
                </label>
                {!yourBrand && !branded
                  ? <p className="text-xs text-amber-200">Choose at least one option to continue.</p>
                  : <p className="text-xs text-muted">Your video will be labeled “{branded ? "Paid partnership" : "Promotional content"}”.</p>}
              </div>
            )}
          </div>
        </div>
      )}

      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-muted">When</legend>
        <div className="grid grid-cols-2 gap-2">
          {([["now", direct ? "Post now" : "Send now"], ["schedule", "Schedule"]] as const).map(([t, label]) => (
            <label key={t} className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm ${timing === t ? "border-accent bg-accent/5" : "border-line"}`}>
              <input type="radio" name="tt-timing" checked={timing === t} onChange={() => { setTiming(t); reset(); }} className="accent-[var(--color-accent)]" />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      {timing === "schedule" && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={bulk ? "First slot" : "Post at"}><Input type="datetime-local" value={at} onChange={(e) => { setAt(e.target.value); reset(); }} required /></Field>
            <Field label="Time zone"><TimeZoneSelect value={tz} onChange={(v) => { setTz(v); reset(); }} /></Field>
          </div>
          {bulk && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Then every">
                <select className={selectCls} value={interval} onChange={(e) => { setInterval_(e.target.value); reset(); }}>
                  <option value="daily">Day</option>
                  <option value="every_other_day">Other day</option>
                  <option value="weekly">Week</option>
                  <option value="custom_hours">Custom (hours)</option>
                </select>
              </Field>
              {interval === "custom_hours" && (
                <Field label="Hours between clips"><Input type="number" min={1} max={720} value={hours} onChange={(e) => { setHours(Number(e.target.value)); reset(); }} /></Field>
              )}
            </div>
          )}
          {bulk && preview && (
            <ol className="max-h-56 divide-y divide-line overflow-y-auto rounded-md border border-line text-sm" aria-label="Schedule preview">
              {preview.slots.map((s) => (
                <li key={s.clip_id} className="flex justify-between gap-3 px-3 py-2">
                  <span className="truncate">{s.clip_title || `Clip ${s.clip_id}`}</span>
                  <span className="shrink-0 tabular-nums text-muted">{s.local}</span>
                </li>
              ))}
            </ol>
          )}
          <p className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"><AlertTriangle size={14} className="mt-0.5 shrink-0" /> {SCHEDULE_WARNING}</p>
        </div>
      )}

      <p className="text-xs text-muted">
        By posting, you agree to TikTok&apos;s{" "}
        {direct && disclose && branded && <><a className="underline hover:text-fg" href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noreferrer">Branded Content Policy</a> and </>}
        <a className="underline hover:text-fg" href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noreferrer">Music Usage Confirmation</a>.
        {!direct && " Drafts land in your TikTok inbox notifications; open the app to finish and post them."}
      </p>
      <ErrorNote>{error}</ErrorNote>
      <div className="flex flex-wrap gap-2">
        {bulk && !preview ? (
          <Button variant="primary" disabled={!ready || busy} onClick={loadPreview}>{busy ? "Checking…" : "Preview schedule"}</Button>
        ) : (
          <Button variant="primary" disabled={!ready || busy} onClick={() => run()}>
            {busy ? "Working…" : bulk ? `Confirm ${clipIds.length} slots` : timing === "schedule" ? "Schedule" : verb}
          </Button>
        )}
        {dupe && <Button variant="secondary" disabled={busy} onClick={() => run(true)}>Post again anyway</Button>}
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}

const TT_ACTIVE = ["scheduled", "queued", "auth_required"];

/** Queue/calendar row for a TikTok post, mirroring UploadRow. */
export function TikTokPostRow({ p, onChange }: { p: TtPost; onChange: () => void }) {
  const [error, setError] = useState("");
  const act = async (path: string, method = "POST") => {
    setError("");
    try { await api(`/api/tiktok/posts/${p.id}${path}`, { method }); onChange(); }
    catch (e: any) { setError(e.message); }
  };
  const s = p.status;
  return (
    <div className="space-y-2 p-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="aspect-[9/16] w-12 shrink-0 overflow-hidden rounded bg-panel-2">
          {p.clip_thumbnail_url && <img src={p.clip_thumbnail_url} alt="" className="size-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="truncate text-sm font-medium">{p.clip_title || `Clip ${p.clip_id}`}</p>
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <PlatformBadge tiktok />
            <StatusBadge status={s} />
            <span>{p.mode === "draft" ? "draft to inbox" : privacyLabel(p.privacy_level)}</span> · <span>{p.account_name}</span>
          </p>
          <p className="text-xs text-muted">
            {p.posted_at ? <>{s === "draft_sent" ? "Sent" : "Posted"} {fmtDate(p.posted_at)}</>
              : p.publish_at ? <>Posts {fmtInZone(p.publish_at, p.timezone || "UTC")} ({p.timezone || "UTC"})</> : <>Added {fmtDate(p.created_at)}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {["failed", "auth_required", "cancelled"].includes(s) && <Button variant="ghost" onClick={() => act("/retry")}><RotateCcw size={16} /> Retry</Button>}
          {TT_ACTIVE.includes(s) && <Button variant="ghost" onClick={() => act("/cancel")}><X size={16} /> Cancel</Button>}
          {!["uploading", "processing"].includes(s) && (
            <Button variant="ghost" aria-label="Remove from queue" onClick={() => {
              const msg = ["published", "draft_sent"].includes(s) ? "Stop tracking this item? The video stays on TikTok." : "Remove this item from the queue?";
              if (confirm(msg)) act("", "DELETE");
            }}><Trash2 size={16} /></Button>
          )}
        </div>
      </div>
      {p.error && <p className="text-xs text-red-300">{p.error}</p>}
      <ErrorNote>{error}</ErrorNote>
    </div>
  );
}

/** Dashboard row for TikTok. Renders nothing until TikTok is configured on the server. */
export function TikTokStats() {
  const [s, setS] = useState<TtStats | null>(null);
  useEffect(() => {
    let on = true, t: ReturnType<typeof setInterval> | undefined;
    const load = () => api<TtStats>("/api/tiktok/stats").then((x) => { if (on) setS(x); }).catch(() => {});
    api<TtConfig>("/api/tiktok/config").then((c) => {
      if (!c.configured || !on) return;
      load();
      t = setInterval(load, 10000);
    }).catch(() => {});
    return () => { on = false; clearInterval(t); };
  }, []);
  if (!s) return null;
  const cells: [string, number, string][] = [
    ["Scheduled", s.scheduled, "/calendar"], ["Posting", s.posting, "/queue"], ["Published", s.published, "/calendar"],
    ["Drafts sent", s.drafts_sent, "/queue"], ["Failed", s.failed, "/queue"],
  ];
  return (
    <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-5 [&>*:last-child]:col-span-2 lg:[&>*:last-child]:col-span-1">
      {cells.map(([label, n, href]) => (
        <Link key={label} href={href}>
          <Card className="p-4 transition hover:border-zinc-600">
            <p className="text-xs text-muted">TikTok · {label}</p>
            <p className={`mt-2 text-2xl font-semibold tabular-nums ${label === "Failed" && n ? "text-red-300" : ""}`}>{n}</p>
          </Card>
        </Link>
      ))}
    </div>
  );
}
