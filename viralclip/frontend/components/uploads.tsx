"use client";

import { ExternalLink, Pause, Play, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { whenLabel, type YtUpload } from "@/lib/youtube";
import { EditTime } from "@/components/schedule";
import { Button, ErrorNote, Progress, StatusBadge } from "@/components/ui";
import { PlatformBadge, TikTokPostRow } from "@/components/tiktok";
import type { TtPost } from "@/lib/tiktok";

export function QueueRow({ item, onChange }: { item: QueueItem; onChange: () => void }) {
  return isTikTok(item) ? <TikTokPostRow p={item} onChange={onChange} /> : <UploadRow u={item} onChange={onChange} />;
}

export const GROUPS = ["Uploading", "Pending", "Failed", "Scheduled", "Published", "Uploaded", "Drafts sent", "Cancelled"] as const;

/** A YouTube upload or a TikTok post; the queue and calendar show both. */
export type QueueItem = YtUpload | TtPost;
export const isTikTok = (x: QueueItem): x is TtPost => (x as TtPost).platform === "tiktok";
export const itemKey = (x: QueueItem) => `${isTikTok(x) ? "tt" : "yt"}-${x.id}`;
export const itemWhen = (x: QueueItem) => isTikTok(x) ? x.publish_at ?? x.posted_at ?? x.created_at : x.publish_at ?? x.uploaded_at ?? x.created_at;
export const itemBusy = (x: QueueItem) => isTikTok(x) ? ["queued", "uploading", "processing"].includes(x.status) : ["uploading", "queued"].includes(x.upload_status);

/** Both platforms' items. TikTok is optional: if its endpoints fail (not configured / older backend), YouTube still shows. */
export async function loadQueue(): Promise<QueueItem[]> {
  const [yt, tt] = await Promise.all([
    api<YtUpload[]>("/api/youtube/uploads"),
    api<Omit<TtPost, "platform">[]>("/api/tiktok/posts").catch(() => []),
  ]);
  return [...yt, ...tt.map((p) => ({ ...p, platform: "tiktok" as const }))];
}

export function groupOf(u: QueueItem): (typeof GROUPS)[number] {
  if (isTikTok(u)) {
    switch (u.status) {
      case "uploading": case "processing": return "Uploading";
      case "queued": return "Pending";
      case "scheduled": return "Scheduled";
      case "published": return "Published";
      case "draft_sent": return "Drafts sent";
      case "cancelled": return "Cancelled";
      default: return "Failed";
    }
  }
  switch (u.upload_status) {
    case "uploading": return "Uploading";
    case "queued": case "paused": case "quota_exceeded": return "Pending";
    case "failed": case "auth_required": return "Failed";
    case "cancelled": return "Cancelled";
  }
  if (u.schedule_status === "scheduled") return "Scheduled";
  if (u.schedule_status === "published") return "Published";
  if (u.schedule_status === "failed") return "Failed"; // uploaded fine, but YouTube didn't accept the schedule
  return "Uploaded";
}

export function UploadRow({ u, onChange }: { u: YtUpload; onChange: () => void }) {
  const [error, setError] = useState("");
  const act = async (path: string, method = "POST") => {
    setError("");
    try { await api(`/api/youtube/uploads/${u.id}${path}`, { method }); onChange(); }
    catch (e: any) { setError(e.message); }
  };
  const s = u.upload_status;
  const notFinished = ["queued", "uploading", "paused", "quota_exceeded", "auth_required"].includes(s);
  return (
    <div className="space-y-2 p-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="aspect-[9/16] w-12 shrink-0 overflow-hidden rounded bg-panel-2">
          {u.clip_thumbnail_url && <img src={u.clip_thumbnail_url} alt="" className="size-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="truncate text-sm font-medium">{u.clip_title || `Clip ${u.clip_id}`}</p>
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <PlatformBadge tiktok={false} />
            <StatusBadge status={s} />
            {u.schedule_status !== "none" && <StatusBadge status={u.schedule_status === "failed" ? "schedule_failed" : u.schedule_status} />}
            <span>{u.publish_at && u.schedule_status !== "failed" && u.schedule_status !== "published" ? "private until publish time" : u.privacy}</span> · <span>{u.channel_title}</span>
          </p>
          <p className="text-xs text-muted">
            {whenLabel(u) ? <>Publishes {whenLabel(u)}</> : u.uploaded_at ? <>Uploaded {fmtDate(u.uploaded_at)}</> : <>Added {fmtDate(u.created_at)}</>}
            {u.run_after && <> · waiting until {fmtDate(u.run_after)}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {u.youtube_url && <a href={u.youtube_url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm text-muted hover:bg-panel-2 hover:text-fg"><ExternalLink size={16} /> YouTube</a>}
          {["queued", "uploading", "quota_exceeded"].includes(s) && <Button variant="ghost" onClick={() => act("/pause")}><Pause size={16} /> Pause</Button>}
          {s === "paused" && <Button variant="ghost" onClick={() => act("/retry")}><Play size={16} /> Resume</Button>}
          {["failed", "auth_required", "cancelled"].includes(s) && <Button variant="ghost" onClick={() => act("/retry")}><RotateCcw size={16} /> Retry</Button>}
          {notFinished && <Button variant="ghost" onClick={() => act("/cancel")}><X size={16} /> Cancel</Button>}
          {s !== "uploading" && (
            <Button variant="ghost" aria-label="Remove from queue" onClick={() => {
              const msg = u.youtube_video_id ? "Stop tracking this item? The video stays on YouTube." : "Remove this item from the queue?";
              if (confirm(msg)) act("", "DELETE");
            }}><Trash2 size={16} /></Button>
          )}
        </div>
      </div>
      {s === "uploading" && <Progress value={u.progress} />}
      {u.error && <p className="text-xs text-red-300">{u.error}</p>}
      {!u.youtube_video_id && s !== "uploading" && <EditTime upload={u} onSaved={onChange} />}
      <ErrorNote>{error}</ErrorNote>
    </div>
  );
}
