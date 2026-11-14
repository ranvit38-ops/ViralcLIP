"use client";

import { Archive, ArchiveRestore, Pencil, Trash2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, fmtBytes, fmtDate, fmtDuration, type Project } from "@/lib/api";
import { useUpload } from "@/lib/upload";
import { Button, Card, ErrorNote, Input, PageHeader, Progress, StatusBadge } from "@/components/ui";

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
  const [p, setP] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => api<Project>(`/api/projects/${id}`).then(setP).catch((e) => e.status === 404 && setMissing(true)), [id]);
  useEffect(() => { load(); }, [load]);
  const active = p?.jobs?.some((j) => ["queued", "running", "cancel_requested"].includes(j.status));
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 3000);
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

  return (
    <>
      <PageHeader
        title={p.name}
        sub={<span className="inline-flex items-center gap-2"><StatusBadge status={p.status} /> {p.archived && <StatusBadge status="archived" />} Created {fmtDate(p.created_at)}</span>}
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
        <ErrorNote>{error || p.error}</ErrorNote>
        {p.status === "uploading" && <ResumeUpload p={p} onDone={load} />}
        {p.status !== "uploading" && p.status !== "failed" && (
          <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
            <video src={`/api/projects/${p.id}/source`} poster={p.thumbnail_url ?? undefined} controls preload="metadata" className="aspect-video w-full rounded-lg border border-line bg-black" />
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
      </div>
    </>
  );
}
