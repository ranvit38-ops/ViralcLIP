"use client";

import { AlertTriangle, Clapperboard, Film, Inbox, Loader2, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate, type Job, type Project } from "@/lib/api";
import { Card, Empty, LinkButton, PageHeader, Progress, StatusBadge } from "@/components/ui";
import { YouTubeStats } from "@/components/youtube-stats";

type Dash = {
  projects: number; clips: number; clips_awaiting_review: number; active_jobs: number; failed_jobs: number;
  recent_jobs: Job[]; recent_projects: Project[];
};

function Stat({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs text-muted">{label}{icon}</div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </Card>
  );
}

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null);
  useEffect(() => {
    const load = () => api<Dash>("/api/dashboard").then(setD).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  if (!d) return <p className="text-sm text-muted">Loading…</p>;
  return (
    <>
      <PageHeader title="Dashboard" actions={<LinkButton href="/projects/new" variant="primary"><Plus size={16} /> New project</LinkButton>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Projects" value={d.projects} icon={<Film size={14} />} />
        <Stat label="Clips generated" value={d.clips} icon={<Clapperboard size={14} />} />
        <Stat label="Awaiting review" value={d.clips_awaiting_review} icon={<Inbox size={14} />} />
        <Stat label="Processing jobs" value={d.active_jobs} icon={<Loader2 size={14} />} />
        <Stat label="Failed jobs" value={d.failed_jobs} icon={<AlertTriangle size={14} />} />
      </div>
      <YouTubeStats />

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-sm font-medium text-muted">Recent projects</h2>
          {d.recent_projects.length === 0 ? (
            <Empty icon={<Film size={22} />} title="No projects yet">Upload a long video to get started.</Empty>
          ) : (
            <Card className="divide-y divide-line">
              {d.recent_projects.map((p) => (
                <Link key={p.id} href={`/projects/${p.id}`} className="flex items-center gap-3 p-3 hover:bg-panel-2">
                  <div className="aspect-video w-20 shrink-0 overflow-hidden rounded bg-panel-2">
                    {p.thumbnail_url && <img src={p.thumbnail_url} alt="" className="size-full object-cover" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{p.name}</p>
                    <p className="text-xs text-muted">{fmtDate(p.created_at)}</p>
                  </div>
                  <StatusBadge status={p.status} />
                </Link>
              ))}
            </Card>
          )}
        </section>
        <section>
          <h2 className="mb-3 text-sm font-medium text-muted">Recent activity</h2>
          {d.recent_jobs.length === 0 ? (
            <Empty icon={<Loader2 size={22} />} title="No processing jobs yet" />
          ) : (
            <Card className="divide-y divide-line">
              {d.recent_jobs.map((j) => (
                <Link key={j.id} href={`/projects/${j.project_id}`} className="block space-y-2 p-3 hover:bg-panel-2">
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <span className="capitalize">{j.job_type} · project #{j.project_id}</span>
                    <StatusBadge status={j.status} />
                  </div>
                  {j.status === "running" && <Progress value={j.progress} />}
                  <p className="truncate text-xs text-muted">{j.error ?? j.message ?? ""} · {fmtDate(j.created_at)}</p>
                </Link>
              ))}
            </Card>
          )}
        </section>
      </div>
    </>
  );
}
