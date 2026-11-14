"use client";

import { AlertTriangle, Music2, Plus, RefreshCw, Unplug } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { browserTz } from "@/lib/youtube";
import { FALLBACK_PRIVACY, privacyLabel, type TtAccount, type TtConfig, type TtCreatorInfo, type TtMode } from "@/lib/tiktok";
import { selectCls, TimeZoneSelect } from "@/components/schedule";
import { NOT_AUDITED, SCHEDULE_WARNING } from "@/components/tiktok";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, PageHeader, Loading } from "@/components/ui";

const connectCls = "inline-flex items-center justify-center gap-2 rounded-md px-3 h-9 text-sm font-medium transition";

function RuleForm({ acc, audited, onSaved }: { acc: TtAccount; audited: boolean; onSaved: () => void }) {
  const r = acc.rule;
  const [enabled, setEnabled] = useState(r?.enabled ?? false);
  const [time, setTime] = useState(r?.time_of_day ?? "18:00");
  const [tz, setTz] = useState(r?.timezone ?? browserTz());
  const [perDay, setPerDay] = useState(r?.clips_per_day ?? 1);
  const [mode, setMode] = useState<TtMode>(r?.mode ?? (audited ? "direct" : "draft"));
  const [privacy, setPrivacy] = useState(r?.privacy_level ?? "");
  const [options, setOptions] = useState<string[]>(FALLBACK_PRIVACY);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (acc.status !== "ok") return;
    api<TtCreatorInfo>(`/api/tiktok/accounts/${acc.id}/creator_info`).then((i) => setOptions(i.privacy_level_options)).catch(() => {});
  }, [acc.id, acc.status]);

  async function save(e: React.FormEvent) {
    e.preventDefault(); setError(""); setMsg("");
    try {
      await api(`/api/tiktok/accounts/${acc.id}/rule`, {
        method: "PUT",
        json: { enabled, time_of_day: time, timezone: tz, clips_per_day: perDay, mode, privacy_level: mode === "direct" ? privacy : null },
      });
      setMsg(enabled ? "Auto-publish is on." : "Auto-publish is off.");
      onSaved();
    } catch (err: any) { setError(err.message); }
  }

  return (
    <form onSubmit={save} className="space-y-3 border-t border-line pt-3">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-[var(--color-accent)]" />
        Auto-publish approved clips to this account
      </label>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="First slot each day"><Input type="time" value={time} onChange={(e) => setTime(e.target.value)} required /></Field>
        <Field label="Time zone"><TimeZoneSelect value={tz} onChange={setTz} /></Field>
        <Field label="Clips per day">
          <Input type="number" min={1} max={15} value={perDay} onChange={(e) => setPerDay(Math.max(1, Math.min(15, Number(e.target.value) || 1)))} />
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="How to post">
          <select className={selectCls} value={mode} onChange={(e) => setMode(e.target.value as TtMode)}>
            <option value="draft">Draft to inbox (finish in the TikTok app)</option>
            <option value="direct">Post directly</option>
          </select>
        </Field>
        {mode === "direct" && (
          <Field label="Who can view">
            <select className={selectCls} value={privacy} onChange={(e) => setPrivacy(e.target.value)} required>
              <option value="" disabled>Choose who can view…</option>
              {options.map((p) => <option key={p} value={p}>{privacyLabel(p)}</option>)}
            </select>
          </Field>
        )}
      </div>
      <p className="text-xs text-muted">
        Only clips you&apos;ve approved and rendered get posted, each into the next free slot. With more than one per day, slots are spread evenly across 24 hours from the first slot. Posts made by the rule leave comments, duets and stitches off and add no content disclosure. {SCHEDULE_WARNING}
      </p>
      <ErrorNote>{error}</ErrorNote>
      <div className="flex items-center gap-3">
        <Button variant="secondary">Save rule</Button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
    </form>
  );
}

