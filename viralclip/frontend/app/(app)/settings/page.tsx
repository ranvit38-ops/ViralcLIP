"use client";

import { Save } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtBytes, type SystemInfo } from "@/lib/api";
import { LANGUAGES, LENGTH_PRESETS, presetFor } from "@/components/project-panels";
import { Badge, Button, ErrorNote, Field, Input, PageHeader, Progress, Section, Select } from "@/components/ui";

type S = {
  defaults: { min_len: number; max_len: number; count: number; language: string };
  whisper_model: string | null;
  metadata: { audience: string; category: string; tone: string; title_style: string; description_length: string; hashtag_count: number; language: string };
  render: { aspect: string; layout: string; preset: string; position: string; captions: boolean };
};

const EMPTY: S = {
  defaults: { min_len: 30, max_len: 60, count: 8, language: "" },
  whisper_model: null,
  metadata: { audience: "", category: "", tone: "", title_style: "", description_length: "short", hashtag_count: 5, language: "" },
  render: { aspect: "9:16", layout: "auto", preset: "bold", position: "lower", captions: true },
};

function merge(raw: Record<string, any>): S {
  return {
    defaults: { ...EMPTY.defaults, ...raw.defaults },
    whisper_model: raw.whisper_model ?? null,
    metadata: { ...EMPTY.metadata, ...raw.metadata },
    render: { ...EMPTY.render, ...raw.render },
  };
}

