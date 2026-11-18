"use client";

import { Check, Download, Film, MessageSquare, Pencil, Play, RotateCcw, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, downloadPost, fmtTime, type Clip, type Job } from "@/lib/api";
import { Badge, Button, ErrorNote, Progress, StatusBadge } from "@/components/ui";

export const RENDER_ACTIVE = ["queued", "rendering"];

export const BREAKDOWN_LABELS: Record<string, string> = {
  hook: "Opening hook", standalone: "Makes sense alone", completeness: "Complete ending", value: "Useful / story content",
  pacing: "Pacing", energy: "Vocal energy", llm: "AI judgement", peak_loudness: "Peak loudness", sustained_energy: "Sustained energy",
};

export function isAi(c: Clip) {
  return String(c.score_breakdown?.method ?? "").includes("llm");
}

const SPIKE = "Twitch chat spiked";

/** Splits the clip's reason text into [other reasons, chat-spike sentences]. */
export function splitReason(reason: string): [string, string[]] {
  const rest: string[] = [], spikes: string[] = [];
  for (const part of (reason ?? "").split(new RegExp(`(?=${SPIKE})`))) {
    if (!part.startsWith(SPIKE)) { rest.push(part); continue; }
    // the spike reason runs to the end of its sentence; anything after is another reason
    const m = part.match(/^(.*?[.!?])\s+(\S[\s\S]*)$/);
    spikes.push((m ? m[1] : part).trim());
    if (m) rest.push(m[2]);
  }
  return [rest.join(" ").replace(/\s+/g, " ").trim(), spikes];
}

export function ScoreBreakdown({ clip }: { clip: Clip }) {
  const rows = Object.entries(clip.score_breakdown ?? {}).filter(([, v]) => typeof v === "number");
  const [reason, spikes] = splitReason(clip.reason);
  return (
    <div className="space-y-2">
    {spikes.map((s, i) => (
      <p key={i} className="flex items-start gap-2 rounded-md border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-xs text-violet-100">
        <MessageSquare size={14} className="mt-px shrink-0 text-violet-300" aria-hidden /> <span><span className="sr-only">Chat spike: </span>{s}</span>
      </p>
    ))}
    <details className="group text-xs">
      <summary className="cursor-pointer select-none text-muted hover:text-fg">
        Score <span className="font-semibold tabular-nums text-fg">{Math.round(clip.score)}</span>/100
        {isAi(clip) && <span className="ml-1.5"><Badge tone="blue">AI-ranked</Badge></span>}
        <span className="ml-1 group-open:hidden">· details</span>
      </summary>
      <div className="mt-2 space-y-1.5 rounded-md border border-line bg-bg p-3">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[1fr_80px_32px] items-center gap-2">
            <span className="text-muted">{BREAKDOWN_LABELS[k] ?? k}</span>
            <Progress value={v as number} />
            <span className="text-right tabular-nums">{Math.round((v as number) * 100)}</span>
          </div>
        ))}
        {reason && <p className="pt-1 text-muted">{reason}</p>}
        <p className="pt-1 text-[11px] text-muted/80">
          This is a heuristic ranking of how well the excerpt stands alone. It is not a prediction of views.
        </p>
      </div>
    </details>
    </div>
  );
}

/** Render state badge + live progress for a clip. */
export function RenderStatus({ clip }: { clip: Clip }) {
  const job: Job | null = clip.job;
  const running = job && ["queued", "running", "cancel_requested"].includes(job.status);
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex items-center gap-2 text-xs text-muted">
        Render <StatusBadge status={clip.render_status} />
        {clip.render_status === "outdated" && <span>settings changed since last render</span>}
      </div>
      {running && job.job_type === "render" && (
        <div className="space-y-1">
          <Progress value={job.progress} />
          <p className="text-[11px] text-muted tabular-nums">{job.message} · {Math.round(job.progress * 100)}%</p>
        </div>
      )}
      {clip.render_status === "failed" && job?.error && <p className="text-xs text-red-300">{job.error}</p>}
    </div>
  );
}

export function renderLabel(c: Clip) {
  return c.render_status === "done" ? "Re-render" : c.render_status === "outdated" ? "Render again" : "Render";
}

