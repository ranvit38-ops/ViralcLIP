"use client";

import { Link2, MonitorPlay, Music2, Radio, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Button, Card, Empty, ErrorNote, Field, Input, Loading, PageHeader, Section, StatusBadge, Textarea } from "@/components/ui";

type Platform = "youtube" | "twitch" | "tiktok";
type Status = "waiting" | "started" | "done" | "skipped" | "failed";
type Item = { id: number; url: string; platform: Platform; status: Status; note: string | null; project_id: number | null; created_at: string };
type Queue = { items: Item[]; buffer_days: number; automation_enabled: boolean };
type AddResult = { added: { id: number; url: string; platform: Platform; status: Status }[]; rejected: { url: string; reason: string }[] };

const ICON: Record<Platform, typeof Radio> = { twitch: Radio, youtube: MonitorPlay, tiktok: Music2 };
const REMOVABLE: Status[] = ["waiting", "skipped", "failed"];
const MAX = 50;

function AddLinks({ onAdded }: { onAdded: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState<AddResult | null>(null);
  const links = [...new Set(text.split("\n").map((l) => l.trim()).filter(Boolean))];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setRes(null);
    try {
      const r = await api<AddResult>("/api/queue", { method: "POST", json: { links } });
      setRes(r);
      setText(r.rejected.map((x) => x.url).join("\n")); // keep only what needs fixing
      onAdded();
    } catch (x) { setError(x instanceof Error ? x.message : "Could not add links"); } finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <Field label="Links, one per line" hint={`${links.length} link${links.length === 1 ? "" : "s"} · up to ${MAX} at a time`}>
        <Textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
          placeholder={"https://www.youtube.com/watch?v=…\nhttps://www.twitch.tv/videos/…"} />
      </Field>
      {links.length > MAX && <p className="text-xs text-amber-300">That&apos;s {links.length} links. Add {MAX} or fewer at a time.</p>}
      <Button variant="primary" disabled={busy || !links.length || links.length > MAX}>{busy ? "Adding…" : "Add to queue"}</Button>
      <ErrorNote>{error}</ErrorNote>
      {res && (
        <div aria-live="polite" className="space-y-2 text-sm">
          <p className="text-emerald-300">{res.added.length} added to the queue.</p>
          {res.rejected.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
              <p className="mb-1 font-medium text-amber-200">{res.rejected.length} not added (left in the box above):</p>
              <ul className="space-y-1">
                {res.rejected.map((r, i) => (
                  <li key={i} className="min-w-0 text-xs"><span className="break-all text-fg">{r.url}</span> <span className="text-amber-200">· {r.reason}</span></li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </form>
  );
}

function BufferDays({ initial }: { initial: number }) {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [days, setDays] = useState(initial);
  const [saved, setSaved] = useState(initial);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api<{ settings: Record<string, unknown> }>("/api/automation").then((d) => setSettings(d.settings)).catch((e) => setError(e.message)); }, []);

  async function save() {
    setError(""); setMsg("");
    try {
      // PUT takes the whole settings object, same as the Automation page.
      const r = await api<Record<string, unknown>>("/api/automation", { method: "PUT", json: { ...settings, queue_buffer_days: days } });
      setSettings(r); setSaved(days); setMsg("Saved");
    } catch (e) { setError(e instanceof Error ? e.message : "Save failed"); }
  }
  const valid = Number.isInteger(days) && days >= 1 && days <= 14;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-start gap-2">
        <Field label="Buffer (days)" hint="The next link starts when your scheduled posts last fewer days than this (1–14).">
          <Input type="number" min={1} max={14} value={days} onChange={(e) => { setDays(+e.target.value); setMsg(""); }} className="w-24" />
        </Field>
        <Button onClick={save} disabled={!settings || !valid || days === saved} className="mt-6">Save</Button>
        {msg && <span className="mt-8 text-sm text-emerald-300">{msg}</span>}
      </div>
      <ErrorNote>{error}</ErrorNote>
    </div>
  );
}

export default function LinkQueuePage() {
  const [q, setQ] = useState<Queue | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => api<Queue>("/api/queue").then((d) => { setQ(d); setError(""); }).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const busy = q?.items.some((i) => i.status === "started");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function remove(i: Item) {
    setError("");
    try { await api(`/api/queue/${i.id}`, { method: "DELETE" }); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Remove failed"); }
  }

  return (
    <>
      <PageHeader title="Link Queue"
        sub="Paste videos from channels you connected. They are clipped one at a time, and the next one starts when your scheduled posts run low." />
      <div className="space-y-4">
        <ErrorNote>{error}</ErrorNote>
        {!q ? <Loading /> : (
          <>
            {!q.automation_enabled && (
              <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                Automation is off, so queued links won&apos;t start. <Link href="/automation" className="font-medium text-accent hover:underline">Turn it on in Automation →</Link>
              </p>
            )}
            <Section title="Add links"><AddLinks onAdded={load} /></Section>
            <Section title="Pacing"><BufferDays initial={q.buffer_days} /></Section>
            {q.items.length === 0 ? (
              <Empty icon={<Link2 size={22} />} title="Queue is empty">Paste a few links above to keep your channel posting.</Empty>
            ) : (
              <Card className="divide-y divide-line">
                {q.items.map((i) => {
                  const Icon = ICON[i.platform] ?? Link2;
                  return (
                    <div key={i.id} className="flex items-start gap-3 p-3">
                      <Icon size={16} className="mt-0.5 shrink-0 text-muted" aria-label={i.platform} />
                      <div className="min-w-0 flex-1 space-y-1">
                        <a href={i.url} target="_blank" rel="noreferrer" className="block truncate text-sm hover:underline">{i.url}</a>
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                          <StatusBadge status={i.status} />
                          <span>added {fmtDate(i.created_at)}</span>
                          {i.project_id != null && <Link href={`/projects/${i.project_id}`} className="text-accent hover:underline">Open project</Link>}
                        </div>
                        {i.note && <p className={`text-xs ${i.status === "failed" ? "text-red-300" : "text-muted"}`}>{i.note}</p>}
                      </div>
                      {REMOVABLE.includes(i.status) && (
                        <Button variant="ghost" aria-label={`Remove ${i.url}`} onClick={() => remove(i)}><Trash2 size={15} /></Button>
                      )}
                    </div>
                  );
                })}
              </Card>
            )}
          </>
        )}
      </div>
    </>
  );
}
