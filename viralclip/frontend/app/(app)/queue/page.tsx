"use client";

import { ListVideo } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { YtUpload } from "@/lib/youtube";
import { GROUPS, groupOf, UploadRow } from "@/components/uploads";
import { Card, Empty, ErrorNote, PageHeader, Loading } from "@/components/ui";

export default function QueuePage() {
  const [items, setItems] = useState<YtUpload[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => api<YtUpload[]>("/api/youtube/uploads").then(setItems).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const busy = items?.some((u) => ["uploading", "queued"].includes(u.upload_status));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [busy, load]);

  return (
    <>
      <PageHeader title="Upload Queue" sub="Everything sent to YouTube. Upload and schedule states are tracked separately." />
      <ErrorNote>{error}</ErrorNote>
      {!items ? <Loading /> : items.length === 0 ? (
        <Empty icon={<ListVideo size={22} />} title="Nothing queued">
          Approve and render clips, then send them to YouTube from <Link href="/projects" className="text-fg underline">a project</Link>.
        </Empty>
      ) : (
        <div className="space-y-6">
          {GROUPS.map((g) => {
            const rows = items.filter((u) => groupOf(u) === g);
            if (!rows.length) return null;
            return (
              <section key={g}>
                <h2 className="mb-2 text-sm font-medium text-muted">{g} <span className="tabular-nums">({rows.length})</span></h2>
                <Card className="divide-y divide-line">{rows.map((u) => <UploadRow key={u.id} u={u} onChange={load} />)}</Card>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
