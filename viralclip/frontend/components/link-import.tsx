"use client";

import { Check, Link2, MonitorPlay, Music2, Radio } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, type Project } from "@/lib/api";
import { Button, Input } from "@/components/ui";

type Platform = "twitch" | "youtube" | "tiktok";
type Detect = { platform: Platform | null; connected: boolean; video_id: string | null };

const P: Record<Platform, { label: string; icon: typeof Radio; page: string; connect: string; ok: string }> = {
  twitch: { label: "Twitch VOD", icon: Radio, page: "/automation", connect: "connect your account first", ok: "account connected" },
  youtube: { label: "YouTube", icon: MonitorPlay, page: "/youtube", connect: "connect your channel first", ok: "channel connected" },
  tiktok: { label: "TikTok", icon: Music2, page: "/tiktok", connect: "connect your account first", ok: "account connected" },
};

/** One link box for Twitch, YouTube and TikTok: live platform detection, then POST /api/import and open the project. */
export function LinkImport({ placeholder = "Paste a Twitch, YouTube or TikTok link", autoFocus }: { placeholder?: string; autoFocus?: boolean }) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [det, setDet] = useState<(Detect & { for: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ msg: string; href?: string; cta?: string } | null>(null);
  const q = url.trim();
  const d = det && det.for === q ? det : null;

  useEffect(() => {
    if (!q) return;
    let live = true;
    const t = setTimeout(() => {
      api<Detect>(`/api/import/detect?url=${encodeURIComponent(q)}`).then((r) => live && setDet({ ...r, for: q })).catch(() => {});
    }, 300);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q || busy) return;
    setBusy(true); setErr(null);
    try {
      const p = await api<Project>("/api/import", { method: "POST", json: { url: q } });
      router.push(`/projects/${p.id}`);
    } catch (x) {
      const status = x instanceof ApiError ? x.status : 0;
      const detail = x instanceof ApiError ? x.detail : null;
      const msg = x instanceof Error ? x.message : "Import failed";
      const pid = detail && typeof detail === "object" && "project_id" in detail ? (detail as { project_id: number }).project_id : null;
      if (pid) setErr({ msg: "Already imported", href: `/projects/${pid}`, cta: "Open project" });
      else if (status === 409 && /tiktok/i.test(msg)) setErr({ msg, href: "/tiktok", cta: "Go to TikTok" });
      else if (status === 409 && d?.platform) setErr({ msg, href: P[d.platform].page, cta: `Connect ${P[d.platform].label.split(" ")[0]}` });
      else setErr({ msg });
      setBusy(false);
    }
  }

  const meta = d?.platform ? P[d.platform] : null;
  const Icon = meta?.icon ?? Link2;
  return (
    <form onSubmit={submit} className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 basis-56">
          <Icon size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input className="pl-8" inputMode="url" value={url} onChange={(e) => { setUrl(e.target.value); setErr(null); }}
            placeholder={placeholder} aria-label="Video link" autoFocus={autoFocus} disabled={busy} />
        </div>
        <Button variant="primary" disabled={!q || busy || d?.platform === null}>{busy ? "Importing…" : "Import"}</Button>
      </div>
      <div aria-live="polite" className="min-h-4 text-xs">
        {err ? (
          <p role="alert" className="text-red-300">
            {err.msg}{err.href && <> · <Link href={err.href} className="font-medium text-accent hover:underline">{err.cta} →</Link></>}
          </p>
        ) : !q || !d ? (
          <p className="text-muted">Only videos from your connected Twitch, YouTube or TikTok accounts can be imported.</p>
        ) : !meta ? (
          <p className="text-amber-300">That doesn&apos;t look like a Twitch, YouTube or TikTok video link.</p>
        ) : d.connected ? (
          <p className="flex items-center gap-1 text-emerald-300"><meta.icon size={13} /> {meta.label} · {meta.ok} <Check size={13} /></p>
        ) : (
          <p className="flex items-center gap-1 text-amber-300"><meta.icon size={13} /> {meta.label} · <Link href={meta.page} className="hover:underline">{meta.connect} →</Link></p>
        )}
      </div>
    </form>
  );
}
