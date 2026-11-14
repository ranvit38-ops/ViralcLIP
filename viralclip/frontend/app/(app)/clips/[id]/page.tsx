"use client";

import { ArrowLeft, CalendarClock, Check, Download, Film, Plus, RotateCcw, Sparkles, Trash2, X } from "lucide-react";
import { ScheduleDialog } from "@/components/schedule";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  api, fmtTime, type CaptionLine, type CaptionStyle, type Clip, type Project, type RenderSettings, type SystemInfo,
} from "@/lib/api";
import { RENDER_ACTIVE, RenderStatus, ScoreBreakdown, renderLabel } from "@/components/clips";
import { Badge, Button, ErrorNote, Field, Input, Section, Select, SaveBar, StatusBadge, Textarea, Loading } from "@/components/ui";

type Draft = {
  start: number; end: number; title: string; description: string; hashtags: string[];
  rs: Omit<RenderSettings, "_rendered_layout" | "_rendered_duration" | "_cuts">;
};

const ASPECTS: Record<string, [number, number]> = { "9:16": [1080, 1920], "1:1": [1080, 1080], "4:5": [1080, 1350], "16:9": [1920, 1080] };
const LAYOUT_HELP: Record<string, string> = {
  auto: "Follows the speaker's face when one is clearly visible; otherwise shows the whole frame.",
  crop: "Fills the frame by cropping. Follows faces, or uses the position you set below.",
  fit: "Shows the whole frame on a blurred background. Best for gameplay and screen recordings.",
  stream: "Streamer split screen: your facecam on top, gameplay below. Auto picks this when it finds a facecam.",
};
const POSITION_TOP: Record<string, string> = { top: "12%", middle: "50%", lower: "76%", bottom: "92%" };

