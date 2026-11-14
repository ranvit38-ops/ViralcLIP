"use client";

import { Archive, ArchiveRestore, Clapperboard, Pencil, Trash2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ACTIVE_JOB, api, fmtBytes, fmtDate, fmtDuration, type Clip, type Project } from "@/lib/api";
import { useUpload } from "@/lib/upload";
import { BulkBar, ClipReviewRow, RENDER_ACTIVE } from "@/components/clips";
import { GeneratePanel, JobsList, TranscriptPanel, type Transcript } from "@/components/project-panels";
import { Button, Card, Empty, ErrorNote, Input, PageHeader, Progress, Section, StatusBadge } from "@/components/ui";

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm tabular-nums">{value ?? "—"}</dd>
    </div>
  );
}

function ResumeUpload({ p, onDone }: { p: Project; onDone: () => void }) {
  const up = useUpload();
  const [err, setErr] = useState("");
  async function pick(f?: File) {
    setErr("");
    if (!f) return;
    if (f.name !== p.source_filename || f.size !== p.upload_size)
      return setErr(`Select the original file: ${p.source_filename} (${fmtBytes(p.upload_size)}).`);
    if (await up.start(p.id, f)) onDone();
  }
  const sent = up.sent || p.bytes_received;
  return (
    <Card className="space-y-3 p-4">
      <p className="text-sm">This upload is incomplete ({fmtBytes(sent)} of {fmtBytes(p.upload_size)}). Select the same file to resume where it stopped.</p>
      <Progress value={sent / (p.upload_size || 1)} />
      <input type="file" accept="video/*" disabled={up.busy} onChange={(e) => pick(e.target.files?.[0])} className="text-sm text-muted file:mr-3 file:rounded-md file:border-0 file:bg-panel-2 file:px-3 file:py-1.5 file:text-fg" />
      <ErrorNote>{err || up.error}</ErrorNote>
    </Card>
  );
}

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const video = useRef<HTMLVideoElement>(null);
  const [p, setP] = useState<Project | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showRejected, setShowRejected] = useState(false);
  const [missing, setMissing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => Promise.all([
    api<Project>(`/api/projects/${id}`),
    api<Clip[]>(`/api/clips?project_id=${id}`),
  ]).then(([proj, cl]) => { setP(proj); setClips(cl); })
    .catch((e) => { if (e.status === 404) setMissing(true); }), [id]);
  useEffect(() => { load(); }, [load]);

  // Load the transcript once an analyze job has finished (a failed one may still have transcribed).
  const analyzeKey = p?.jobs?.filter((j) => j.job_type === "analyze" && ["done", "failed"].includes(j.status)).map((j) => `${j.id}${j.status}`).join(",") ?? "";
  useEffect(() => {
    if (!analyzeKey) return;
    api<Transcript>(`/api/projects/${id}/transcript`).then(setTranscript).catch(() => setTranscript(null));
  }, [id, analyzeKey]);

  const active = !!p?.jobs?.some((j) => ACTIVE_JOB.includes(j.status)) || clips.some((c) => RENDER_ACTIVE.includes(c.render_status));
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [active, load]);

  if (missing) return <p className="text-sm text-muted">Project not found.</p>;
  if (!p) return <p className="text-sm text-muted">Loading…</p>;

  async function patch(body: object) {
    setError("");
    try { setP({ ...p!, ...(await api<Project>(`/api/projects/${p!.id}`, { method: "PATCH", json: body })) }); }
    catch (e: any) { setError(e.message); }
  }
  async function remove() {
    if (!confirm(`Delete "${p!.name}"? This removes the source video and every generated clip. It can't be undone.`)) return;
    await api(`/api/projects/${p!.id}`, { method: "DELETE" });
    router.push("/projects");
  }
  function seek(t: number) {
    if (!video.current) return;
    video.current.currentTime = t;
    video.current.play().catch(() => {});
    video.current.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const shown = clips.filter((c) => showRejected || c.review_status !== "rejected");
  const ids = shown.filter((c) => selected.has(c.id)).map((c) => c.id);
  const rejected = clips.length - clips.filter((c) => c.review_status !== "rejected").length;
  const analyzing = p.jobs?.some((j) => j.job_type === "analyze" && ACTIVE_JOB.includes(j.status));
  const playable = p.status !== "uploading" && p.status !== "failed";

  return (
    <>
      <PageHeader
        title={p.name}
        sub={<span className="inline-flex flex-wrap items-center gap-2"><StatusBadge status={p.status} /> {p.archived && <StatusBadge status="archived" />} Created {fmtDate(p.created_at)}</span>}
        actions={<>
          <Button variant="ghost" onClick={() => { setName(p.name); setEditing(true); }}><Pencil size={16} /> Rename</Button>
          <Button variant="ghost" onClick={() => patch({ archived: !p.archived })}>
            {p.archived ? <><ArchiveRestore size={16} /> Unarchive</> : <><Archive size={16} /> Archive</>}
          </Button>
          <Button variant="danger" onClick={remove}><Trash2 size={16} /> Delete</Button>
        </>}
      />
      {editing && (
        <form className="mb-4 flex max-w-md gap-2" onSubmit={(e) => { e.preventDefault(); patch({ name }); setEditing(false); }}>
          <Input autoFocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          <Button variant="primary" disabled={!name.trim()}>Save</Button>
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
        </form>
      )}
      <div className="space-y-4">
        <ErrorNote>{error || (p.has_audio === false ? null : p.error)}</ErrorNote>
        {p.status === "uploading" && <ResumeUpload p={p} onDone={load} />}
        {playable && (
          <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
            <video ref={video} src={`/api/projects/${p.id}/source`} poster={p.thumbnail_url ?? undefined} controls preload="metadata" className="aspect-video w-full rounded-lg border border-line bg-black" />
            <Card className="p-4">
              <dl className="grid grid-cols-2 gap-4">
                <Meta label="Duration" value={fmtDuration(p.duration)} />
                <Meta label="Resolution" value={p.width && `${p.width}×${p.height}`} />
                <Meta label="Frame rate" value={p.fps && `${p.fps} fps`} />
                <Meta label="File size" value={fmtBytes(p.upload_size)} />
                <Meta label="Video codec" value={p.video_codec} />
                <Meta label="Audio" value={p.has_audio ? p.audio_codec : "none"} />
              </dl>
              <p className="mt-4 truncate text-xs text-muted" title={p.source_filename ?? ""}>{p.source_filename}</p>
            </Card>
          </div>
        )}

        {(p.status === "uploaded" || p.status === "ready") && !analyzing && (
          <GeneratePanel project={p} hasTranscript={!!transcript} onStarted={load} />
        )}
        <JobsList jobs={p.jobs ?? []} onChange={load} />

        {playable && (
          <Section
            title={`Clips${clips.length ? ` (${shown.length})` : ""}`}
            actions={rejected > 0 && (
              <label className="flex items-center gap-2 text-xs text-muted">
                <input type="checkbox" checked={showRejected} onChange={(e) => setShowRejected(e.target.checked)} className="accent-[var(--color-accent)]" />
                Show {rejected} rejected
              </label>
            )}
          >
            {shown.length === 0 ? (
              <Empty icon={<Clapperboard size={22} />} title={analyzing ? "Finding clips…" : "No clips yet"}>
                {analyzing ? "Clips appear here when the job above finishes." : "Use Generate clips above to find the best moments."}
              </Empty>
            ) : (
              <>
                <BulkBar
                  ids={ids}
                  all={ids.length === shown.length}
                  onSelectAll={(v) => setSelected(v ? new Set(shown.map((c) => c.id)) : new Set())}
                  onDone={load}
                  projectId={p.id}
                />
                <div className="-mx-4 mt-3 divide-y divide-line border-t border-line">
                  {shown.map((c) => (
                    <ClipReviewRow
                      key={c.id}
                      clip={c}
                      selected={selected.has(c.id)}
                      onSelect={(v) => setSelected((s) => { const n = new Set(s); if (v) n.add(c.id); else n.delete(c.id); return n; })}
                      onChange={load}
                    />
                  ))}
                </div>
              </>
            )}
          </Section>
        )}

        {transcript && <TranscriptPanel transcript={transcript} onSeek={seek} />}
      </div>
    </>
  );
}
