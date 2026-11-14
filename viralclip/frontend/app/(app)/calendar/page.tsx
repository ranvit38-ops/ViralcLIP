"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { browserTz, dayKey, fmtInZone, parseUtc, type YtUpload } from "@/lib/youtube";
import { TimeZoneSelect } from "@/components/schedule";
import { groupOf, UploadRow } from "@/components/uploads";
import { Button, Card, ErrorNote, PageHeader } from "@/components/ui";

type View = "month" | "week" | "day";

// Calendar dates are plain 'YYYY-MM-DD' keys; arithmetic happens in UTC so the browser's zone never leaks in.
const toDate = (k: string) => { const [y, m, d] = k.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const keyOf = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (k: string, n: number) => keyOf(new Date(toDate(k).getTime() + n * 86400000));
const addMonths = (k: string, n: number) => { const d = toDate(k); return keyOf(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1))); };
const label = (k: string, o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(undefined, { ...o, timeZone: "UTC" }).format(toDate(k));
const when = (u: YtUpload) => u.publish_at ?? u.uploaded_at ?? u.created_at;

const TONE: Record<string, string> = {
  Scheduled: "border-sky-500/40 bg-sky-500/10 text-sky-200", Published: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  Failed: "border-red-500/40 bg-red-500/10 text-red-200", Pending: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  Uploading: "border-amber-500/40 bg-amber-500/10 text-amber-200", Uploaded: "border-zinc-500/40 bg-zinc-500/10 text-zinc-200",
  Cancelled: "border-zinc-700 bg-transparent text-zinc-500 line-through",
};

const noop = () => () => {};

export default function CalendarPage() {
  // Time zones and "today" differ between the server prerender and the browser: render client-side only.
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  return hydrated ? <Calendar /> : <p className="text-sm text-muted">Loading…</p>;
}

function Calendar() {
  const [tz, setTz] = useState(browserTz());
  const [view, setView] = useState<View>("month");
  const [anchor, setAnchor] = useState(() => dayKey(new Date(), browserTz()));
  const [items, setItems] = useState<YtUpload[] | null>(null);
  const [selected, setSelected] = useState<YtUpload | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => api<YtUpload[]>("/api/youtube/uploads").then((us) => {
    setItems(us);
    setSelected((s) => (s ? us.find((u) => u.id === s.id) ?? null : null));
  }).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const byDay = useMemo(() => {
    const m: Record<string, YtUpload[]> = {};
    for (const u of items ?? []) (m[dayKey(parseUtc(when(u)), tz)] ??= []).push(u);
    for (const k in m) m[k].sort((a, b) => parseUtc(when(a)).getTime() - parseUtc(when(b)).getTime());
    return m;
  }, [items, tz]);

  const today = dayKey(new Date(), tz);
  const first = anchor.slice(0, 8) + "01";
  const days = view === "month"
    ? Array.from({ length: 42 }, (_, i) => addDays(first, i - toDate(first).getUTCDay()))
    : view === "week" ? Array.from({ length: 7 }, (_, i) => addDays(anchor, i - toDate(anchor).getUTCDay())) : [anchor];
  const title = view === "month" ? label(anchor, { month: "long", year: "numeric" })
    : view === "week" ? `${label(days[0], { month: "short", day: "numeric" })} – ${label(days[6], { month: "short", day: "numeric", year: "numeric" })}`
    : label(anchor, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  const step = (n: number) => setAnchor(view === "month" ? addMonths(anchor, n) : addDays(anchor, n * (view === "week" ? 7 : 1)));
  const time = (u: YtUpload) => fmtInZone(when(u), tz, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

  const chip = (u: YtUpload, compact: boolean) => (
    <button key={u.id} onClick={() => setSelected(u)} title={`${u.clip_title} · ${groupOf(u)}`}
      className={`block w-full truncate rounded border px-1.5 py-0.5 text-left text-[11px] ${TONE[groupOf(u)]}`}>
      <span className="tabular-nums">{time(u)}</span>{!compact && <> · {u.clip_title || `Clip ${u.clip_id}`}</>}
      {compact && <span className="hidden sm:inline"> · {u.clip_title || `Clip ${u.clip_id}`}</span>}
    </button>
  );

  return (
    <>
      <PageHeader title="Publishing Calendar" sub="Scheduled, published, pending and failed uploads." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-line p-0.5">
          {(["month", "week", "day"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} className={`rounded px-3 py-1 text-sm capitalize ${view === v ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"}`}>{v}</button>
          ))}
        </div>
        <Button variant="ghost" aria-label="Previous" onClick={() => step(-1)}><ChevronLeft size={16} /></Button>
        <Button variant="ghost" onClick={() => setAnchor(today)}>Today</Button>
        <Button variant="ghost" aria-label="Next" onClick={() => step(1)}><ChevronRight size={16} /></Button>
        <h2 className="mr-auto text-sm font-medium">{title}</h2>
        <label className="flex items-center gap-2 text-xs text-muted">
          Times shown in
          <span className="w-52"><TimeZoneSelect value={tz} onChange={setTz} /></span>
        </label>
      </div>
      <ErrorNote>{error}</ErrorNote>

      {!items ? <p className="text-sm text-muted">Loading…</p> : view === "day" ? (
        <Card className="divide-y divide-line">
          {(byDay[anchor] ?? []).length === 0 ? <p className="p-4 text-sm text-muted">Nothing on this day.</p>
            : byDay[anchor].map((u) => <div key={u.id} className="p-2">{chip(u, false)}</div>)}
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="grid grid-cols-7 border-b border-line text-center text-[11px] text-muted">
            {days.slice(0, 7).map((d) => <div key={d} className="py-1.5">{label(d, { weekday: "short" })}</div>)}
          </div>
          <div className="grid grid-cols-7">
            {days.map((d) => {
              const list = byDay[d] ?? [];
              const out = view === "month" && d.slice(0, 7) !== anchor.slice(0, 7);
              return (
                <div key={d} className={`min-w-0 space-y-1 border-b border-r border-line p-1 ${view === "week" ? "min-h-48" : "min-h-20 sm:min-h-24"} ${out ? "bg-bg/60 text-muted" : ""}`}>
                  <button onClick={() => { setAnchor(d); setView("day"); }}
                    className={`grid size-6 place-items-center rounded-full text-[11px] tabular-nums ${d === today ? "bg-accent text-accent-fg" : "hover:bg-panel-2"}`}>
                    {Number(d.slice(8))}
                  </button>
                  {list.slice(0, view === "month" ? 3 : 50).map((u) => chip(u, true))}
                  {view === "month" && list.length > 3 && (
                    <button onClick={() => { setAnchor(d); setView("day"); }} className="text-[11px] text-muted hover:text-fg">+{list.length - 3} more</button>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {selected && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true" onClick={() => setSelected(null)}>
          <Card className="w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-line px-3 py-2 text-sm">
              <span className="text-muted">{fmtInZone(when(selected), tz)} ({tz})</span>
              <button aria-label="Close" onClick={() => setSelected(null)} className="text-muted hover:text-fg"><X size={18} /></button>
            </div>
            <UploadRow u={selected} onChange={load} />
          </Card>
        </div>
      )}
    </>
  );
}
