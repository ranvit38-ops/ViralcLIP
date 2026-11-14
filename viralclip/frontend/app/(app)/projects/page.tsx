"use client";

import { Archive, Film, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate, fmtDuration, type Project } from "@/lib/api";
import { Button, Card, Empty, Input, LinkButton, PageHeader, StatusBadge } from "@/components/ui";

export default function ProjectsPage() {
  const [archived, setArchived] = useState(false);
  const [items, setItems] = useState<Project[] | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { api<Project[]>(`/api/projects?archived=${archived}`).then(setItems).catch(() => setItems([])); }, [archived]);

  const shown = items?.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <PageHeader title="My Videos" sub="Long-form source videos and their projects."
        actions={<LinkButton href="/projects/new" variant="primary"><Plus size={16} /> New project</LinkButton>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <Input placeholder="Search projects" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <Button variant={archived ? "secondary" : "ghost"} onClick={() => { setItems(null); setArchived(!archived); }}>
          <Archive size={16} /> {archived ? "Showing archived" : "Show archived"}
        </Button>
      </div>
      {!shown ? <p className="text-sm text-muted">Loading…</p> : shown.length === 0 ? (
        <Empty icon={<Film size={22} />} title={archived ? "No archived projects" : q ? "No matches" : "No projects yet"} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`}>
              <Card className="overflow-hidden transition hover:border-zinc-600">
                <div className="aspect-video bg-panel-2">
                  {p.thumbnail_url && <img src={p.thumbnail_url} alt="" className="size-full object-cover" />}
                </div>
                <div className="space-y-1 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-medium">{p.name}</p>
                    <StatusBadge status={p.status} />
                  </div>
                  <p className="text-xs text-muted">
                    {fmtDuration(p.duration)} · {p.clip_count ?? 0} clips · {fmtDate(p.created_at)}
                  </p>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
