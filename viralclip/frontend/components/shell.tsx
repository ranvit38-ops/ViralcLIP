"use client";

import { Bot, CalendarDays, Loader2, Clapperboard, Film, LayoutDashboard, ListVideo, LogOut, Menu, MonitorPlay, Plus, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// Only sections that actually work are listed. New ones are added as their phase ships.
const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects/new", label: "Create Project", icon: Plus },
  { href: "/projects", label: "My Videos", icon: Film },
  { href: "/clips", label: "Generated Clips", icon: Clapperboard },
  { href: "/automation", label: "Automation", icon: Bot },
  { href: "/youtube", label: "YouTube Studio", icon: MonitorPlay },
  { href: "/calendar", label: "Publishing Calendar", icon: CalendarDays },
  { href: "/queue", label: "Upload Queue", icon: ListVideo },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [me, setMe] = useState<{ email: string } | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => { api("/api/auth/me").then(setMe).catch(() => {}); }, []);

  const active = (href: string) =>
    href === "/projects" ? path.startsWith("/projects") && path !== "/projects/new"
      : href === "/" || href === "/projects/new" ? path === href : path === href || path.startsWith(href + "/");

  if (!me) return <div className="grid min-h-dvh place-items-center"><Loader2 size={20} className="animate-spin text-muted" aria-label="Loading" /></div>;

  const nav = (
    <nav className="flex h-full flex-col gap-1 p-3">
      <Link href="/" className="mb-4 flex items-center gap-2 px-2 pt-1 text-[15px] font-semibold tracking-tight">
        <img src="/icon.svg" alt="" className="size-6" />
        ViralClip
      </Link>
      {NAV.map(({ href, label, icon: Icon }) => (
        <Link key={href} href={href} onClick={() => setOpen(false)}
          className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition ${active(href) ? "bg-panel-2 text-fg" : "text-muted hover:bg-panel-2 hover:text-fg"}`}>
          <Icon size={16} /> {label}
        </Link>
      ))}
      <div className="mt-auto border-t border-line pt-3">
        <p className="truncate px-2 text-xs text-muted" title={me.email}>{me.email}</p>
        <button onClick={async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/login"; }}
          className="mt-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted hover:bg-panel-2 hover:text-fg">
          <LogOut size={16} /> Sign out
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 border-r border-line bg-panel md:block">{nav}</aside>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-panel">{nav}</aside>
        </div>
      )}
      <div className="min-w-0 flex-1">
        <header className="flex h-12 items-center gap-2 border-b border-line px-4 md:hidden">
          <button aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen(!open)} className="text-muted">{open ? <X size={20} /> : <Menu size={20} />}</button>
          <span className="flex items-center gap-2 text-sm font-semibold"><img src="/icon.svg" alt="" className="size-5" />ViralClip</span>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
