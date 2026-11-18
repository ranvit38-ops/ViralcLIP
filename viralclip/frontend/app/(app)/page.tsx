"use client";

import { AlertTriangle, Clapperboard, Film, Inbox, Loader2, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate, type Job, type Project } from "@/lib/api";
import { Card, Empty, LinkButton, Loading, PageHeader, Progress, StatusBadge } from "@/components/ui";
import { JOB_LABEL } from "@/components/project-panels";
import { YouTubeStats } from "@/components/youtube-stats";
import { TikTokStats } from "@/components/tiktok";
import { SetupBanner } from "@/components/setup";
import { LinkImport } from "@/components/link-import";
import { WeekStatus } from "@/components/week-status";

type Dash = {
  projects: number; clips: number; clips_awaiting_review: number; active_jobs: number; failed_jobs: number;
  recent_jobs: Job[]; recent_projects: Project[];
};

function Stat({ label, value, icon, href }: { label: string; value: number; icon: React.ReactNode; href?: string }) {
  const card = (
    <Card className={`h-full p-4 ${href ? "transition hover:border-zinc-600" : ""}`}>
      <div className="flex items-center justify-between text-xs text-muted">{label}{icon}</div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </Card>
  );
  return href ? <Link href={href}>{card}</Link> : card;
}

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  useEffect(() => {
    api<Project[]>("/api/projects").then((ps) => setNames(Object.fromEntries(ps.map((p) => [p.id, p.name])))).catch(() => {});
    const load = () => api<Dash>("/api/dashboard").then(setD).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  if (!d) return <Loading />;
  return (
    <>
      <PageHeader title="Dashboard" actions={<LinkButton href="/projects/new" variant="primary"><Plus size={16} /> New project</LinkButton>} />
      <SetupBanner />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5 [&>*:last-child]:col-span-2 lg:[&>*:last-child]:col-span-1">
        <Stat label="Projects" value={d.projects} icon={<Film size={14} />} href="/projects" />
        <Stat label="Clips generated" value={d.clips} icon={<Clapperboard size={14} />} href="/clips" />
        <Stat label="Awaiting review" value={d.clips_awaiting_review} icon={<Inbox size={14} />} href="/clips?review_status=suggested" />
        <Stat label="Processing jobs" value={d.active_jobs} icon={<Loader2 size={14} />} />
        <Stat label="Failed jobs" value={d.failed_jobs} icon={<AlertTriangle size={14} />} />
      </div>
      <WeekStatus />
      <Card className="mt-3 p-4">
        <h2 className="mb-2 text-sm font-medium">Quick import from a link</h2>
        <LinkImport />
      </Card>
      <YouTubeStats />
      <TikTokStats />

      <div className="mt-8 grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
        <section>
          <h2 className="mb-3 text-sm font-medium text-muted">Recent projects</h2>
          {d.recent_projects.length === 0 ? (
            <Empty icon={<Film size={22} />} title="No projects yet">Upload a long video to get started.</Empty>
          ) : (
            <Card className="divide-y divide-line">
              {d.recent_projects.map((p) => (
                <Link key={p.id} href={`/projects/${p.id}`} className="flex items-center gap-3 p-3 hover:bg-panel-2">
                  <div className="grid aspect-video w-20 shrink-0 place-items-center overflow-hidden rounded bg-panel-2 text-muted">
                    {p.thumbnail_url ? <img src={p.thumbnail_url} alt="" className="size-full object-cover" /> : <Film size={16} />}
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
                    <span className="min-w-0 truncate"><span className="font-medium">{JOB_LABEL[j.job_type] ?? j.job_type}</span>
                      <span className="text-muted"> · {names[j.project_id] ?? `Project #${j.project_id}`}</span></span>
                    <StatusBadge status={j.status} />
                  </div>
                  {j.status === "running" && <Progress value={j.progress} />}
                  <p className={`truncate text-xs ${j.error ? "text-red-300" : "text-muted"}`}>
                    {j.error ?? (j.status === "done" ? "Finished" : j.status === "cancelled" ? "Cancelled" : j.message)} · {fmtDate(j.finished_at ?? j.created_at)}
                  </p>
                </Link>
              ))}
            </Card>
          )}
        </section>
      </div>
    </>
  );
}