export default function SettingsPage() {
  const [s, setS] = useState<S | null>(null);
  const [orig, setOrig] = useState<S | null>(null);
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Record<string, any>>("/api/settings").then((r) => { const m = merge(r); setS(m); setOrig(m); }).catch((e) => setError(e.message));
    api<SystemInfo>("/api/system").then(setSys).catch(() => {});
  }, []);

  if (!s || !orig) return error ? <ErrorNote>{error}</ErrorNote> : <p className="text-sm text-muted">Loading…</p>;
  const dirty = JSON.stringify(s) !== JSON.stringify(orig);
  const upd = <K extends keyof S>(k: K, patch: Partial<S[K]> | S[K]) => {
    setS({ ...s, [k]: typeof patch === "object" && patch !== null ? { ...(s[k] as object), ...patch } : patch });
    setStatus("");
  };
  const preset = presetFor(s.defaults.min_len, s.defaults.max_len);

  async function save() {
    setError("");
    if (!(s!.defaults.min_len >= 5 && s!.defaults.max_len <= 600 && s!.defaults.min_len < s!.defaults.max_len))
      return setError("Default clip length must be 5–600 seconds with minimum below maximum.");
    setBusy(true);
    try {
      const m = merge(await api("/api/settings", { method: "PUT", json: { settings: s } }));
      setS(m); setOrig(m); setStatus("Settings saved.");
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }

  const text = (k: keyof S["metadata"], label: string, placeholder: string) => (
    <Field label={label}>
      <Input value={s.metadata[k] as string} placeholder={placeholder} maxLength={120} onChange={(e) => upd("metadata", { [k]: e.target.value })} />
    </Field>
  );

  return (
    <>
      <PageHeader title="Settings" sub="Defaults for new clips and AI suggestions. Existing clips keep their own settings." />
      <div className="max-w-3xl space-y-4">
        <Section title="Clip defaults">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Clip length">
              <Select value={preset} onChange={(e) => { const p = LENGTH_PRESETS[e.target.value]; if (p) upd("defaults", { min_len: p[0], max_len: p[1] }); }}>
                {Object.keys(LENGTH_PRESETS).map((k) => <option key={k}>{k}</option>)}
                {preset === "custom" && <option value="custom">Custom ({s.defaults.min_len}–{s.defaults.max_len} s)</option>}
              </Select>
            </Field>
            <Field label="Number of clips">
              <Input type="number" min={1} max={50} value={s.defaults.count} onChange={(e) => upd("defaults", { count: Math.max(1, Math.min(50, +e.target.value || 1)) })} />
            </Field>
            <Field label="Language">
              <Select value={s.defaults.language} onChange={(e) => upd("defaults", { language: e.target.value })}>
                {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Aspect ratio">
              <Select value={s.render.aspect} onChange={(e) => upd("render", { aspect: e.target.value })}>
                <option value="9:16">9:16 vertical (Shorts)</option><option value="1:1">1:1 square</option><option value="4:5">4:5 portrait</option><option value="16:9">16:9 landscape</option>
              </Select>
            </Field>
            <Field label="Layout">
              <Select value={s.render.layout} onChange={(e) => upd("render", { layout: e.target.value })}>
                <option value="auto">Auto</option><option value="crop">Crop (fill frame)</option><option value="fit">Fit (blurred background)</option>
              </Select>
            </Field>
          </div>
        </Section>

        <Section title="Captions">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Burn in captions">
              <Select value={s.render.captions ? "on" : "off"} onChange={(e) => upd("render", { captions: e.target.value === "on" })}>
                <option value="on">On</option><option value="off">Off</option>
              </Select>
            </Field>
            <Field label="Style preset">
              <Select value={s.render.preset} onChange={(e) => upd("render", { preset: e.target.value })}>
                {Object.keys(sys?.caption_presets ?? { bold: 1, karaoke: 1, clean: 1, minimal: 1 }).map((p) => <option key={p} value={p} className="capitalize">{p}</option>)}
              </Select>
            </Field>
            <Field label="Position">
              <Select value={s.render.position} onChange={(e) => upd("render", { position: e.target.value })}>
                <option value="top">Top</option><option value="middle">Middle</option><option value="lower">Lower third</option><option value="bottom">Bottom</option>
              </Select>
            </Field>
          </div>
          <p className="mt-3 text-xs text-muted">Fonts, colors and sizes can be fine-tuned per clip in the clip editor.</p>
        </Section>

        <Section title="AI">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Transcription model" hint={`Runs on: ${sys?.whisper_device ?? "auto"} (set WHISPER_DEVICE on the server). Models download on first use.`}>
              <Select value={s.whisper_model ?? ""} onChange={(e) => upd("whisper_model", e.target.value || null)}>
                <option value="">Server default ({sys?.whisper_default ?? "small"})</option>
                {(sys?.whisper_models ?? []).map((m) => <option key={m} value={m}>{m}{sys?.whisper_downloaded.includes(m) ? " · downloaded" : ""}</option>)}
              </Select>
            </Field>
            <div className="space-y-1.5">
              <span className="text-xs font-medium text-muted">Language model (clip ranking, titles, hashtags)</span>
              {sys?.llm_configured ? (
                <p className="text-sm"><Badge tone="green">connected</Badge> <span className="break-all text-muted">{sys.llm_model}</span></p>
              ) : (
                <div className="space-y-1 text-sm">
                  <p><Badge tone="amber">not configured</Badge> <span className="text-muted">using basic built-in suggestions</span></p>
                  <p className="text-xs text-muted">For AI titles and ranking, run a local model (e.g. <code>ollama run qwen2.5:7b-instruct</code>) and set <code>LLM_BASE_URL=http://localhost:11434/v1</code> and <code>LLM_MODEL=qwen2.5:7b-instruct</code> on the server.</p>
                </div>
              )}
            </div>
          </div>
          <h3 className="mb-3 mt-5 text-xs font-semibold uppercase tracking-wide text-muted">Title, description & hashtag preferences</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            {text("audience", "Target audience", "e.g. gamers, new investors")}
            {text("category", "Content category", "e.g. gaming, finance, comedy")}
            {text("tone", "Tone", "e.g. casual, hype, professional")}
            <Field label="Title style">
              <Select value={s.metadata.title_style} onChange={(e) => upd("metadata", { title_style: e.target.value })}>
                <option value="">Mixed (recommended)</option>
                <option value="curiosity-driven but accurate">Curiosity</option>
                <option value="educational">Educational</option>
                <option value="question-based">Question</option>
                <option value="direct and descriptive">Direct & descriptive</option>
              </Select>
            </Field>
            <Field label="Description length">
              <Select value={s.metadata.description_length} onChange={(e) => upd("metadata", { description_length: e.target.value })}>
                <option value="short">Short (1–2 sentences)</option><option value="long">Longer (3–5 sentences)</option>
              </Select>
            </Field>
            <Field label="Hashtags per clip">
              <Input type="number" min={1} max={8} value={s.metadata.hashtag_count} onChange={(e) => upd("metadata", { hashtag_count: Math.max(1, Math.min(8, +e.target.value || 1)) })} />
            </Field>
            {text("language", "Metadata language", "same as the video")}
          </div>
        </Section>

        <Section title="Storage">
          {sys ? (
            <div className="space-y-2 text-sm">
              <Progress value={1 - sys.disk_free / sys.disk_total} />
              <p className="text-muted">{fmtBytes(sys.disk_free)} free of {fmtBytes(sys.disk_total)} · ViralClip is using {fmtBytes(sys.storage_used)} for videos and clips.</p>
              <p className="text-xs text-muted">The storage folder is set with VC_DATA_DIR on the server.</p>
            </div>
          ) : <p className="text-sm text-muted">Loading…</p>}
        </Section>

        <Section title="Privacy & data">
          <p className="text-sm text-muted">Your videos never leave this server unless you upload them somewhere. Delete a project (and all its clips and files) from its page in <Link href="/projects" className="text-accent hover:underline">My Videos</Link>.</p>
        </Section>

        <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-line bg-bg/95 py-3 backdrop-blur">
          <Button variant="primary" onClick={save} disabled={!dirty || busy}><Save size={15} /> {busy ? "Saving…" : "Save settings"}</Button>
          {dirty && <Button variant="ghost" onClick={() => { setS(orig); setError(""); }}>Discard</Button>}
          <span className="text-sm text-muted">{dirty ? "Unsaved changes" : status || "All settings saved"}</span>
          <ErrorNote>{error}</ErrorNote>
        </div>
      </div>
    </>
  );
}
