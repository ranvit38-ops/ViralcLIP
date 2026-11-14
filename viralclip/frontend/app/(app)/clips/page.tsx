"use client";

import { CalendarClock, Clapperboard } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtTime, type Clip, type Project } from "@/lib/api";
import { BulkBar, RENDER_ACTIVE } from "@/components/clips";
import { ScheduleDialog } from "@/components/schedule";
import { Button, Card, Empty, Input, PageHeader, Select, StatusBadge, Loading } from "@/components/ui";

function ClipsGrid() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [q, setQ] = useState(params.get("q") ?? "");
  const [scheduling, setScheduling] = useState<number[] | null>(null);

  const filters = { project_id: params.get("project_id") ?? "", review_status: params.get("review_status") ?? "", render_status: params.get("render_status") ?? "" };
  const query = params.toString();

  const setParam = useCallback((key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value); else next.delete(key);
    router.replace(`${pathname}${next.size ? `?${next}` : ""}`, { scroll: false });
  }, [params, pathname, router]);

  const load = useCallback(() => api<Clip[]>(`/api/clips${query ? `?${query}` : ""}`).then(setClips).catch(() => setClips([])), [query]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api<Project[]>("/api/projects").then(setProjects).catch(() => {}); }, []);

  // Debounce search typing into the URL.
  useEffect(() => {
    if (q === (params.get("q") ?? "")) return;
    const t = setTimeout(() => setParam("q", q.trim()), 300);
    return () => clearTimeout(t);
  }, [q, params, setParam]);

  const rendering = clips?.some((c) => RENDER_ACTIVE.includes(c.render_status));
  useEffect(() => {
    if (!rendering) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [rendering, load]);

  const ids = (clips ?? []).filter((c) => selected.has(c.id)).map((c) => c.id);
  const any = Object.values(filters).some(Boolean) || params.get("q");

  return (
    <>
      <PageHeader title="Generated Clips" sub="Every clip across your projects." />
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Input placeholder="Search titles and transcripts" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search clips" />
        <Select aria-label="Project" value={filters.project_id} onChange={(e) => setParam("project_id", e.target.value)}>
          <option value="">All projects</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
        <Select aria-label="Review status" value={filters.review_status} onChange={(e) => setParam("review_status", e.target.value)}>
          <option value="">Any review status</option>
          <option value="suggested">Awaiting review</option><option value="approved">Approved</option><option value="rejected">Rejected</option>
        </Select>
        <Select aria-label="Render status" value={filters.render_status} onChange={(e) => setParam("render_status", e.target.value)}>
          <option value="">Any render status</option>
          <option value="none">Not rendered</option><option value="queued">Queued</option><option value="rendering">Rendering</option>
          <option value="done">Rendered</option><option value="outdated">Outdated</option><option value="failed">Failed</option>
        </Select>
      </div>
      {!clips ? <Loading /> : clips.length === 0 ? (
        <Empty icon={<Clapperboard size={22} />} title={any ? "No clips match these filters" : "No clips yet"}>
          {any ? <button className="text-accent hover:underline" onClick={() => { setQ(""); router.replace(pathname); }}>Clear filters</button> :
            "Open a project and use Generate clips to find the best moments."}
        </Empty>
      ) : (
        <>
          {(() => {
            const ready = clips.filter((c) => selected.has(c.id) && c.review_status === "approved" && c.render_status === "done").map((c) => c.id);
            return (
              <BulkBar ids={ids} all={ids.length === clips.length} onSelectAll={(v) => setSelected(v ? new Set(clips.map((c) => c.id)) : new Set())} onDone={load}
                extra={ids.length > 0 && (
                  <Button onClick={() => setScheduling(ready)} disabled={ready.length === 0}
                    title={ready.length ? undefined : "Only approved, rendered clips can be published"}>
                    <CalendarClock size={16} /> Publish to YouTube{ready.length ? ` (${ready.length})` : ""}
                  </Button>
                )} />
            );
          })()}
          {scheduling && <ScheduleDialog clipIds={scheduling} onClose={() => setScheduling(null)} onDone={() => { setScheduling(null); load(); }} />}
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {clips.map((c) => (
              <Card key={c.id} className={`relative overflow-hidden transition hover:border-zinc-600 ${selected.has(c.id) ? "border-accent" : ""}`}>
                <input type="checkbox" aria-label={`Select ${c.title}`} checked={selected.has(c.id)}
                  onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(c.id); else n.delete(c.id); return n; })}
                  className="absolute left-2 top-2 z-10 size-4 accent-[var(--color-accent)]" />
                <Link href={`/clips/${c.id}`} className="block">
                  <div className="relative aspect-video bg-black">
                    <img src={c.thumbnail_url} alt="" className="size-full object-cover" />
                    <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[11px] tabular-nums">{fmtTime(c.duration)}</span>
                  </div>
                  <div className="space-y-1.5 p-3">
                    <p className="line-clamp-2 text-sm font-medium">{c.title || "Untitled clip"}</p>
                    <p className="truncate text-xs text-muted">{c.project_name} · {fmtDate(c.created_at)}</p>
                    <div className="flex flex-wrap gap-1.5"><StatusBadge status={c.review_status} /><StatusBadge status={c.render_status === "none" ? "not rendered" : c.render_status} /></div>
                  </div>
                </Link>
              </Card>
            ))}
          </div>
        </>
      )}
    </>
  );
}

export default function ClipsPage() {
  return <Suspense fallback={<Loading />}><ClipsGrid /></Suspense>;
}
