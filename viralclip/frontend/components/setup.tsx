"use client";

import { ArrowRight, ListChecks } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type SystemInfo } from "@/lib/api";
import { Card, LinkButton, Progress } from "@/components/ui";

/** One check inside a setup part. "info" never blocks completion (things ViralClip can't check, or notes). */
export type Step = { state: "ok" | "todo" | "info"; text: ReactNode; next?: ReactNode };
export type Part = { key: string; title: string; guide: string; steps: Step[]; soon?: boolean };

type Cfg = { configured: boolean; missing: string[]; audited?: boolean };
type Acc = { id: number; status: string; autopublish_enabled: boolean; display_name?: string; channel_title?: string };
type Auto = {
  settings: { enabled: boolean; twitch_account_ids: number[]; youtube_account_ids: number[] };
  twitch_accounts: { id: number; display_name: string; status: string }[];
  youtube_accounts: Acc[]; tiktok_accounts: Acc[];
};
type Sys = SystemInfo & { research_configured?: boolean; research?: { configured?: boolean } | boolean };
type Data = {
  sys?: Sys; twitch?: Cfg; youtube?: Cfg; tiktok?: Cfg; auto?: Auto;
  yt?: { published: number; scheduled: number }; tt?: { published: number; drafts_sent: number }; rendered?: unknown[];
};

const A = ({ href, children }: { href: string; children: ReactNode }) => {
  const ext = href.startsWith("http");
  return <a href={href} {...(ext ? { target: "_blank", rel: "noreferrer" } : {})} className="font-medium text-accent hover:underline">{children}</a>;
};
const Env = ({ children }: { children: ReactNode }) => <code className="rounded bg-panel-2 px-1 py-0.5 text-[12px] text-fg">{children}</code>;
const unknown: Step = { state: "todo", text: "Couldn't check this right now.", next: "Retrying automatically." };

function envStep(cfg: Cfg | undefined, keys: string[], what: string, portal: ReactNode): Step {
  if (!cfg) return unknown;
  if (cfg.configured) return { state: "ok", text: `${what} keys are in .env.` };
  const missing = cfg.missing.length ? cfg.missing : keys;
  return {
    state: "todo", text: `${what} keys are missing.`,
    next: <>Get them from {portal}, add {missing.map((k, i) => <span key={k}>{i ? ", " : ""}<Env>{k}</Env></span>)} to <Env>viralclip\.env</Env> and restart <Env>start.bat</Env>.</>,
  };
}

function accountStep(accs: { status: string; name?: string }[] | undefined, noun: string, next: ReactNode): Step {
  if (!accs) return unknown;
  const ok = accs.filter((a) => a.status === "ok");
  if (ok.length) return { state: "ok", text: `${noun} connected: ${ok.map((a) => a.name || "account").join(", ")}.` };
  if (accs.length) return { state: "todo", text: `${noun} login expired.`, next: <>Reconnect it: {next}.</> };
  return { state: "todo", text: `No ${noun} connected yet.`, next };
}

