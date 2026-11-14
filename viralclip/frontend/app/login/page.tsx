"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, ErrorNote, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api(`/api/auth/${mode}`, { method: "POST", json: { email, password } });
      const next = new URLSearchParams(location.search).get("next");
      location.href = next?.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  }

  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-6 flex items-center gap-2 text-[15px] font-semibold">
          <span className="grid size-6 place-items-center rounded bg-accent text-[11px] font-bold text-accent-fg">VC</span>
          ViralClip
        </div>
        <h1 className="text-lg font-semibold">{mode === "login" ? "Sign in" : "Create an account"}</h1>
        <p className="mb-5 mt-1 text-sm text-muted">Your videos stay on this server.</p>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Email"><Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Password" hint={mode === "register" ? "At least 8 characters." : undefined}>
            <Input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 8 : undefined} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <ErrorNote>{error}</ErrorNote>
          <Button variant="primary" className="w-full" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</Button>
        </form>
        <button className="mt-4 text-sm text-muted hover:text-fg" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </Card>
    </div>
  );
}
