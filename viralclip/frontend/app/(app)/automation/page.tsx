"use client";

import { ExternalLink, Play, Plus, Radio, RefreshCw, Trash2, Zap } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtDuration } from "@/lib/api";
import { LENGTH_PRESETS, presetFor } from "@/components/project-panels";
import { Badge, Button, Card, ErrorNote, Field, Input, PageHeader, Section, Select } from "@/components/ui";

type Settings = {
  enabled: boolean; twitch_account_ids: number[]; youtube_account_ids: number[]; min_source_minutes: number;
  analyze: { min_len: number; max_len: number; count: number; model: string | null };
  auto_approve: { enabled: boolean; top_n: number; min_score: number };
  auto_render: boolean; delete_source_after_days: number | null; enabled_at?: string | null;
};
type TwitchAccount = { id: number; login: string; display_name: string; profile_image_url: string | null; status: string };
type Vod = { id: string; title: string; created_at: string; duration_seconds: number; thumbnail_url: string | null; imported: boolean; project_id: number | null };
type State = {
  settings: Settings; twitch_configured: boolean; twitch_missing: string[]; twitch_accounts: TwitchAccount[];
  youtube_accounts: { id: number; channel_title: string; status: string; autopublish_enabled: boolean }[];
  waiting_review: number; events: { id: number; kind: string; message: string; project_id: number | null; created_at: string }[];
};

const linkCls = "inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition";
const toggle = (ids: number[], id: number, on: boolean) => (on ? [...new Set([...ids, id])] : ids.filter((x) => x !== id));

function Step({ n, title, children, actions }: { n: number; title: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <Section title={`${n} · ${title}`} actions={actions}>
      {children}
    </Section>
  );
}

function Vods({ acc, onImported }: { acc: TwitchAccount; onImported: () => void }) {
  const [vods, setVods] = useState<Vod[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api<Vod[]>(`/api/twitch/accounts/${acc.id}/videos`).then(setVods).catch((e) => setError(e.message)), [acc.id]);
  useEffect(() => { load(); }, [load]);
  async function imp(v: Vod) {
    setBusy(v.id); setError("");
    try { await api("/api/twitch/import", { method: "POST", json: { url: `https://www.twitch.tv/videos/${v.id}` } }); await load(); onImported(); }
    catch (e: any) { setError(e.message); } finally { setBusy(""); }
  }
  if (error && !vods) return <ErrorNote>{error}</ErrorNote>;
  if (!vods) return <p className="text-xs text-muted">Loading VODs…</p>;
  if (!vods.length) return <p className="text-xs text-muted">No past broadcasts found. Make sure “Store past broadcasts” is on in your Twitch settings.</p>;
  return (
    <div className="space-y-2">
      <ErrorNote>{error}</ErrorNote>
      {vods.map((v) => (
        <div key={v.id} className="flex items-center gap-3 rounded-md border border-line p-2">
          <div className="aspect-video w-24 shrink-0 overflow-hidden rounded bg-panel-2">{v.thumbnail_url && <img src={v.thumbnail_url} alt="" className="size-full object-cover" />}</div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{v.title || `VOD ${v.id}`}</p>
            <p className="text-xs text-muted">{fmtDuration(v.duration_seconds)} · {fmtDate(v.created_at)}</p>
          </div>
          {v.imported ? (
            v.project_id ? <Link href={`/projects/${v.project_id}`} className="text-xs text-accent hover:underline">Open project</Link> : <Badge>imported</Badge>
          ) : (
            <Button onClick={() => imp(v)} disabled={!!busy}>{busy === v.id ? "Starting…" : "Import"}</Button>
          )}
        </div>
      ))}
    </div>
  );
}

export default function AutomationPage() {
  return <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}><Automation /></Suspense>;
}

