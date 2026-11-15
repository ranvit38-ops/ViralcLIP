"use client";

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { Badge, Card, Loading, PageHeader, Progress } from "@/components/ui";
import { partDone, useSetup, type Step } from "@/components/setup";

const ICON = {
  ok: <CheckCircle2 size={16} className="text-emerald-400" aria-label="Done" />,
  todo: <AlertTriangle size={16} className="text-amber-400" aria-label="To do" />,
  info: <Info size={16} className="text-sky-400" aria-label="Note" />,
};

function StepRow({ s }: { s: Step }) {
  return (
    <li className="flex gap-2.5 py-2">
      <span className="mt-0.5 shrink-0">{ICON[s.state]}</span>
      <div className={`min-w-0 text-sm ${s.state === "info" ? "text-muted" : ""}`}>
        <p className="break-words">{s.text}</p>
        {s.next && <p className="mt-1 break-words text-muted"><span className="font-medium text-amber-300">Do this next:</span> {s.next}</p>}
      </div>
    </li>
  );
}

export default function SetupPage() {
  const { parts, done, total } = useSetup();
  if (!parts) return <Loading />;
  return (
    <>
      <PageHeader title="Setup" sub="Mirrors docs/SETUP_GUIDE.md and re-checks itself every few seconds." />
      <Card className="mb-6 space-y-2 p-4">
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span className="font-medium">{done === total ? "Setup complete" : `${done} of ${total} done`}</span>
          <span className="text-xs tabular-nums text-muted">{Math.round((done / total) * 100)}%</span>
        </div>
        <Progress value={done / total} />
        <p className="text-xs text-muted">Keys can&apos;t be entered in the browser. Edit <code>viralclip\.env</code> with Notepad, then close the ViralClip window and double-click <code>start.bat</code> again.</p>
      </Card>
      <ol className="space-y-3">
        {parts.map((p, i) => {
          const ok = partDone(p);
          return (
            <li key={p.key}>
              <Card className="p-4">
                <div className="flex items-center gap-2">
                  <span className={`grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold ring-1 ${p.soon ? "bg-panel-2 text-muted ring-line" : ok ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30" : "bg-amber-500/10 text-amber-300 ring-amber-500/30"}`}>{i + 1}</span>
                  <h2 className="min-w-0 flex-1 text-sm font-semibold">{p.title}<span className="block text-xs font-normal text-muted sm:hidden">{p.guide}</span></h2>
                  <span className="hidden text-xs text-muted sm:inline">{p.guide}</span>
                  {p.soon ? <Badge>coming soon</Badge> : <Badge tone={ok ? "green" : "amber"}>{ok ? "done" : "to do"}</Badge>}
                </div>
                <ul className="mt-2 divide-y divide-line/60 sm:pl-8">{p.steps.map((s, j) => <StepRow key={j} s={s} />)}</ul>
              </Card>
            </li>
          );
        })}
      </ol>
    </>
  );
}