function TikTok() {
  const params = useSearchParams();
  const [cfg, setCfg] = useState<TtConfig | null>(null);
  const [accounts, setAccounts] = useState<TtAccount[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<TtConfig>("/api/tiktok/config").then((c) => {
      setCfg(c);
      if (c.configured) api<TtAccount[]>("/api/tiktok/accounts").then(setAccounts).catch((e) => setError(e.message));
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function disconnect(a: TtAccount) {
    if (!confirm(`Disconnect ${a.display_name}? ViralClip's access is revoked at TikTok. Pending posts to it are cancelled; videos already on TikTok stay there.`)) return;
    try { await api(`/api/tiktok/accounts/${a.id}`, { method: "DELETE" }); load(); }
    catch (e: any) { setError(e.message); }
  }

  const connectButton = (
    <a href="/api/tiktok/connect" className={`${connectCls} bg-accent text-accent-fg hover:brightness-110`}><Plus size={16} /> Connect an account</a>
  );

  return (
    <>
      <PageHeader title="TikTok" sub="Connect your TikTok accounts and set auto-publish rules." actions={cfg?.configured ? connectButton : undefined} />
      <div className="space-y-4">
        {params.get("connected") && <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">TikTok account connected.</p>}
        <ErrorNote>{params.get("error") || error}</ErrorNote>
        {!cfg && !error && <Loading />}

        {cfg && !cfg.configured && (
          <Card className="space-y-2 p-4 text-sm">
            <p className="font-medium">TikTok isn&apos;t configured on this server yet</p>
            <p className="text-muted">Create an app on TikTok for Developers with Login Kit and the Content Posting API, then set these environment variables on the backend and restart it:</p>
            <ul className="list-inside list-disc font-mono text-xs">{cfg.missing.map((m) => <li key={m}>{m}</li>)}</ul>
            <p className="text-muted">Redirect URI to register: <code className="break-all font-mono text-xs text-fg">{cfg.redirect_uri}</code></p>
            <p className="text-muted">Step-by-step instructions are in <code className="font-mono text-xs text-fg">viralclip/docs/TIKTOK_SETUP.md</code>.</p>
          </Card>
        )}

        {cfg?.configured && !cfg.audited && (
          <p className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200"><AlertTriangle size={16} className="mt-0.5 shrink-0" /> {NOT_AUDITED}</p>
        )}

        {cfg?.configured && accounts && accounts.length === 0 && (
          <Empty icon={<Music2 size={22} />} title="No TikTok accounts connected">
            <p className="mb-3">Sign in with TikTok to let ViralClip post clips for you. Repeat for each account you own.</p>
            {connectButton}
          </Empty>
        )}

        {cfg?.configured && accounts?.map((a) => (
          <Card key={a.id} className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-3">
              {a.avatar_url ? <img src={a.avatar_url} alt="" className="size-10 rounded-full" /> : <div className="size-10 rounded-full bg-panel-2" />}
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 font-medium">
                  <span className="truncate">{a.display_name || a.open_id}</span>
                  {a.status === "ok" ? <Badge tone="green">connected</Badge> : <Badge tone="red">reconnect required</Badge>}
                </p>
                <p className="truncate text-xs text-muted">{a.posts_today}/{a.daily_limit} posts today</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {a.status !== "ok" && <a href="/api/tiktok/connect" className={`${connectCls} border border-line bg-panel-2 hover:bg-line`}><RefreshCw size={16} /> Reconnect</a>}
                <Button variant="danger" onClick={() => disconnect(a)}><Unplug size={16} /> Disconnect</Button>
              </div>
            </div>
            <RuleForm acc={a} audited={cfg.audited} onSaved={load} />
          </Card>
        ))}

        {cfg?.configured && accounts && accounts.length > 0 && (
          <p className="text-xs text-muted">To add another account, sign out of TikTok in this browser (or use a private window), then click Connect again.</p>
        )}
      </div>
    </>
  );
}

export default function TikTokPage() {
  return <Suspense fallback={<Loading />}><TikTok /></Suspense>;
}