function Automation() {
  const params = useSearchParams();
  const [st, setSt] = useState<State | null>(null);
  const [s, setS] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [openVods, setOpenVods] = useState<number | null>(null);
  const [link, setLink] = useState("");

  const load = useCallback(() => api<State>("/api/automation").then((d) => { setSt(d); setS((cur) => cur ?? d.settings); }).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (!st || !s) return <p className="text-sm text-muted">{error || params.get("error") || "Loading…"}</p>;
  const dirty = JSON.stringify({ ...s, enabled_at: undefined }) !== JSON.stringify({ ...st.settings, enabled_at: undefined });
  const upd = (patch: Partial<Settings>) => { setS({ ...s, ...patch }); setSaved(""); };

  async function save(next = s!) {
    setBusy(true); setError("");
    try { const r = await api<Settings>("/api/automation", { method: "PUT", json: next }); setS(r); await load(); setSaved("Saved"); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }
  async function runNow() {
    setBusy(true); setError("");
    try { const d = await api<State>("/api/automation/run", { method: "POST" }); setSt(d); setNotice("Checked your sources just now."); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }
  async function disconnect(a: TwitchAccount) {
    if (!confirm(`Disconnect ${a.display_name}? Already imported videos stay.`)) return;
    try { await api(`/api/twitch/accounts/${a.id}`, { method: "DELETE" }); upd({ twitch_account_ids: s!.twitch_account_ids.filter((x) => x !== a.id) }); load(); }
    catch (e: any) { setError(e.message); }
  }
  async function importLink(e: React.FormEvent) {
    e.preventDefault(); setError("");
    try { const p = await api<{ id: number }>("/api/twitch/import", { method: "POST", json: { url: link } }); setLink(""); setNotice(""); location.href = `/projects/${p.id}`; }
    catch (err: any) { setError(err.message); }
  }

  const preset = presetFor(s.analyze.min_len, s.analyze.max_len);
  const watching = s.twitch_account_ids.length + s.youtube_account_ids.length;
  const publishing = st.youtube_accounts.filter((a) => a.autopublish_enabled);

  return (
    <>
      <PageHeader title="Automation"
        sub="New streams and videos from your own channels are imported, clipped and published without you lifting a finger."
        actions={<>
          {st.settings.enabled && <Button onClick={runNow} disabled={busy}><RefreshCw size={16} /> Check now</Button>}
          <Button variant="primary" onClick={() => save()} disabled={busy || !dirty}>{busy ? "Saving…" : dirty ? "Save changes" : saved || "Saved"}</Button>
        </>} />

      <div className="space-y-4">
        <ErrorNote>{error || params.get("error")}</ErrorNote>
        {params.get("twitch_connected") && !notice && <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">Twitch account connected.</p>}
        {notice && <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">{notice}</p>}

        <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
          <label className="flex items-center gap-3">
            <input type="checkbox" checked={s.enabled} onChange={(e) => save({ ...s, enabled: e.target.checked })} disabled={busy} className="size-5 accent-[var(--color-accent)]" />
            <span>
              <span className="font-medium">Automation is {st.settings.enabled ? "on" : "off"}</span>
              <span className="block text-xs text-muted">
                {st.settings.enabled
                  ? `Watching ${watching} source${watching === 1 ? "" : "s"} every 30 minutes${st.settings.enabled_at ? ` · only videos from ${fmtDate(st.settings.enabled_at)} (minus a day) onward` : ""}.`
                  : "Turn on to watch your channels. Older videos are never imported automatically."}
              </span>
            </span>
          </label>
          {st.waiting_review > 0 && <Link href="/clips?review_status=suggested" className="text-sm text-accent hover:underline">{st.waiting_review} clip(s) waiting for your review →</Link>}
        </Card>
        {st.settings.enabled && watching === 0 && <p className="text-sm text-amber-200">Automation is on but no sources are selected below, so nothing will be imported.</p>}

        <Step n={1} title="Sources">
          <div className="space-y-5">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="flex items-center gap-2 text-sm font-medium"><Radio size={16} /> Twitch</h3>
                {st.twitch_configured && <a href="/api/twitch/connect" className={`${linkCls} bg-panel-2 border border-line hover:bg-line`}><Plus size={16} /> Connect Twitch</a>}
              </div>
              {!st.twitch_configured && (
                <p className="rounded-md border border-line bg-panel-2 px-3 py-2 text-sm text-muted">
                  Twitch isn&apos;t set up on this server yet. Create a free app at dev.twitch.tv and set {st.twitch_missing.join(", ")} (see docs/TWITCH_SETUP.md).
                </p>
              )}
              {st.twitch_accounts.map((a) => (
                <div key={a.id} className="rounded-md border border-line p-3">
                  <div className="flex flex-wrap items-center gap-3">
                    {a.profile_image_url ? <img src={a.profile_image_url} alt="" className="size-8 rounded-full" /> : <div className="size-8 rounded-full bg-panel-2" />}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{a.display_name}</p>
                      <p className="text-xs text-muted">twitch.tv/{a.login}</p>
                    </div>
                    {a.status === "ok" ? <Badge tone="green">connected</Badge> : <a href="/api/twitch/connect" className="text-xs text-accent hover:underline">Reconnect</a>}
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={s.twitch_account_ids.includes(a.id)} onChange={(e) => upd({ twitch_account_ids: toggle(s.twitch_account_ids, a.id, e.target.checked) })} className="size-4 accent-[var(--color-accent)]" />
                      Auto-import new VODs
                    </label>
                    <Button variant="ghost" onClick={() => setOpenVods(openVods === a.id ? null : a.id)}><Play size={15} /> {openVods === a.id ? "Hide VODs" : "VODs"}</Button>
                    <Button variant="ghost" aria-label={`Disconnect ${a.display_name}`} onClick={() => disconnect(a)}><Trash2 size={15} /></Button>
                  </div>
                  {openVods === a.id && <div className="mt-3"><Vods acc={a} onImported={load} /></div>}
                </div>
              ))}
              {st.twitch_configured && (
                <form onSubmit={importLink} className="flex flex-wrap gap-2">
                  <Input value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://www.twitch.tv/videos/123456789" className="max-w-md" aria-label="Twitch VOD link" />
                  <Button disabled={!link.trim()}>Import VOD</Button>
                </form>
              )}
              <p className="text-xs text-muted">
                Twitch deletes past broadcasts after 7 days (14–60 for Affiliates, Partners and Turbo). Turn on <span className="text-fg">Settings → Stream → Store past broadcasts</span> on Twitch, and keep ViralClip running so VODs are grabbed in time. Only VODs from accounts you connect can be imported.
              </p>
            </div>

            <div className="space-y-2 border-t border-line pt-4">
              <h3 className="text-sm font-medium">YouTube (your own long videos)</h3>
              {st.youtube_accounts.length === 0 ? (
                <p className="text-sm text-muted">No channels connected. <Link href="/youtube" className="text-accent hover:underline">Connect one in YouTube Studio</Link>.</p>
              ) : st.youtube_accounts.map((a) => (
                <label key={a.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={s.youtube_account_ids.includes(a.id)} onChange={(e) => upd({ youtube_account_ids: toggle(s.youtube_account_ids, a.id, e.target.checked) })} className="size-4 accent-[var(--color-accent)]" />
                  Auto-import new uploads from <span className="font-medium">{a.channel_title}</span>
                </label>
              ))}
            </div>

            <Field label="Skip videos shorter than (minutes)" hint="Keeps short uploads, and your own Shorts, from being re-clipped.">
              <Input type="number" min={5} max={1440} value={s.min_source_minutes} onChange={(e) => upd({ min_source_minutes: +e.target.value })} className="w-28" />
            </Field>
          </div>
        </Step>

        <Step n={2} title="Find clips">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Clip length">
              <Select value={preset} onChange={(e) => { const p = LENGTH_PRESETS[e.target.value]; if (p) upd({ analyze: { ...s.analyze, min_len: p[0], max_len: p[1] } }); }}>
                {Object.keys(LENGTH_PRESETS).map((k) => <option key={k}>{k}</option>)}
                {preset === "custom" && <option value="custom">Custom ({s.analyze.min_len}–{s.analyze.max_len} s)</option>}
              </Select>
            </Field>
            <Field label="Clips per video">
              <Input type="number" min={1} max={50} value={s.analyze.count} onChange={(e) => upd({ analyze: { ...s.analyze, count: +e.target.value } })} />
            </Field>
            <Field label="Transcription model" hint="Default comes from Settings.">
              <Select value={s.analyze.model ?? ""} onChange={(e) => upd({ analyze: { ...s.analyze, model: e.target.value || null } })}>
                <option value="">Default</option>{["tiny", "base", "small", "medium", "large-v3"].map((m) => <option key={m}>{m}</option>)}
              </Select>
            </Field>
          </div>
        </Step>

        <Step n={3} title="Approval">
          <div className="space-y-3">
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="approve" checked={!s.auto_approve.enabled} onChange={() => upd({ auto_approve: { ...s.auto_approve, enabled: false } })} className="mt-0.5 accent-[var(--color-accent)]" />
              <span><span className="font-medium">I review clips</span><span className="block text-xs text-muted">Clips wait in Generated Clips until you approve them. Approved clips are then rendered and published automatically.</span></span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="approve" checked={s.auto_approve.enabled} onChange={() => upd({ auto_approve: { ...s.auto_approve, enabled: true } })} className="mt-0.5 accent-[var(--color-accent)]" />
              <span><span className="font-medium">Auto-approve the best clips</span><span className="block text-xs text-muted">Fully hands-off.</span></span>
            </label>
            {s.auto_approve.enabled && (
              <div className="ml-6 space-y-3">
                <div className="flex flex-wrap gap-4">
                  <Field label="Top clips per video"><Input type="number" min={1} max={20} value={s.auto_approve.top_n} onChange={(e) => upd({ auto_approve: { ...s.auto_approve, top_n: +e.target.value } })} className="w-24" /></Field>
                  <Field label="Minimum score (0–100)"><Input type="number" min={0} max={100} value={s.auto_approve.min_score} onChange={(e) => upd({ auto_approve: { ...s.auto_approve, min_score: +e.target.value } })} className="w-24" /></Field>
                </div>
                <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                  Nobody checks these before they go public. AI titles can be wrong, and the score is a ranking, not a view prediction. YouTube can demonetize channels that look mass-produced. Skim your published clips now and then.
                </p>
              </div>
            )}
          </div>
        </Step>

        <Step n={4} title="Render">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={s.auto_render} onChange={(e) => upd({ auto_render: e.target.checked })} className="size-4 accent-[var(--color-accent)]" />
            Render approved clips automatically
          </label>
          <p className="mt-2 text-xs text-muted">Uses your defaults from <Link href="/settings" className="text-accent hover:underline">Settings</Link>: layout (auto picks streamer split-screen when it finds a facecam), captions, jump cuts, zoom and hook title.</p>
        </Step>

        <Step n={5} title="Publish">
          {publishing.length ? (
            <p className="text-sm">Auto-publish is on for {publishing.map((a) => a.channel_title).join(", ")}. Approved, rendered clips are scheduled into your daily slots.</p>
          ) : (
            <p className="text-sm text-amber-200">No auto-publish rule is on, so rendered clips will wait for you to schedule them.</p>
          )}
          <Link href="/youtube" className="mt-2 inline-flex items-center gap-1 text-sm text-accent hover:underline">Set up auto-publish rules in YouTube Studio <ExternalLink size={13} /></Link>
        </Step>

        <Step n={6} title="Storage">
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <input type="checkbox" checked={s.delete_source_after_days != null} onChange={(e) => upd({ delete_source_after_days: e.target.checked ? 7 : null })} className="size-4 accent-[var(--color-accent)]" />
            Delete the full stream/video after
            <Input type="number" min={1} max={365} disabled={s.delete_source_after_days == null} value={s.delete_source_after_days ?? 7}
              onChange={(e) => upd({ delete_source_after_days: +e.target.value })} className="w-20" aria-label="Days" />
            days, once its approved clips are rendered
          </label>
          <p className="mt-2 text-xs text-muted">Streams are big (often 5–10 GB). Rendered clips are always kept.</p>
        </Step>

        <Section title="Activity" actions={<Button variant="ghost" onClick={load}><RefreshCw size={15} /> Refresh</Button>}>
          {st.events.length === 0 ? <p className="text-sm text-muted">Nothing yet. Activity appears here as automation works.</p> : (
            <ul className="divide-y divide-line">
              {st.events.map((e) => (
                <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 text-sm">
                  <span className="w-36 shrink-0 text-xs text-muted">{fmtDate(e.created_at)}</span>
                  <Badge tone={e.kind === "error" ? "red" : e.kind === "approve" || e.kind === "import" ? "green" : "gray"}><Zap size={10} className="mr-1" />{e.kind}</Badge>
                  <span className="min-w-0 flex-1">{e.message}</span>
                  {e.project_id && <Link href={`/projects/${e.project_id}`} className="text-xs text-accent hover:underline">Open</Link>}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </>
  );
}
