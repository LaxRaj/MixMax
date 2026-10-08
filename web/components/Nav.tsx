"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useIdentity } from "@/components/Identity";
import { ago, isLive, type Heartbeat } from "@/lib/song";
import styles from "./studio.module.css";

const LINKS = [
  { href: "/", label: "Songs", match: (p: string) => p === "/" || p.startsWith("/songs") },
  { href: "/upload", label: "Upload", match: (p: string) => p.startsWith("/upload") },
  { href: "/listen", label: "Listening tests", match: (p: string) => p.startsWith("/listen") },
  { href: "/dashboard", label: "Pipeline", match: (p: string) => p.startsWith("/dashboard") },
];

const SEEN_KEY = "mixmax.seenProblems";

export function seenProblems(): string[] {
  try {
    return JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

export function markProblemsSeen(ids: string[]): void {
  try {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...seenProblems(), ...ids])]));
    window.dispatchEvent(new Event("mixmax:seen"));
  } catch {
    // Storage refused; the badge simply stays.
  }
}

export function Nav() {
  const pathname = usePathname() ?? "/";
  const { name, setName } = useIdentity();
  const [heartbeat, setHeartbeat] = useState<Heartbeat>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [, setTick] = useState(0);

  // A blind listener must not see song names, and the login screen has nowhere to go.
  const hidden = pathname.startsWith("/listen") || pathname.startsWith("/login");

  useEffect(() => {
    if (hidden) return;
    let alive = true;
    const load = () =>
      fetch("/api/status")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!alive || !data) return;
          setHeartbeat(data.heartbeat);
          setProblems(data.problems ?? []);
          setLoaded(true);
        })
        .catch(() => {});
    load();
    const timer = window.setInterval(load, 30_000);
    const reseen = () => setTick((n) => n + 1);
    window.addEventListener("mixmax:seen", reseen);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener("mixmax:seen", reseen);
    };
  }, [hidden, pathname]);

  if (hidden) return null;

  const unseen = loaded ? problems.filter((id) => !seenProblems().includes(id)).length : 0;
  const live = isLive(heartbeat);

  return (
    <header className={styles.nav}>
      <div className={styles.navInner}>
        <Link href="/" className={styles.brand}>
          MixMax
        </Link>
        <nav className={styles.navLinks} aria-label="Main">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={styles.navLink}
              aria-current={link.match(pathname) ? "page" : undefined}
            >
              {link.label}
              {link.href === "/upload" && unseen > 0 && (
                <span className={styles.badge} aria-label={`${unseen} file(s) with problems`}>
                  {unseen}
                </span>
              )}
            </Link>
          ))}
        </nav>
        <div className={styles.navMeta}>
          {loaded && (
            <span
              className={styles.macState}
              data-live={live}
              title="Renders and file checks happen on the studio Mac, while `producer sync --watch` is running there."
            >
              <span className={styles.dot} />
              {live
                ? "Studio Mac is on"
                : heartbeat
                  ? `Studio Mac last seen ${ago(heartbeat.at)}`
                  : "Studio Mac has not synced yet"}
            </span>
          )}
          {name && (
            <button
              className={styles.who}
              onClick={() => setName("")}
              title="Change the name on your notes"
            >
              {name}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