function draftFrom(c: Clip): Draft {
  const { _rendered_layout, _rendered_duration, _cuts, ...rs } = c.render_settings; // eslint-disable-line @typescript-eslint/no-unused-vars
  return { start: c.start, end: c.end, title: c.title, description: c.description, hashtags: [...c.hashtags], rs: structuredClone(rs) };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const r2 = (n: number) => Math.round(n * 100) / 100;

/** Source frame with the crop box and a CSS approximation of the burned-in captions. */
function FramePreview({ project, rs, sample }: { project: Project; rs: Draft["rs"]; sample: string }) {
  const [W, H] = ASPECTS[rs.aspect] ?? ASPECTS["9:16"];
  const sw = project.width ?? 16, sh = project.height ?? 9;
  const dst = W / H, src = sw / sh;
  const wfrac = src > dst ? (sh * dst) / sw : 1;
  const left = rs.crop_x == null ? (1 - wfrac) / 2 : Math.max(0, Math.min(1 - wfrac, rs.crop_x - wfrac / 2));
  const st = rs.style;
  const previewW = 220;
  const scale = previewW / W;
  const words = (sample || "Your captions look like this").split(/\s+/).slice(0, st.words);
  const hlIndex = Math.min(1, words.length - 1);
  const stroke = st.box ? 0 : st.outline_width * scale * 2;

  return (
    <div className="flex flex-wrap items-start gap-4">
      <div>
        <p className="mb-1.5 text-xs text-muted">Source frame · crop area</p>
        <div className="relative w-64 overflow-hidden rounded-md bg-black" style={{ aspectRatio: `${sw}/${sh}` }}>
          {project.thumbnail_url && <img src={project.thumbnail_url} alt="" className="size-full object-cover" />}
          {rs.layout !== "fit" && wfrac < 1 && (
            <>
              <div className="absolute inset-y-0 left-0 bg-black/60" style={{ width: `${left * 100}%` }} />
              <div className="absolute inset-y-0 right-0 bg-black/60" style={{ width: `${(1 - left - wfrac) * 100}%` }} />
              <div className={`absolute inset-y-0 border-2 ${rs.crop_x == null ? "border-dashed border-white/70" : "border-accent"}`}
                style={{ left: `${left * 100}%`, width: `${wfrac * 100}%` }} />
            </>
          )}
        </div>
        <p className="mt-1.5 max-w-64 text-[11px] text-muted">
          {rs.layout === "stream" ? "Facecam (found at render time) on top; the gameplay area shown here goes below." :
            rs.layout === "fit" ? "Whole frame is shown, centered on a blurred copy." :
            rs.crop_x == null ? (rs.layout === "auto" ? "Face tracking picks the framing at render time (dashed = starting guess)." : "Face tracking picks the position at render time.") :
              "Fixed crop position."}
        </p>
      </div>
      <div>
        <p className="mb-1.5 text-xs text-muted">Output {rs.aspect} · captions preview</p>
        <div className="relative overflow-hidden rounded-md bg-black" style={{ width: previewW, aspectRatio: `${W}/${H}` }}>
          {project.thumbnail_url && (rs.layout === "fit" ? (
            <>
              <img src={project.thumbnail_url} alt="" className="absolute inset-0 size-full scale-110 object-cover blur-md brightness-75" />
              <img src={project.thumbnail_url} alt="" className="absolute inset-0 size-full object-contain" />
            </>
          ) : (
            <img src={project.thumbnail_url} alt="" className="absolute inset-0 size-full object-cover"
              style={{ objectPosition: `${wfrac < 1 ? (left / (1 - wfrac)) * 100 : 50}% 50%` }} />
          ))}
          {rs.captions && (
            <div className="absolute inset-x-2 -translate-y-1/2 text-center leading-tight" style={{ top: POSITION_TOP[rs.position] ?? "76%" }}>
              <span
                style={{
                  fontFamily: `"${st.font}", system-ui, sans-serif`, fontSize: st.size * scale, fontWeight: st.bold ? 800 : 500,
                  color: st.color, WebkitTextStroke: stroke ? `${stroke}px ${st.outline}` : undefined, paintOrder: "stroke fill",
                  textShadow: st.shadow ? `${st.shadow * scale * 2}px ${st.shadow * scale * 2}px 0 rgba(0,0,0,.6)` : undefined,
                  background: st.box ? "rgba(0,0,0,.62)" : undefined, padding: st.box ? "2px 6px" : undefined, borderRadius: st.box ? 3 : undefined,
                  textTransform: st.uppercase ? "uppercase" : "none", boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone",
                }}
              >
                {words.map((w, i) => (
                  <Fragment key={i}>
                    <span style={i === hlIndex ? { color: st.highlight, display: "inline-block", transform: st.pop ? "scale(1.12)" : undefined } : undefined}>{w}</span>
                    {i < words.length - 1 ? " " : ""}
                  </Fragment>
                ))}
              </span>
            </div>
          )}
        </div>
        <p className="mt-1.5 text-[11px] text-muted">Approximate. The rendered video is exact.</p>
      </div>
    </div>
  );
}

function Nudge({ label, value, onChange, min, max }: { label: string; value: number; onChange: (v: number) => void; min: number; max: number }) {
  const set = (v: number) => onChange(r2(Math.max(min, Math.min(max, v))));
  return (
    <Field label={label}>
      <div className="flex items-center gap-1">
        <Button type="button" variant="ghost" className="px-2" onClick={() => set(value - 1)} aria-label={`${label} −1s`}>−1</Button>
        <Button type="button" variant="ghost" className="px-2" onClick={() => set(value - 0.1)} aria-label={`${label} −0.1s`}>−.1</Button>
        <Input type="number" step={0.01} min={min} max={max} value={value} onChange={(e) => set(+e.target.value)} className="w-24 text-center tabular-nums" aria-label={`${label} seconds`} />
        <Button type="button" variant="ghost" className="px-2" onClick={() => set(value + 0.1)} aria-label={`${label} +0.1s`}>+.1</Button>
        <Button type="button" variant="ghost" className="px-2" onClick={() => set(value + 1)} aria-label={`${label} +1s`}>+1</Button>
      </div>
    </Field>
  );
}

function HashtagEditor({ tags, onChange }: { tags: string[]; onChange: (t: string[]) => void }) {
  const [text, setText] = useState("");
  function add(raw: string) {
    const fresh = raw.split(/[\s,]+/).map((t) => t.replace(/^#+/, "").replace(/[^\p{L}\p{N}_]/gu, "").slice(0, 40)).filter(Boolean);
    if (fresh.length) onChange([...new Set([...tags, ...fresh])]);
    setText("");
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {tags.map((t) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full border border-line bg-panel-2 py-0.5 pl-2.5 pr-1 text-sm">
            <button type="button" title="Edit" onClick={() => { onChange(tags.filter((x) => x !== t)); setText(t); }}>#{t}</button>
            <button type="button" aria-label={`Remove #${t}`} onClick={() => onChange(tags.filter((x) => x !== t))} className="rounded-full p-0.5 text-muted hover:bg-line hover:text-fg"><X size={13} /></button>
          </span>
        ))}
        {!tags.length && <span className="text-sm text-muted">No hashtags yet.</span>}
      </div>
      <div className="flex gap-2">
        <Input placeholder="Add hashtag (Enter)" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); } }} />
        <Button type="button" onClick={() => add(text)} disabled={!text.trim()}><Plus size={15} /> Add</Button>
      </div>
      <p className="text-xs text-muted">{tags.length} hashtag{tags.length === 1 ? "" : "s"}. 3–8 relevant tags work best; YouTube ignores all hashtags if there are more than 60.</p>
    </div>
  );
}

