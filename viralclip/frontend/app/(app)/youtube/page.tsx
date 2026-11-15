"use client";

import { Link2, MonitorPlay, Plus, RefreshCw, Unplug } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { browserTz, type YtAccount, type YtConfig } from "@/lib/youtube";
import { LinkImport } from "@/components/link-import";
import { TimeZoneSelect } from "@/components/schedule";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, PageHeader, Loading } from "@/components/ui";

const connectCls = "inline-flex items-center justify-center gap-2 rounded-md px-3 h-9 text-sm font-medium transition";

function RuleForm({ acc, onSaved }: { acc: YtAccount; onSaved: () => void }) {
  const r = acc.rule;
  const [enabled, setEnabled] = useState(r?.enabled ?? false);
  const [time, setTime] = useState(r?.time_of_day ?? "18:00");
  const [tz, setTz] = useState(r?.timezone ?? browserTz());
  const [perDay, setPerDay] = useState(r?.clips_per_day ?? 1);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault(); setError(""); setMsg("");
    try {
      await api(`/api/youtube/accounts/${acc.id}/rule`, { method: "PUT", json: { enabled, time_of_day: time, timezone: tz, clips_per_day: perDay } });
      setMsg(enabled ? "Auto-publish is on." : "Auto-publish is off.");
      onSaved();
    } catch (err: any) { setError(err.message); }
  }

  return (
    <form onSubmit={save} className="space-y-3 border-t border-line pt-3">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-[var(--color-accent)]" />
        Auto-publish approved clips to this channel
      </label>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="First slot each day"><Input type="time" value={time} onChange={(e) => setTime(e.target.value)} required /></Field>
        <Field label="Time zone"><TimeZoneSelect value={tz} onChange={setTz} /></Field>
        <Field label="Clips per day">
          <Input type="number" min={1} max={6} value={perDay} onChange={(e) => setPerDay(Math.max(1, Math.min(6, Number(e.target.value) || 1)))} />
        </Field>
      </div>
      <p className="text-xs text-muted">
        Only clips you&apos;ve approved and rendered get scheduled, each into the next free slot. With more than one per day, slots are spread evenly across 24 hours from the first slot. New clips are never published without your approval.
      </p>
      <ErrorNote>{error}</ErrorNote>
      <div className="flex items-center gap-3">
        <Button variant="secondary">Save rule</Button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
    </form>
  );
}

function ImportForm() {
  return (
    <Card className="p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-medium"><Link2 size={16} /> Import from one of your videos</h2>
      <p className="mb-3 text-xs text-muted">
        The original file from your computer is better quality than YouTube&apos;s re-encoded copy, so upload that when you have it.
        Downloads use yt-dlp, which breaks for a while whenever YouTube changes things.
      </p>
      <LinkImport placeholder="https://www.youtube.com/watch?v=…" />
    </Card>
  );
}

function Studio() {
  const params = useSearchParams();
  const [cfg, setCfg] = useState<YtConfig | null>(null);
  const [accounts, setAccounts] = useState<YtAccount[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<YtConfig>("/api/youtube/config").then(setCfg).catch((e) => setError(e.message));
    api<YtAccount[]>("/api/youtube/accounts").then(setAccounts).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function disconnect(a: YtAccount) {
    if (!confirm(`Disconnect ${a.channel_title}? ViralClip's access is revoked at Google. Pending uploads to it are cancelled; videos already on YouTube stay there.`)) return;
    try { await api(`/api/youtube/accounts/${a.id}`, { method: "DELETE" }); load(); }
    catch (e: any) { setError(e.message); }
  }

  const connected = params.get("connected");
  const connectButton = (
    <a href="/api/youtube/connect" className={`${connectCls} bg-accent text-accent-fg hover:brightness-110`}><Plus size={16} /> Connect a channel</a>
  );

  return (
    <>
      <PageHeader title="YouTube Studio" sub="Connect your channels, import your own videos, and set auto-publish rules." actions={cfg?.configured ? connectButton : undefined} />
      <div className="space-y-4">
        {connected && <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">Connected {connected} channel{connected === "1" ? "" : "s"}.</p>}
        <ErrorNote>{params.get("error") || error}</ErrorNote>

        {cfg && !cfg.configured && (
          <Card className="space-y-2 p-4 text-sm">
            <p className="font-medium">YouTube isn&apos;t configured on this server yet</p>
            <p className="text-muted">Create a Google Cloud OAuth web client with the YouTube Data API v3 enabled, then set these environment variables on the backend and restart it:</p>
            <ul className="list-inside list-disc font-mono text-xs">{cfg.missing.map((m) => <li key={m}>{m}</li>)}</ul>
            <p className="text-muted">Authorized redirect URI to register: <code className="font-mono text-xs text-fg">{cfg.redirect_uri}</code></p>
            <p className="text-muted">Step-by-step instructions are in <code className="font-mono text-xs text-fg">viralclip/docs/YOUTUBE_SETUP.md</code>.</p>
          </Card>
        )}

        {accounts && accounts.length === 0 && cfg?.configured && (
          <Empty icon={<MonitorPlay size={22} />} title="No channels connected">
            <p className="mb-3">Sign in with Google and pick the channel to upload to. Repeat for each channel you own.</p>
            {connectButton}
          </Empty>
        )}

        {accounts?.map((a) => (
          <Card key={a.id} className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-3">
              {a.thumbnail_url ? <img src={a.thumbnail_url} alt="" className="size-10 rounded-full" /> : <div className="size-10 rounded-full bg-panel-2" />}
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 font-medium">
                  <span className="truncate">{a.channel_title || a.channel_id}</span>
                  {a.status === "ok" ? <Badge tone="green">connected</Badge> : <Badge tone="red">reconnect required</Badge>}
                </p>
                <p className="truncate text-xs text-muted">{a.channel_id} · connected {fmtDate(a.created_at)} · {a.uploads_today}/{a.daily_limit} uploads today (Pacific day)</p>
              </div>
              <div className="flex gap-2">
                {a.status !== "ok" && <a href="/api/youtube/connect" className={`${connectCls} bg-panel-2 border border-line hover:bg-line`}><RefreshCw size={16} /> Reconnect</a>}
                <Button variant="danger" onClick={() => disconnect(a)}><Unplug size={16} /> Disconnect</Button>
              </div>
            </div>
            <RuleForm acc={a} onSaved={load} />
          </Card>
        ))}

        {accounts && accounts.length > 0 && (
          <p className="text-xs text-muted">To add another channel, click Connect again and choose that channel (or its Brand Account) on Google&apos;s screen.</p>
        )}
        {accounts && accounts.length > 0 && <ImportForm />}
      </div>
    </>
  );
}

export default function YouTubePage() {
  return <Suspense fallback={<Loading />}><Studio /></Suspense>;
}
