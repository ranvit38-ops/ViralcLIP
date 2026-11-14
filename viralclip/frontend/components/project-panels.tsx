"use client";

import { Ban, RotateCcw, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ACTIVE_JOB, api, fmtDate, fmtTime, type Job, type Project, type SystemInfo } from "@/lib/api";
import { Button, ErrorNote, Field, Input, Progress, Section, Select, StatusBadge, Loading } from "@/components/ui";

export const LENGTH_PRESETS: Record<string, [number, number]> = {
  "15–30 s": [15, 30], "30–60 s": [30, 60], "60–90 s": [60, 90], "90–180 s": [90, 180],
};

export function presetFor(min: number, max: number) {
  return Object.entries(LENGTH_PRESETS).find(([, [a, b]]) => a === min && b === max)?.[0] ?? "custom";
}

export const LANGUAGES: [string, string][] = [
  ["", "Auto-detect"], ["en", "English"], ["es", "Spanish"], ["pt", "Portuguese"], ["fr", "French"], ["de", "German"],
  ["it", "Italian"], ["nl", "Dutch"], ["hi", "Hindi"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"],
  ["ru", "Russian"], ["ar", "Arabic"], ["tr", "Turkish"], ["pl", "Polish"],
];

const MODEL_HELP: Record<string, string> = {
  tiny: "fastest, least accurate", base: "fast", small: "good balance (recommended on CPU)",
  medium: "accurate, slow on CPU", "large-v3": "most accurate, needs a GPU for long videos",
};

export function GeneratePanel({ project, hasTranscript, onStarted }: { project: Project; hasTranscript: boolean; onStarted: () => void }) {
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [preset, setPreset] = useState("30–60 s");
  const [min, setMin] = useState(30);
  const [max, setMax] = useState(60);
  const [count, setCount] = useState(8);
  const [language, setLanguage] = useState("");
  const [model, setModel] = useState("");
  const [retranscribe, setRetranscribe] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false); // don't show (or submit) hardcoded values before saved defaults load

  useEffect(() => {
    api<SystemInfo>("/api/system").then(setSys).catch(() => {});
    api<Record<string, any>>("/api/settings").then((s) => {
      const d = s.defaults ?? {};
      if (d.min_len && d.max_len) { setMin(d.min_len); setMax(d.max_len); setPreset(presetFor(d.min_len, d.max_len)); }
      if (d.count) setCount(d.count);
      if (d.language) setLanguage(d.language);
      if (s.whisper_model) setModel(s.whisper_model);
    }).catch(() => {}).finally(() => setReady(true));
  }, []);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!(min >= 5 && max <= 600 && min < max)) return setError("Clip length must be 5–600 seconds, with minimum below maximum.");
    setBusy(true);
    try {
      await api(`/api/projects/${project.id}/analyze`, {
        method: "POST",
        json: { min_len: min, max_len: max, count, language: language || null, model: model || null, retranscribe },
      });
      onStarted();
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  }

  const activeModel = model || sys?.whisper_default || "small";
  return (
    <Section title={hasTranscript ? "Generate clips again" : "Generate clips"}>
      {!ready ? <Loading /> : project.has_audio === false ? (
        <ErrorNote>This video has no audio track, so it can&apos;t be transcribed or analyzed. Upload a version with sound.</ErrorNote>
      ) : (
        <form onSubmit={start} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Clip length">
              <Select value={preset} onChange={(e) => {
                setPreset(e.target.value);
                const p = LENGTH_PRESETS[e.target.value];
                if (p) { setMin(p[0]); setMax(p[1]); }
              }}>
                {Object.keys(LENGTH_PRESETS).map((k) => <option key={k}>{k}</option>)}
                <option value="custom">Custom</option>
              </Select>
            </Field>
            {preset === "custom" && (
              <div className="grid grid-cols-2 gap-2">
                <Field label="Min (s)"><Input type="number" min={5} max={599} value={min} onChange={(e) => setMin(+e.target.value)} /></Field>
                <Field label="Max (s)"><Input type="number" min={6} max={600} value={max} onChange={(e) => setMax(+e.target.value)} /></Field>
              </div>
            )}
            <Field label="Number of clips">
              <Input type="number" min={1} max={50} value={count} onChange={(e) => setCount(Math.max(1, Math.min(50, +e.target.value || 1)))} />
            </Field>
            <Field label="Language">
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Transcription model" hint={sys && !sys.whisper_downloaded.includes(activeModel) ? "Not downloaded yet: it downloads on first use." : MODEL_HELP[activeModel]}>
              <Select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Default ({sys?.whisper_default ?? "small"})</option>
                {(sys?.whisper_models ?? []).map((m) => (
                  <option key={m} value={m}>{m}{sys?.whisper_downloaded.includes(m) ? " · downloaded" : ""}</option>
                ))}
              </Select>
            </Field>
          </div>
          {project.duration != null && project.duration < min * 3 && (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              This video is only {Math.round(project.duration)} s long, so {min}–{max} s clips leave room for just one or two.
              {min > 15 && (
                <button type="button" className="font-medium text-amber-100 underline" onClick={() => { setPreset("15–30 s"); setMin(15); setMax(30); }}>
                  Use 15–30 s clips
                </button>
              )}
            </p>
          )}
          {hasTranscript && (
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={retranscribe} onChange={(e) => setRetranscribe(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
              Transcribe again (otherwise the existing transcript is reused, which is much faster)
            </label>
          )}
          {hasTranscript && <p className="text-xs text-muted">Approved, rendered or edited clips are kept. Untouched suggestions are replaced.</p>}
          <ErrorNote>{error}</ErrorNote>
          <Button variant="primary" disabled={busy}><Wand2 size={16} /> {busy ? "Starting…" : "Find clips"}</Button>
        </form>
      )}
    </Section>
  );
}

export const JOB_LABEL: Record<string, string> = {
  analyze: "Find clips", render: "Render clip", metadata: "Regenerate metadata",
  youtube_import: "Import from YouTube", twitch_import: "Import from Twitch", youtube_upload: "Upload to YouTube",
};

export function JobsList({ jobs, onChange }: { jobs: Job[]; onChange: () => void }) {
  const [error, setError] = useState("");
  if (!jobs.length) return null;
  async function act(path: string) {
    setError("");
    try { await api(path, { method: "POST" }); onChange(); } catch (e: any) { setError(e.message); }
  }
  return (
    <Section title="Processing">
      <ErrorNote>{error}</ErrorNote>
      <ul className="divide-y divide-line">
        {jobs.slice(0, 8).map((j) => {
          const active = ACTIVE_JOB.includes(j.status);
          return (
            <li key={j.id} className="space-y-2 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm">
                  <span>{JOB_LABEL[j.job_type] ?? j.job_type}</span>
                  <StatusBadge status={j.status} />
                  <span className="text-xs text-muted">{fmtDate(j.created_at)}</span>
                </div>
                <div className="flex gap-2">
                  {(j.status === "queued" || j.status === "running") && (
                    <Button variant="ghost" onClick={() => act(`/api/jobs/${j.id}/cancel`)}><Ban size={15} /> Cancel</Button>
                  )}
                  {(j.status === "failed" || j.status === "cancelled") && ["analyze", "render", "metadata"].includes(j.job_type) && (
                    <Button variant="ghost" onClick={() => act(`/api/jobs/${j.id}/retry`)}><RotateCcw size={15} /> Retry</Button>
                  )}
                </div>
              </div>
              {active && (
                <>
                  <Progress value={j.progress} />
                  <p className="text-xs text-muted tabular-nums">
                    {j.status !== "queued" ? j.message : jobs.some((x) => x.status === "running")
                      ? "Queued: starts when the jobs ahead of it finish"
                      : "Waiting for a worker. If it doesn't start, check that the worker process is running."} · {Math.round(j.progress * 100)}%
                  </p>
                </>
              )}
              {j.error && <p className="text-xs text-red-300">{j.error}</p>}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export type Transcript = { language: string; model: string; text: string; segments: { start: number; end: number; text: string }[] };

export function TranscriptPanel({ transcript, onSeek }: { transcript: Transcript; onSeek: (t: number) => void }) {
  const [q, setQ] = useState("");
  const segs = transcript.segments.filter((s) => s.text.toLowerCase().includes(q.toLowerCase()));
  return (
    <Section title="Transcript" actions={<span className="text-xs text-muted">{transcript.language} · {transcript.model} model · {transcript.segments.length} segments</span>}>
      <Input placeholder="Search transcript" value={q} onChange={(e) => setQ(e.target.value)} className="mb-3" />
      <div className="max-h-96 space-y-0.5 overflow-y-auto pr-1">
        {segs.map((s, i) => (
          <button key={i} onClick={() => onSeek(s.start)} className="grid w-full grid-cols-[52px_1fr] gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-panel-2">
            <span className="pt-px text-xs tabular-nums text-accent">{fmtTime(s.start)}</span>
            <span>{s.text}</span>
          </button>
        ))}
        {!segs.length && <p className="px-2 text-sm text-muted">No matches.</p>}
      </div>
    </Section>
  );
}
