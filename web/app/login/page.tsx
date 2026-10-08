"use client";

import { useEffect, useState } from "react";
import styles from "@/components/studio.module.css";

export default function LoginPage() {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Sign in — MixMax studio";
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passcode }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error ?? "Could not sign in.");
      }
      // Only ever follow a path on this site, never a full URL from the query string.
      const next = new URLSearchParams(window.location.search).get("next") ?? "/";
      window.location.assign(next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className={styles.login}>
      <form className={`${styles.loginCard} rise`} onSubmit={submit}>
        <p className={styles.eyebrow}>MixMax studio</p>
        <h1>Enter the passcode</h1>
        <p>It&apos;s the one Lakshya sent you. You only need it once on this device.</p>
        <input
          className={styles.input}
          type="password"
          aria-label="Passcode"
          value={passcode}
          onChange={(event) => setPasscode(event.target.value)}
          autoComplete="current-password"
          autoFocus
        />
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <button className={styles.primary} type="submit" disabled={busy || !passcode.trim()}>
          {busy ? "Checking…" : "Open the studio"}
        </button>
      </form>
    </main>
  );
}
