"use client";

import { ArrowRight, Link2, MessageSquare } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, LinkButton, Section } from "@/components/ui";

type Status = {
  enabled: boolean; queue: Record<"waiting" | "started" | "done" | "skipped" | "failed", number>;
  scheduled_posts: number; last_scheduled_at: string | null; posts_per_day: number; runway_days: number | null;
  buffer_days: number; add_more_links: boolean;
  failures: { kind: "queue" | "project"; name: string; error: string; project_id: number | null; at: string }[];
};
type Chat = { recording: boolean; channels: { login: string; recording: boolean; messages_24h: number; last_message_at: string | null }[] };

function Tile({ label, value, sub, href }: { label: string; value: React.ReactNode; sub?: string; href?: string }) {
  const body = (
    <div className={`h-full rounded-md border border-line bg-bg p-3 ${href ? "transition hover:border-zinc-600" : ""}`}>
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-[11px] text-muted">{sub}</p>}
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

/** Dashboard "This week" panel. Hidden until /api/automation/status answers; chat part hidden if /api/chat/status doesn't. */
export function WeekStatus() {
  const [s, setS] = useState<Status | null>(null);
  const [chat, setChat] = useState<Chat | null>(null);
  useEffect(() => {
    const load = () => {
      api<Status>("/api/automation/status").then(setS).catch(() => {});
      api<Chat>("/api/chat/status").then(setChat).catch(() => setChat(null));
    };
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  if (!s) return null;

  const runway = s.runway_days == null ? "—" : `${s.runway_days.toFixed(s.runway_days < 10 ? 1 : 0)} d`;
  return (
    <Section title="This week" className="mt-3"
      actions={!s.enabled ? <Link href="/automation" className="text-xs text-accent hover:underline">Automation is off · turn on →</Link> : undefined}>
      <div className="space-y-4">
        {s.add_more_links && (
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-accent/40 bg-accent/10 px-3 py-2">
            <Link2 size={16} className="shrink-0 text-accent" />
            <p className="min-w-0 flex-1 basis-48 text-sm">
              <span className="font-medium">Add more links.</span>{" "}
              <span className="text-muted">Queue more videos to keep posting once your scheduled posts run out{s.runway_days != null ? ` (in about ${Math.max(0, Math.round(s.runway_days))} days)` : ""}.</span>
            </p>
            <LinkButton href="/links">Open Link Queue <ArrowRight size={15} /></LinkButton>
          </div>
        )}
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Tile label="Links waiting" value={s.queue.waiting} sub={s.queue.started ? `${s.queue.started} in progress` : undefined} href="/links" />
          <Tile label="Scheduled posts" value={s.scheduled_posts} sub={`${s.posts_per_day} per day`} href="/calendar" />
          <Tile label="Runway" value={runway} sub={`next link starts below ${s.buffer_days} d`} />
          <Tile label="Last scheduled" value={<span className="text-sm">{s.last_scheduled_at ? fmtDate(s.last_scheduled_at) : "—"}</span>} />
        </div>

        {s.failures.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-xs font-medium text-muted">Failures</h3>
            <ul className="divide-y divide-line rounded-md border border-line">
              {s.failures.map((f, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 p-2 text-sm">
                  <Badge tone="red">{f.kind}</Badge>
                  <span className="min-w-0 break-all font-medium">{f.name}</span>
                  <span className="min-w-0 flex-1 basis-40 text-xs text-red-300">{f.error}</span>
                  <span className="text-xs text-muted">{fmtDate(f.at)}</span>
                  {f.project_id != null && <Link href={`/projects/${f.project_id}`} className="text-xs text-accent hover:underline">Open project</Link>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {chat && chat.channels.length > 0 && (
          <div>
            <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted"><MessageSquare size={13} /> Twitch chat recording</h3>
            <ul className="space-y-1">
              {chat.channels.map((c) => (
                <li key={c.login} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium">{c.login}</span>
                  {c.recording ? <Badge tone="green">recording</Badge> : <Badge>not recording</Badge>}
                  <span className="text-xs text-muted tabular-nums">{c.messages_24h} messages in 24 h · last {fmtDate(c.last_message_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  );
}