export default function ClipEditor() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const player = useRef<HTMLVideoElement>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [lines, setLines] = useState<CaptionLine[]>([]);
  const [linesOrig, setLinesOrig] = useState<CaptionLine[]>([]);
  const [linesEdited, setLinesEdited] = useState(false);
  const [view, setView] = useState<"rendered" | "source">("rendered");
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [publishing, setPublishing] = useState(false);

  const loadCaptions = useCallback(async () => {
    const c = await api<{ edited: boolean; lines: CaptionLine[] }>(`/api/clips/${id}/captions`);
    setLines(c.lines); setLinesOrig(c.lines); setLinesEdited(c.edited);
  }, [id]);

  useEffect(() => {
    (async () => {
      try {
        const c = await api<Clip>(`/api/clips/${id}`);
        setClip(c); setDraft(draftFrom(c));
        setProject(await api<Project>(`/api/projects/${c.project_id}`));
        await loadCaptions();
      } catch (e: any) { if (e.status === 404) setMissing(true); else setError(e.message); }
    })();
    api<SystemInfo>("/api/system").then(setSys).catch(() => {});
  }, [id, loadCaptions]);

  const rendering = !!clip && RENDER_ACTIVE.includes(clip.render_status);
  useEffect(() => {
    if (!rendering) return;
    const t = setInterval(async () => {
      try { setClip(await api<Clip>(`/api/clips/${id}`)); } catch { /* keep last state */ }
    }, 1500);
    return () => clearInterval(t);
  }, [rendering, id]);

  if (missing) return <p className="text-sm text-muted">Clip not found.</p>;
  if (!clip || !draft || !project) return error ? <ErrorNote>{error}</ErrorNote> : <Loading />;

  const orig = draftFrom(clip);
  const dirty = !same(draft, orig);
  const captionsDirty = !same(lines, linesOrig);
  const trimChanged = draft.start !== orig.start || draft.end !== orig.end;
  const duration = project.duration ?? draft.end;
  const len = draft.end - draft.start;
  const trimError = !(draft.start >= 0 && draft.end <= duration && len >= 1 && len <= 600) ? "Clip must be 1 s – 10 min long and inside the video." : "";
  // A trim resets caption edits server-side, so old line timings aren't validated against the new length.
  const lineError = captionsDirty && !trimChanged && lines.some((l) => !(l.end > l.start) || l.start < 0 || l.end > len + 0.5) ? "Each caption line needs an end after its start, within the clip." : "";

  const set = (patch: Partial<Draft>) => { setDraft({ ...draft, ...patch }); setSaved(""); };
  const setRs = (patch: Partial<Draft["rs"]>) => set({ rs: { ...draft.rs, ...patch } });
  const setStyle = (patch: Partial<CaptionStyle>) => setRs({ style: { ...draft.rs.style, ...patch } });

  async function save(): Promise<Clip | null> {
    setError("");
    if (trimError || lineError) { setError(trimError || lineError); return null; }
    const body: Record<string, unknown> = {};
    if (trimChanged) { body.start = draft!.start; body.end = draft!.end; }
    if (draft!.title !== orig.title) body.title = draft!.title;
    if (draft!.description !== orig.description) body.description = draft!.description;
    if (!same(draft!.hashtags, orig.hashtags)) body.hashtags = draft!.hashtags;
    if (!same(draft!.rs, orig.rs)) body.render_settings = draft!.rs;
    // New timings invalidate per-line timings, so the server resets caption edits on a trim.
    if (captionsDirty && !trimChanged) body.caption_overrides = lines;
    if (!Object.keys(body).length) return clip;
    try {
      const c = await api<Clip>(`/api/clips/${clip!.id}`, { method: "PATCH", json: body });
      setClip(c); setDraft(draftFrom(c));
      if (trimChanged || body.caption_overrides) await loadCaptions();
      setSaved(trimChanged && captionsDirty ? "Saved. Caption text edits were reset because the clip timing changed." : "All changes saved.");
      return c;
    } catch (e: any) { setError(e.message); return null; }
  }

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label); setError("");
    try { await fn(); } catch (e: any) { setError(e.message); } finally { setBusy(""); }
  }

  const render = () => act("render", async () => {
    if ((dirty || captionsDirty) && !(await save())) return;
    await api(`/api/clips/${clip.id}/render`, { method: "POST" });
    setClip(await api<Clip>(`/api/clips/${clip.id}`));
    setView("rendered");
  });

  const regen = (fields: string[]) => act(fields.join(), async () => {
    const c = await api<Clip>(`/api/clips/${clip.id}/metadata`, { method: "POST", json: { fields } });
    setClip(c);
    setDraft({
      ...draft,
      title: fields.includes("titles") ? c.title : draft.title,
      description: fields.includes("description") ? c.description : draft.description,
      hashtags: fields.includes("hashtags") ? c.hashtags : draft.hashtags,
    });
  });

  const review = (review_status: string) => act("review", async () => {
    const c = await api<Clip>(`/api/clips/${clip.id}`, { method: "PATCH", json: { review_status } });
    setClip({ ...clip, review_status: c.review_status });
  });

  const del = () => act("delete", async () => {
    if (!confirm("Delete this clip and its rendered file? This can't be undone.")) return;
    await api(`/api/clips/${clip.id}`, { method: "DELETE" });
    router.push(`/projects/${clip.project_id}`);
  });

  const hasRender = !!clip.video_url;
  const showRendered = hasRender && view === "rendered";
  const src = showRendered ? `${clip.video_url}?v=${clip.job?.finished_at ?? ""}` : `/api/projects/${clip.project_id}/source#t=${draft.start},${draft.end}`;
  const now = () => {
    const t = player.current?.currentTime ?? 0;
    return r2(showRendered ? clip.start + t : t);
  };
  const metaMethod = clip.score_breakdown?.metadata_method;
  const presets = sys?.caption_presets ?? {};

  return (
    <>
      <title>{`${clip.title || "Clip"} · ViralClip`}</title>
      <Link href={`/projects/${clip.project_id}`} className="inline-flex max-w-full items-center gap-1.5 text-sm text-muted hover:text-fg"><ArrowLeft size={15} className="shrink-0" /> <span className="truncate">{clip.project_name}</span></Link>
      <div className="mb-5 mt-2 flex flex-wrap items-center justify-between gap-3">
        <h1 className="min-w-0 max-w-2xl text-xl font-semibold tracking-tight">{clip.title || "Untitled clip"}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={clip.review_status} />
          {clip.review_status !== "approved" && <Button onClick={() => review("approved")} disabled={!!busy}><Check size={15} /> Approve</Button>}
          {clip.review_status !== "rejected" && <Button variant="ghost" onClick={() => review("rejected")} disabled={!!busy}><X size={15} /> Reject</Button>}
          {clip.review_status !== "suggested" && <Button variant="ghost" onClick={() => review("suggested")} disabled={!!busy}><RotateCcw size={15} /> Undo review</Button>}
          {clip.review_status === "approved" && clip.render_status === "done" && (
            <Button variant="primary" onClick={() => setPublishing(true)} disabled={!!busy}><CalendarClock size={15} /> Publish to YouTube</Button>
          )}
          <Button variant="danger" onClick={del} disabled={!!busy}><Trash2 size={15} /> Delete</Button>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,380px)_1fr]">
        {/* left: player + render */}
        <div className="space-y-3 lg:sticky lg:top-6 lg:self-start">
          {hasRender && (
            <div className="inline-flex rounded-md border border-line p-0.5 text-xs">
              {(["rendered", "source"] as const).map((v) => (
                <button key={v} onClick={() => setView(v)} className={`rounded px-2.5 py-1 capitalize ${view === v ? "bg-panel-2 text-fg" : "text-muted"}`}>{v}</button>
              ))}
            </div>
          )}
          <video ref={player} key={src} src={src} controls preload="metadata" playsInline
            className={`w-full rounded-lg border border-line bg-black ${showRendered ? "mx-auto max-h-[60vh] object-contain lg:max-h-[70vh]" : "aspect-video"}`}
            style={showRendered ? { aspectRatio: (ASPECTS[clip.render_settings.aspect] ?? ASPECTS["9:16"]).join("/") } : undefined} />
          {(!hasRender || clip.render_status === "outdated") && (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              {hasRender ? "The rendered video is out of date: render again to see your latest framing and captions." :
                "Showing the source video. Render to see the final framing and captions."}
            </p>
          )}
          <RenderStatus clip={clip} />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={render} disabled={rendering || !!busy || !!trimError}>
              <Film size={15} /> {rendering ? "Rendering…" : dirty || captionsDirty ? "Save & render" : renderLabel(clip)}
            </Button>
            {hasRender && <a href={`/api/clips/${clip.id}/video?download=true`} className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-panel-2 px-3 text-sm hover:bg-line"><Download size={15} /> Download MP4</a>}
          </div>
          {hasRender && clip.render_settings._rendered_duration != null && (
            <p className="text-xs text-muted">
              Rendered {clip.render_settings._rendered_duration.toFixed(1)} s
              {clip.render_settings._cuts ? ` · ${clip.render_settings._cuts} pause${clip.render_settings._cuts === 1 ? "" : "s"} cut` : ""}
              {clip.render_settings._rendered_layout ? ` · ${clip.render_settings._rendered_layout} layout` : ""}
            </p>
          )}
          <ScoreBreakdown clip={clip} />
        </div>

        {/* right: editing */}
        <div className="space-y-4">
          <Section title="Trim">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <Nudge label="Start" value={draft.start} min={0} max={duration} onChange={(v) => set({ start: v })} />
                <button type="button" className="text-xs text-accent hover:underline" onClick={() => set({ start: now() })}>Set to player time</button>
              </div>
              <div className="space-y-1">
                <Nudge label="End" value={draft.end} min={0} max={duration} onChange={(v) => set({ end: v })} />
                <button type="button" className="text-xs text-accent hover:underline" onClick={() => set({ end: now() })}>Set to player time</button>
              </div>
              <p className="pb-2 text-sm tabular-nums text-muted">{fmtTime(draft.start)} – {fmtTime(draft.end)} · <span className="text-fg">{len.toFixed(1)} s</span></p>
            </div>
            <ErrorNote>{trimError}</ErrorNote>
            {trimChanged && captionsDirty && <p className="mt-2 text-xs text-amber-200">Changing the timing resets caption text edits on save.</p>}
          </Section>

          <Section title="Framing">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Aspect ratio" group>
                <div className="flex flex-wrap gap-1.5">
                  {Object.keys(ASPECTS).map((a) => (
                    <button key={a} type="button" onClick={() => setRs({ aspect: a })}
                      className={`h-9 rounded-md border px-3 text-sm ${draft.rs.aspect === a ? "border-accent bg-accent/10 text-fg" : "border-line text-muted hover:text-fg"}`}>{a}</button>
                  ))}
                </div>
              </Field>
              <Field label="Layout" hint={LAYOUT_HELP[draft.rs.layout]}>
                <Select value={draft.rs.layout} onChange={(e) => setRs({ layout: e.target.value })}>
                  <option value="auto">Auto</option><option value="crop">Crop (fill frame)</option><option value="fit">Fit (blurred background)</option>
                  <option value="stream">Streamer split (facecam + gameplay)</option>
                </Select>
              </Field>
            </div>
            {draft.rs.layout !== "fit" && (
              <div className="mt-4 space-y-2">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={draft.rs.crop_x == null} onChange={(e) => setRs({ crop_x: e.target.checked ? null : 0.5 })} className="size-4 accent-[var(--color-accent)]" />
                  Automatic position (face tracking)
                </label>
                {draft.rs.crop_x != null && (
                  <Field label={`Crop position · ${Math.round(draft.rs.crop_x * 100)}% from left`}>
                    <input type="range" min={0} max={1} step={0.01} value={draft.rs.crop_x} onChange={(e) => setRs({ crop_x: +e.target.value })} className="w-full accent-[var(--color-accent)]" />
                  </Field>
                )}
              </div>
            )}
            <div className="mt-4"><FramePreview project={project} rs={draft.rs} sample={lines[0]?.text ?? ""} /></div>
          </Section>

          <Section title="Shorts effects">
            <div className="space-y-3">
              {([
                ["remove_pauses", "Jump cuts", "Cuts out pauses longer than 0.6 s between words, so the clip moves fast."],
                ["zoom", "Punch-in zoom", "Zooms in a little on every other sentence (face-tracked crops only)."],
                ["hook", "Hook title", "Shows a title in a box for the first 3 seconds to stop the scroll."],
              ] as const).map(([key, label, help]) => (
                <label key={key} className="flex items-start gap-2 text-sm">
                  <input type="checkbox" checked={!!draft.rs[key]} onChange={(e) => setRs({ [key]: e.target.checked })} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
                  <span><span className="font-medium">{label}</span><span className="block text-xs text-muted">{help}</span></span>
                </label>
              ))}
              {draft.rs.hook && (
                <Field label="Hook text" hint="Leave empty to use the clip title.">
                  <Input value={draft.rs.hook_text ?? ""} maxLength={100} placeholder={draft.title}
                    onChange={(e) => setRs({ hook_text: e.target.value || null })} />
                </Field>
              )}
            </div>
          </Section>

          <Section title="Captions" actions={
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.rs.captions} onChange={(e) => setRs({ captions: e.target.checked })} className="size-4 accent-[var(--color-accent)]" />
              Burn in captions
            </label>
          }>
            {draft.rs.captions && (
              <div className="space-y-4">
                <div>
                  <p className="mb-1.5 text-xs font-medium text-muted">Style preset</p>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {Object.entries(presets).map(([name, p]) => (
                      <button key={name} type="button" onClick={() => setRs({ preset: name, style: { ...p } })}
                        className={`rounded-md border px-2 py-3 text-center ${draft.rs.preset === name ? "border-accent bg-accent/10" : "border-line hover:border-zinc-600"}`}>
                        <span className="block text-sm" style={{
                          fontWeight: p.bold ? 800 : 500, color: p.color, textTransform: p.uppercase ? "uppercase" : "none",
                          WebkitTextStroke: p.box ? undefined : `${Math.min(2, p.outline_width / 3)}px ${p.outline}`, paintOrder: "stroke fill",
                          background: p.box ? "rgba(0,0,0,.6)" : undefined, padding: p.box ? "1px 4px" : undefined,
                        }}>Say <span style={{ color: p.highlight }}>this</span></span>
                        <span className="mt-1 block text-[11px] capitalize text-muted">{name}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <Field label="Position">
                    <Select value={draft.rs.position} onChange={(e) => setRs({ position: e.target.value })}>
                      <option value="top">Top</option><option value="middle">Middle</option><option value="lower">Lower third</option><option value="bottom">Bottom</option>
                    </Select>
                  </Field>
                  <Field label="Font">
                    <Select value={draft.rs.style.font} onChange={(e) => setStyle({ font: e.target.value })}>
                      {[...new Set([draft.rs.style.font, ...(sys?.fonts ?? [])])].map((f) => <option key={f}>{f}</option>)}
                    </Select>
                  </Field>
                  <Field label={`Size · ${draft.rs.style.size}px`}>
                    <input type="range" min={24} max={140} value={draft.rs.style.size} onChange={(e) => setStyle({ size: +e.target.value })} className="h-9 w-full accent-[var(--color-accent)]" />
                  </Field>
                  <Field label="Words per line">
                    <Input type="number" min={1} max={12} value={draft.rs.style.words} onChange={(e) => setStyle({ words: Math.max(1, Math.min(12, +e.target.value || 1)) })} />
                  </Field>
                  <Field label={`Outline width · ${draft.rs.style.outline_width}`}>
                    <input type="range" min={0} max={20} value={draft.rs.style.outline_width} onChange={(e) => setStyle({ outline_width: +e.target.value })} className="h-9 w-full accent-[var(--color-accent)]" />
                  </Field>
                  <div className="flex items-end gap-3">
                    {([["color", "Text"], ["highlight", "Highlight"], ["outline", "Outline"]] as const).map(([k, l]) => (
                      <label key={k} className="space-y-1.5 text-xs text-muted">
                        <span className="block">{l}</span>
                        <input type="color" value={draft.rs.style[k]} onChange={(e) => setStyle({ [k]: e.target.value.toUpperCase() })} className="h-9 w-12 cursor-pointer rounded border border-line bg-bg" />
                      </label>
                    ))}
                  </div>
                </div>
                <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
                  {([["uppercase", "UPPERCASE"], ["bold", "Bold"], ["pop", "Pop active word"], ["box", "Background box"]] as const).map(([k, l]) => (
                    <label key={k} className="flex items-center gap-2">
                      <input type="checkbox" checked={draft.rs.style[k]} onChange={(e) => setStyle({ [k]: e.target.checked })} className="size-4 accent-[var(--color-accent)]" /> {l}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </Section>

          {draft.rs.captions && (
            <Section title="Caption text" actions={<>
              {linesEdited && <Badge tone="blue">edited</Badge>}
              {(linesEdited || captionsDirty) && (
                <Button variant="ghost" onClick={() => act("reset", async () => {
                  if (!confirm("Discard caption text edits and use the transcript again?")) return;
                  await api(`/api/clips/${clip.id}`, { method: "PATCH", json: { caption_overrides: [] } });
                  setClip(await api<Clip>(`/api/clips/${clip.id}`));
                  await loadCaptions();
                })}><RotateCcw size={15} /> Reset to transcript</Button>
              )}
            </>}>
              <p className="mb-3 text-xs text-muted">Fix words the transcription got wrong. Times are seconds from the start of the clip. Edited lines highlight words evenly across the line.</p>
              <div className="max-h-96 space-y-1.5 overflow-y-auto pr-1">
                {lines.map((l, i) => (
                  <div key={i} className="grid grid-cols-2 gap-1.5 border-b border-line pb-1.5 sm:grid-cols-[72px_72px_1fr] sm:border-0 sm:pb-0">
                    <Input type="number" step={0.1} value={l.start} aria-label={`Line ${i + 1} start`} className="px-2 tabular-nums"
                      onChange={(e) => { setLines(lines.map((x, k) => k === i ? { ...x, start: r2(+e.target.value) } : x)); setSaved(""); }} />
                    <Input type="number" step={0.1} value={l.end} aria-label={`Line ${i + 1} end`} className="px-2 tabular-nums"
                      onChange={(e) => { setLines(lines.map((x, k) => k === i ? { ...x, end: r2(+e.target.value) } : x)); setSaved(""); }} />
                    <Input value={l.text} maxLength={300} className="col-span-2 sm:col-span-1" aria-label={`Line ${i + 1} text`}
                      onChange={(e) => { setLines(lines.map((x, k) => k === i ? { ...x, text: e.target.value } : x)); setSaved(""); }} />
                  </div>
                ))}
                {!lines.length && <p className="text-sm text-muted">No speech in this clip, so there are no captions.</p>}
              </div>
              <ErrorNote>{lineError}</ErrorNote>
            </Section>
          )}

          <Section title="Title, description & hashtags" actions={
            <span className="text-xs text-muted">{metaMethod === "llm" ? "Written by AI from the transcript" : metaMethod === "basic" ? "Basic suggestions (no AI model configured)" : ""}</span>
          }>
            <div className="space-y-4">
              <Field label={`Title · ${draft.title.length}/100`} hint={clip.title_edited ? "You edited this title, so regenerating will never replace it." : undefined}>
                <Input value={draft.title} maxLength={100} onChange={(e) => set({ title: e.target.value })} />
              </Field>
              {clip.title_options.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {clip.title_options.map((t) => (
                    <button key={t} type="button" onClick={() => set({ title: t.slice(0, 100) })}
                      className={`rounded-full border px-2.5 py-1 text-left text-xs ${draft.title === t ? "border-accent bg-accent/10" : "border-line text-muted hover:text-fg"}`}>{t}</button>
                  ))}
                </div>
              )}
              <Field label={`Description · ${draft.description.length}/4500`}>
                <Textarea rows={4} maxLength={4500} value={draft.description} onChange={(e) => set({ description: e.target.value })} />
              </Field>
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted">Hashtags</p>
                <HashtagEditor tags={draft.hashtags} onChange={(hashtags) => set({ hashtags })} />
              </div>
              <div className="flex flex-wrap gap-2 border-t border-line pt-3">
                <span className="self-center text-xs text-muted">Regenerate:</span>
                {([["titles", "Titles"], ["description", "Description"], ["hashtags", "Hashtags"]] as const).map(([f, l]) => (
                  <Button key={f} variant="ghost" disabled={!!busy || !clip.transcript_excerpt} onClick={() => regen([f])}>
                    <Sparkles size={14} /> {busy === f ? "Working…" : l}
                  </Button>
                ))}
              </div>
            </div>
          </Section>

          <SaveBar dirty={dirty || captionsDirty} saving={busy === "save"} savedText={saved} error={error}
            onSave={() => act("save", async () => { await save(); })}
            onDiscard={() => { setDraft(orig); setLines(linesOrig); setError(""); }} />
        </div>
      </div>
      {publishing && <ScheduleDialog clipIds={[clip.id]} onClose={() => setPublishing(false)} onDone={() => setPublishing(false)} />}
    </>
  );
}
