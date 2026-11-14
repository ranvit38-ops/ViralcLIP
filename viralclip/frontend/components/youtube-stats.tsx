"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtInZone, type YtUpload } from "@/lib/youtube";
import { Card } from "@/components/ui";

type Stats = { channels: number; queued: number; uploading: number; scheduled: number; published: number; failed: number; recent_published: YtUpload[] };

/** Dashboard row: real counts from the uploads table. Nothing here comes from estimates. */
export function YouTubeStats() {
  const [s, setS] = useState<Stats | null>(null);
  useEffect(() => {
    const load = () => api<Stats>("/api/youtube/stats").then(setS).catch(() => {});
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  if (!s) return null;
  if (!s.channels && !s.queued && !s.published) {
    return <p className="mt-3 text-xs text-muted"><Link href="/youtube" className="underline hover:text-fg">Connect a YouTube channel</Link> to upload and schedule clips.</p>;
  }
  const cells: [string, number, string][] = [
    ["Pending uploads", s.queued + s.uploading, "/queue"], ["Scheduled", s.scheduled, "/calendar"],
    ["Published", s.published, "/calendar"], ["Failed uploads", s.failed, "/queue"],
  ];
  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cells.map(([label, n, href]) => (
          <Link key={label} href={href}>
            <Card className="p-4 transition hover:border-zinc-600">
              <p className="text-xs text-muted">YouTube · {label}</p>
              <p className={`mt-2 text-2xl font-semibold tabular-nums ${label === "Failed uploads" && n ? "text-red-300" : ""}`}>{n}</p>
            </Card>
          </Link>
        ))}
      </div>
      {s.recent_published.length > 0 && (
        <Card className="divide-y divide-line">
          <p className="px-3 py-2 text-xs font-medium text-muted">Recently published</p>
          {s.recent_published.map((u) => (
            <a key={u.id} href={u.youtube_url ?? "#"} target="_blank" rel="noreferrer" className="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-panel-2">
              <span className="truncate">{u.clip_title}</span>
              <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted">{fmtInZone(u.publish_at ?? u.uploaded_at, u.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone)} <ExternalLink size={12} /></span>
            </a>
          ))}
        </Card>
      )}
    </div>
  );
}
