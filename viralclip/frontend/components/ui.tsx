import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

const BTN = {
  primary: "bg-accent text-accent-fg hover:brightness-110",
  secondary: "bg-panel-2 text-fg border border-line hover:bg-line",
  ghost: "text-muted hover:text-fg hover:bg-panel-2",
  danger: "bg-red-500/10 text-red-300 border border-red-500/30 hover:bg-red-500/20",
};
const btnBase = "inline-flex items-center justify-center gap-2 rounded-md px-3 h-9 text-sm font-medium transition disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-accent";

export function Button({ variant = "secondary", className, ...p }: ComponentProps<"button"> & { variant?: keyof typeof BTN }) {
  return <button className={cx(btnBase, BTN[variant], className)} {...p} />;
}

export function LinkButton({ variant = "secondary", className, ...p }: ComponentProps<typeof Link> & { variant?: keyof typeof BTN }) {
  return <Link className={cx(btnBase, BTN[variant], className)} {...p} />;
}

export function Card({ className, ...p }: ComponentProps<"div">) {
  return <div className={cx("rounded-lg border border-line bg-panel", className)} {...p} />;
}

export function Input({ className, ...p }: ComponentProps<"input">) {
  return <input className={cx("h-9 w-full rounded-md border border-line bg-bg px-3 text-sm outline-none focus:border-accent placeholder:text-muted/60", className)} {...p} />;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}

const TONES: Record<string, string> = {
  green: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
  amber: "bg-amber-500/10 text-amber-300 ring-amber-500/25",
  red: "bg-red-500/10 text-red-300 ring-red-500/25",
  blue: "bg-sky-500/10 text-sky-300 ring-sky-500/25",
  gray: "bg-zinc-500/10 text-zinc-300 ring-zinc-500/25",
};
const STATUS_TONE: Record<string, keyof typeof TONES> = {
  uploaded: "blue", ready: "green", done: "green", approved: "green", published: "green", scheduled: "blue",
  uploading: "amber", processing: "amber", queued: "amber", running: "amber", rendering: "amber", cancel_requested: "amber",
  failed: "red", rejected: "red", cancelled: "gray", suggested: "gray", none: "gray", outdated: "amber",
};

export function Select({ className, ...p }: ComponentProps<"select">) {
  return <select className={cx("h-9 w-full rounded-md border border-line bg-bg px-2 text-sm outline-none focus:border-accent", className)} {...p} />;
}

export function Textarea({ className, ...p }: ComponentProps<"textarea">) {
  return <textarea className={cx("w-full rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent placeholder:text-muted/60", className)} {...p} />;
}

/** Card section with a heading row. */
export function Section({ title, actions, children, className }: { title: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Card className={cx("p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </Card>
  );
}

export function Badge({ children, tone = "gray" }: { children: ReactNode; tone?: keyof typeof TONES }) {
  return <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", TONES[tone])}>{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "gray"}>{status.replace("_", " ")}</Badge>;
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.min(100, value * 100)}%` }} />
    </div>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <div className="text-muted">{icon}</div>
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted">{children}</div>}
    </Card>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <p role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{children}</p>;
}
