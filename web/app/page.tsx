"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "@/components/studio.module.css";
import { STATE_COPY } from "@/lib/compare";
import { ago, clock, isLive, type Heartbeat, type SongSummary } from "@/lib/song";

type Home = { songs: SongSummary[]; heartbeat: Heartbeat; published: boolean };

export default function SongsPage() {
  const [data, setData] = useState<Home | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Songs — MixMax studio";
    let alive = true;
    const load = () =>
      fetch("/api/songs")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Could not load songs (${r.status})`))))
        .then((next: Home) => alive && setData(next))
        .catch((e: Error) => alive && setError(e.message));
    load();
    const timer = window.setInterval(load, 20_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  if (error && !data) {
    return (
      <main className={styles.page}>
        <div className={styles.head}>
          <p className={styles.eyebrow}>Songs</p>
          <h1>Could not load</h1>
          <p>{error}</p>
        </div>
      </main>
    );
  }
  if (!data) {
    return (
      <main className={styles.page}>
        <p className={styles.muted}>Loading…</p>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>Songs</p>
        <h1>What we&apos;re working on</h1>
        <p>
          Open a song to hear each piece of it, leave notes, change how it&apos;s mixed, or swap a
          file. Everything you say is kept with the song.
        </p>
      </header>

      {!isLive(data.heartbeat) && data.songs.length > 0 && (
        <p className={styles.notice}>
          <strong>The studio Mac is not syncing right now</strong>
          {data.heartbeat ? ` — last seen ${ago(data.heartbeat.at)}.` : "."} You can still listen
          and leave notes. New renders and file checks wait until it is back.
        </p>
      )}

      {data.songs.length === 0 ? (
        <div className={styles.empty}>
          <p>
            {data.published
              ? "No songs yet. Upload a vocal to start one."
              : "Nothing has been published yet. On the studio Mac, run:"}
          </p>
          {data.published ? (
            <Link className={styles.primary} href="/upload" style={{ display: "inline-block", textDecoration: "none" }}>
              Upload a file
            </Link>
          ) : (
            <code>producer sync --watch</code>
          )}
        </div>
      ) : (
        <div className={styles.grid}>
          {data.songs.map((song) => (
            <Link key={song.slug} href={`/songs/${song.slug}`} className={styles.songCard}>
              <div>
                <div className={styles.songTitle}>{song.title}</div>
                <div className={styles.kind}>
                  {song.kind === "vocal-only" ? "vocal + beat" : "finished mix"} · {clock(song.duration_s)}
                </div>
              </div>
              <div>
                <div className={styles.pctRow}>
                  <span className={styles.pctBig}>{song.percent}%</span>
                  <span className={styles.kind}>finished</span>
                </div>
                <div className={styles.pctBar} style={{ marginTop: 10 }}>
                  <div className={styles.pctFill} style={{ width: `${song.percent}%` }} />
                </div>
              </div>
              <div className={styles.stageDots}>
                {song.stages.map((stage) => (
                  <span
                    key={stage.key}
                    className={styles.stageDot}
                    data-s={stage.state}
                    title={STATE_COPY[stage.state].label}
                  >
                    {STATE_COPY[stage.state].mark} {stage.label}
                  </span>
                ))}
              </div>
              <div className={styles.cardNext}>
                <strong>Next</strong>
                {song.next_step}
              </div>
              <div className={styles.cardFoot}>
                <span data-on={song.notes > 0}>
                  {song.notes === 0 ? "No notes yet" : `${song.notes} note${song.notes === 1 ? "" : "s"}`}
                </span>
                {song.pending > 0 && (
                  <span data-on="true">
                    {song.pending} change{song.pending === 1 ? "" : "s"} waiting to render
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
