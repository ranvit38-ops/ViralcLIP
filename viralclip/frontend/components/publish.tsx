"use client";

import { MonitorPlay, Music2, Send, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { YouTubePublish } from "@/components/schedule";
import { TikTokPublish } from "@/components/tiktok";
import { Card } from "@/components/ui";

export type Platform = "youtube" | "tiktok";

/** One "Publish" dialog for the editor and the bulk bar, with a tab per platform. */
export function PublishDialog({ clipIds, onClose, onDone }: { clipIds: number[]; onClose: () => void; onDone?: () => void }) {
  const [tab, setTab] = useState<Platform>("youtube");
  // Open on TikTok when YouTube isn't set up but TikTok is.
  useEffect(() => {
    const ok = (p: string) => api<{ configured: boolean }>(p).then((c) => c.configured).catch(() => false);
    Promise.all([ok("/api/youtube/config"), ok("/api/tiktok/config")]).then(([yt, tt]) => { if (!yt && tt) setTab("tiktok"); });
  }, []);
  const tabs = [["youtube", "YouTube", MonitorPlay], ["tiktok", "TikTok", Music2]] as const;
  return (
    <div className="fixed inset-0 z-50 flex justify-center overflow-y-auto bg-black/60 p-4" role="dialog" aria-modal="true" aria-labelledby="publish-title">
      <Card className="my-auto w-full max-w-lg p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 id="publish-title" className="flex items-center gap-2 font-semibold"><Send size={18} /> Publish {clipIds.length} clip{clipIds.length > 1 ? "s" : ""}</h2>
          <button onClick={onClose} aria-label="Close" className="text-muted hover:text-fg"><X size={18} /></button>
        </div>
        <div role="tablist" aria-label="Platform" className="mb-4 grid grid-cols-2 rounded-md border border-line p-0.5">
          {tabs.map(([id, label, Icon]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`flex items-center justify-center gap-2 rounded px-3 py-1.5 text-sm ${tab === id ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"}`}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>
        {tab === "youtube"
          ? <YouTubePublish clipIds={clipIds} onClose={onClose} onDone={onDone} />
          : <TikTokPublish clipIds={clipIds} onClose={onClose} onDone={onDone} />}
      </Card>
    </div>
  );
}