/** One clip in the project review list. */
export function ClipReviewRow({ clip, selected, onSelect, onChange }: {
  clip: Clip; selected: boolean; onSelect: (v: boolean) => void; onChange: () => void;
}) {
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(false);
  const busy = RENDER_ACTIVE.includes(clip.render_status);

  async function run(fn: () => Promise<unknown>) {
    setError("");
    try { await fn(); onChange(); } catch (e: any) { setError(e.message); }
  }
  const review = (review_status: string) => run(() => api(`/api/clips/${clip.id}`, { method: "PATCH", json: { review_status } }));
  const src = clip.video_url ?? `/api/projects/${clip.project_id}/source#t=${clip.start},${clip.end}`;

  return (
    <div className={`grid gap-4 p-4 sm:grid-cols-[auto_200px_1fr] ${clip.review_status === "rejected" ? "opacity-60" : ""}`}>
      <input type="checkbox" aria-label={`Select ${clip.title}`} checked={selected} onChange={(e) => onSelect(e.target.checked)} className="mt-1 size-4 accent-[var(--color-accent)]" />
      <div className="relative aspect-video overflow-hidden rounded-md bg-black sm:w-[200px]">
        {preview ? (
          <video key={src} src={src} autoPlay controls className="size-full object-contain" />
        ) : (
          <button onClick={() => setPreview(true)} className="group relative size-full" aria-label={`Preview ${clip.title}`}>
            <img src={clip.thumbnail_url} alt="" className="size-full object-cover" />
            <span className="absolute inset-0 grid place-items-center bg-black/30 opacity-90 group-hover:bg-black/10">
              <Play size={26} className="fill-white text-white" />
            </span>
            <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[11px] tabular-nums">{fmtTime(clip.duration)}</span>
          </button>
        )}
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-medium">{clip.title || "Untitled clip"}</p>
            <p className="text-xs text-muted tabular-nums">{fmtTime(clip.start)} – {fmtTime(clip.end)} · {clip.duration.toFixed(1)}s</p>
          </div>
          <StatusBadge status={clip.review_status} />
        </div>
        {clip.transcript_excerpt && <p className="line-clamp-3 text-sm text-muted">“{clip.transcript_excerpt}”</p>}
        <ScoreBreakdown clip={clip} />
        <RenderStatus clip={clip} />
        <ErrorNote>{error}</ErrorNote>
        <div className="flex flex-wrap gap-2 pt-1">
          {clip.review_status !== "approved" && <Button onClick={() => review("approved")}><Check size={15} /> Approve</Button>}
          {clip.review_status !== "rejected" && <Button variant="ghost" onClick={() => review("rejected")}><X size={15} /> Reject</Button>}
          {clip.review_status !== "suggested" && <Button variant="ghost" onClick={() => review("suggested")}><RotateCcw size={15} /> Undo</Button>}
          <Button disabled={busy} onClick={() => run(() => api(`/api/clips/${clip.id}/render`, { method: "POST" }))}><Film size={15} /> {busy ? "Rendering…" : renderLabel(clip)}</Button>
          {clip.video_url && <a href={`/api/clips/${clip.id}/video?download=true`} className="inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm text-muted hover:bg-panel-2 hover:text-fg"><Download size={15} /> Download</a>}
          <Link href={`/clips/${clip.id}`} className="inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm text-muted hover:bg-panel-2 hover:text-fg"><Pencil size={15} /> Edit</Link>
        </div>
      </div>
    </div>
  );
}

/** Actions for a set of selected clips. `extra` is where other features (e.g. YouTube scheduling) mount their own button. */
export function BulkBar({ ids, all, onSelectAll, onDone, projectId, extra }: {
  ids: number[]; all: boolean; onSelectAll: (v: boolean) => void; onDone: () => void; projectId?: number; extra?: React.ReactNode;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  async function run(label: string, fn: () => Promise<string | void>) {
    setBusy(label); setError(""); setNote("");
    try { setNote((await fn()) ?? ""); onDone(); } catch (e: any) { setError(e.message); } finally { setBusy(""); }
  }
  const none = ids.length === 0;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="mr-1 inline-flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={all} onChange={(e) => onSelectAll(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
          {ids.length ? `${ids.length} selected` : "Select all"}
        </label>
        <Button disabled={none || !!busy} onClick={() => run("approve", async () => {
          for (const id of ids) await api(`/api/clips/${id}`, { method: "PATCH", json: { review_status: "approved" } });
          return `Approved ${ids.length} clip${ids.length > 1 ? "s" : ""}.`;
        })}><Check size={15} /> Approve selected</Button>
        <Button disabled={none || !!busy} onClick={() => run("render", async () => {
          const r = await api<{ queued: number[] }>("/api/clips/render", { method: "POST", json: { clip_ids: ids } });
          return `Queued ${r.queued.length} render${r.queued.length === 1 ? "" : "s"}${r.queued.length < ids.length ? ` (${ids.length - r.queued.length} already rendering)` : ""}.`;
        })}><Film size={15} /> Render selected</Button>
        <Button disabled={none || !!busy} onClick={() => run("zip", async () => {
          await downloadPost("/api/clips/download", { clip_ids: ids }, "viralclip-clips.zip");
        })}><Download size={15} /> {busy === "zip" ? "Preparing…" : "Download zip"}</Button>
        {projectId && (
          <Button variant="ghost" disabled={!!busy} onClick={() => run("meta", async () => {
            await api(`/api/projects/${projectId}/metadata`, { method: "POST", json: { fields: ["titles", "description", "hashtags"] } });
            return "Regenerating titles, descriptions and hashtags for every clip. Titles you edited are kept.";
          })}><Sparkles size={15} /> Regenerate all metadata</Button>
        )}
        {extra}
      </div>
      <ErrorNote>{error}</ErrorNote>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  );
}
