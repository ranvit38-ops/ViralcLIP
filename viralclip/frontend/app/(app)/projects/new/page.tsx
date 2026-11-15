"use client";

import { Link2, UploadCloud } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, fmtBytes, type Project } from "@/lib/api";
import { useUpload } from "@/lib/upload";
import { Button, Card, ErrorNote, Field, Input, PageHeader, Progress } from "@/components/ui";
import { LinkImport } from "@/components/link-import";

const EXTS = [".mp4", ".mov", ".webm", ".m4v", ".mkv"];

export default function NewProject() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pid, setPid] = useState<number | null>(null);
  const up = useUpload();
  const [drag, setDrag] = useState(false);
  const [tab, setTab] = useState<"file" | "link">("file");

  function pick(f?: File | null) {
    setError("");
    if (!f) return;
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!EXTS.includes(ext)) return setError(`"${f.name}" isn't a supported video. Use MP4, MOV or WEBM.`);
    setFile(f);
    if (!name) setName(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 200));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || up.busy) return;
    setError("");
    try {
      const p = await api<Project>("/api/projects", { method: "POST", json: { name: name.trim() || file.name, filename: file.name, size: file.size } });
      setPid(p.id);
      const done = await up.start(p.id, file);
      if (done) router.push(`/projects/${p.id}`);
    } catch (err: any) { setError(err.message); }
  }

  async function cancel() {
    up.abort();
    if (pid) await api(`/api/projects/${pid}`, { method: "DELETE" }).catch(() => {});
    setPid(null);
  }

  return (
    <>
      <PageHeader title="Create project" sub="Upload a long-form video you own or have the rights to use, or import one from your connected channels." />
      <Card className="max-w-2xl p-5">
        <div role="tablist" aria-label="Video source" className="mb-5 grid grid-cols-2 gap-1 rounded-md border border-line bg-bg p-1 sm:inline-grid">
          {([["file", "Upload a file", UploadCloud], ["link", "Paste a link", Link2]] as const).map(([k, label, I]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)} disabled={up.busy}
              className={`inline-flex h-8 items-center justify-center gap-2 rounded px-3 text-sm font-medium transition disabled:opacity-50 ${tab === k ? "bg-panel-2 text-fg ring-1 ring-line" : "text-muted hover:text-fg"}`}>
              <I size={15} /> {label}
            </button>
          ))}
        </div>
        {tab === "link" ? (
          <div role="tabpanel" className="space-y-3">
            <p className="text-sm text-muted">
              Paste a link to a Twitch VOD, YouTube video or TikTok from one of <span className="text-fg">your connected channels</span>. Videos from other channels are refused.
            </p>
            <LinkImport autoFocus />
          </div>
        ) : (
        <form role="tabpanel" onSubmit={submit} className="space-y-5">
          <label
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
            className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center transition ${drag ? "border-accent bg-accent/5" : "border-line hover:border-zinc-600"}`}>
            <UploadCloud className="text-muted" />
            {file ? (
              <span className="text-sm"><span className="font-medium">{file.name}</span> <span className="text-muted">· {fmtBytes(file.size)}</span></span>
            ) : (
              <span className="text-sm text-muted">Drop a video here or <span className="text-fg underline">browse</span><br />MP4, MOV, WEBM</span>
            )}
            <input type="file" accept={EXTS.join(",") + ",video/*"} className="sr-only" disabled={up.busy} onChange={(e) => pick(e.target.files?.[0])} />
          </label>
          <Field label="Project name">
            <Input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Episode 42 — interview" disabled={up.busy} />
          </Field>
          {(up.busy || up.sent > 0) && file && (
            <div className="space-y-1.5">
              <Progress value={up.sent / file.size} />
              <p className="text-xs text-muted tabular-nums">
                {fmtBytes(up.sent)} of {fmtBytes(file.size)} · {Math.floor((up.sent / file.size) * 100)}%
                {up.sent === file.size && up.busy && " · checking video…"}
              </p>
            </div>
          )}
          <ErrorNote>{error || up.error}</ErrorNote>
          {up.error && pid && <p className="text-xs text-muted">Progress is saved. You can resume from the project page by selecting the same file.</p>}
          <div className="flex gap-2">
            <Button variant="primary" disabled={!file || up.busy}>{up.busy ? "Uploading…" : "Upload"}</Button>
            {up.busy && <Button type="button" variant="ghost" onClick={cancel}>Cancel upload</Button>}
          </div>
        </form>
        )}
      </Card>
    </>
  );
}