function build(d: Data): Part[] {
  const { sys, auto } = d;
  const model = sys?.whisper_default ?? "small";
  const research = sys == null ? undefined
    : typeof sys.research === "boolean" ? sys.research : sys.research?.configured ?? sys.research_configured;
  const pos = (n?: number) => n != null && n > 0;
  return [
    { key: "start", title: "ViralClip running and account created", guide: "Part 1", steps: [
      { state: "ok", text: "ViralClip is running and you're signed in." },
    ] },
    { key: "model", title: "Speech model", guide: "Part 1", steps: [
      !sys ? unknown : sys.whisper_downloaded.includes(model)
        ? { state: "ok", text: <>Speech model <Env>{model}</Env> is downloaded.</> }
        : { state: "todo", text: <>Speech model <Env>{model}</Env> isn&apos;t downloaded yet. It downloads on your first clip.</>,
            next: <><A href="/projects/new">Create a project</A>: upload any video with talking and generate clips.</> },
    ] },
    { key: "twitch", title: "Twitch", guide: "Parts 2 and 5", steps: [
      envStep(d.twitch, ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"], "Twitch", <A href="https://dev.twitch.tv/console/apps">the Twitch developer console</A>),
      accountStep(auto?.twitch_accounts.map((a) => ({ status: a.status, name: a.display_name })), "Twitch account",
        <><A href="/automation">Automation</A> → Connect Twitch</>),
      { state: "info", text: <>Turn on <b>Store past broadcasts</b>: Twitch → <A href="https://dashboard.twitch.tv/settings/stream">Creator Dashboard → Settings → Stream</A>. ViralClip can&apos;t check this.</> },
    ] },
    { key: "youtube", title: "YouTube", guide: "Parts 3 and 5", steps: [
      envStep(d.youtube, ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"], "YouTube", <A href="https://console.cloud.google.com">Google Cloud Console</A>),
      accountStep(auto?.youtube_accounts.map((a) => ({ status: a.status, name: a.channel_title })), "YouTube channel",
        <><A href="/youtube">YouTube Studio</A> → Connect a channel</>),
      { state: "info", text: "Until Google audits your project, uploads stay private. In Testing mode YouTube logins expire every 7 days." },
    ] },
    { key: "tiktok", title: "TikTok", guide: "Parts 4 and 5", steps: [
      envStep(d.tiktok, ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"], "TikTok", <A href="https://developers.tiktok.com">TikTok for Developers</A>),
      accountStep(auto?.tiktok_accounts.map((a) => ({ status: a.status, name: a.display_name })), "TikTok account",
        <><A href="/tiktok">TikTok</A> → Connect TikTok</>),
      d.tiktok?.audited
        ? { state: "ok", text: "App audited: clips post publicly by themselves." }
        : { state: "info", text: <>Not audited yet, so clips go to your TikTok inbox as <b>drafts</b>. After the audit, add <Env>TIKTOK_AUDITED=true</Env> to <Env>viralclip\.env</Env> and restart <Env>start.bat</Env>.</> },
    ] },
    { key: "research", title: "Trend titles (research key)", guide: "Optional", soon: research === undefined, steps: [
      research === undefined
        ? { state: "info", text: "Coming soon: trend research isn't in this version yet." }
        : research
          ? { state: "ok", text: "Research key is set, so titles use what's trending." }
          : { state: "todo", text: "No research key configured.",
              next: <>Create an API key in <A href="https://console.cloud.google.com/apis/credentials">Google Cloud → Credentials</A>, add <Env>YOUTUBE_RESEARCH_API_KEY</Env> to <Env>viralclip\.env</Env> and restart <Env>start.bat</Env>.</> },
    ] },
    { key: "test", title: "Test once before automating", guide: "Part 6", steps: [
      !d.rendered ? unknown : d.rendered.length
        ? { state: "ok", text: "At least one clip rendered." }
        : { state: "todo", text: "No rendered clips yet.", next: <>Upload or import a stream, then <A href="/clips">Generated Clips</A> → approve one → <b>Render</b>.</> },
      !d.yt ? unknown : pos(d.yt.published + d.yt.scheduled)
        ? { state: "ok", text: "A YouTube upload succeeded." }
        : { state: "todo", text: "No successful YouTube upload yet.", next: <>Open a rendered clip in <A href="/clips">Generated Clips</A> → <b>Publish → YouTube</b>, privacy Private, publish now.</> },
      !d.tt ? unknown : pos(d.tt.published + d.tt.drafts_sent)
        ? { state: "ok", text: "A TikTok post or draft succeeded." }
        : { state: "todo", text: "No successful TikTok post or draft yet.", next: <>Open a rendered clip in <A href="/clips">Generated Clips</A> → <b>Publish → TikTok</b> → Draft, then check your TikTok inbox.</> },
    ] },
    { key: "auto", title: "Turn on automation", guide: "Part 7", steps: !auto ? [unknown] : [
      auto.settings.enabled ? { state: "ok", text: "Automation is on." }
        : { state: "todo", text: "Automation is off.", next: <>On the <A href="/automation">Automation page</A>, turn <b>Automation on</b>.</> },
      auto.settings.twitch_account_ids.length + auto.settings.youtube_account_ids.length
        ? { state: "ok", text: "New VODs are auto-imported from at least one source." }
        : { state: "todo", text: "No source is watched for new VODs.", next: <>On the <A href="/automation">Automation page</A>, tick <b>Auto-import new VODs</b> for your Twitch account.</> },
      auto.youtube_accounts.some((a) => a.autopublish_enabled) ? { state: "ok", text: "A YouTube auto-publish rule is on." }
        : { state: "todo", text: "No YouTube auto-publish rule.", next: <>In <A href="/youtube">YouTube Studio</A>, enable an auto-publish rule, e.g. 2 clips a day at 18:00.</> },
      auto.tiktok_accounts.some((a) => a.autopublish_enabled) ? { state: "ok", text: "A TikTok auto-publish rule is on." }
        : { state: "todo", text: "No TikTok auto-publish rule.", next: <>On the <A href="/tiktok">TikTok page</A>, enable an auto-publish rule.</> },
    ] },
  ];
}

export const partDone = (p: Part) => p.steps.every((s) => s.state !== "todo");

/** Live setup checklist: re-checks every 10 s and when the tab regains focus (e.g. after restarting start.bat). */
export function useSetup() {
  const [parts, setParts] = useState<Part[] | null>(null);
  useEffect(() => {
    const get = <T,>(p: string) => api<T>(p).catch(() => undefined);
    // ponytail: 8 small GETs per poll; fold into one /api/setup endpoint if it ever shows up in profiles.
    const load = async () => {
      const [sys, twitch, youtube, tiktok, auto, yt, tt, rendered] = await Promise.all([
        get<Sys>("/api/system"), get<Cfg>("/api/twitch/config"), get<Cfg>("/api/youtube/config"), get<Cfg>("/api/tiktok/config"),
        get<Auto>("/api/automation"), get<Data["yt"]>("/api/youtube/stats"), get<Data["tt"]>("/api/tiktok/stats"),
        get<unknown[]>("/api/clips?render_status=done"),
      ]);
      setParts(build({ sys, twitch, youtube, tiktok, auto, yt, tt, rendered }));
    };
    load();
    const t = setInterval(load, 10000);
    const onVis = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  const counted = parts?.filter((p) => !p.soon) ?? [];
  return { parts, done: counted.filter(partDone).length, total: counted.length };
}

/** Dashboard nudge, shown only while setup is incomplete. */
export function SetupBanner() {
  const { parts, done, total } = useSetup();
  if (!parts || done === total) return null;
  const next = parts.find((p) => !p.soon && !partDone(p));
  return (
    <Card className="mb-6 flex flex-wrap items-center gap-3 border-accent/40 p-4">
      <ListChecks size={18} className="shrink-0 text-accent" />
      <div className="min-w-0 flex-1 basis-48 space-y-2">
        <p className="text-sm"><span className="font-medium">Setup: {done} of {total} done.</span>{next && <span className="text-muted"> Next: {next.title}.</span>}</p>
        <Progress value={done / total} />
      </div>
      <LinkButton href="/setup" variant="primary">Continue setup <ArrowRight size={15} /></LinkButton>
    </Card>
  );
}
