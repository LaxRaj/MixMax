"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { NameGate, useIdentity } from "@/components/Identity";
import { markProblemsSeen } from "@/components/Nav";
import styles from "@/components/studio.module.css";
import {
  ago,
  clock,
  isLive,
  slugify,
  type Heartbeat,
  type SongSummary,
  type Upload,
  type UploadKind,
} from "@/lib/song";
import { checkFile, sendUpload, type ClientCheck } from "@/lib/uploadClient";

const NEW_SONG = "__new__";

const KIND_COPY: Record<UploadKind, string> = {
  vocal: "Vocal",
  beat: "Beat / instrumental",
  reference: "Reference track",
  other: "Something else",
};

type Item = {
  key: string;
  file: File;
  kind: UploadKind;
  song: string;          // a slug, or NEW_SONG
  title: string;         // used when song === NEW_SONG
  chosen: boolean;       // the person picked the song themselves
  check: ClientCheck | null;
  state: "waiting" | "uploading" | "done" | "failed";
  progress: number;
  error?: string;
};

function guessKind(name: string): UploadKind {
  const lower = name.toLowerCase();
  if (/\b(beat|instrumental|inst|backing)\b|[-_ ](beat|instrumental)/.test(lower)) return "beat";
  if (/\bref(erence)?\b|[-_ ]ref/.test(lower)) return "reference";
  return "vocal";
}

function cleanTitle(name: string): string {
  return name
    .replace(/\.[^.]+$/, "")
    .replace(/[-_ ]*(beat|instrumental|inst|backing|reference|ref|vocal|vox)\b/gi, "")
    .replace(/[-_]+/g, " ")
    .trim();
}

function matchSong(title: string, songs: SongSummary[]): string {
  const slug = slugify(title);
  const match = songs.find((s) => slug.includes(s.slug) || s.slug.includes(slug));
  return match ? match.slug : NEW_SONG;
}

export default function UploadPage() {
  const { name } = useIdentity();
  const [items, setItems] = useState<Item[]>([]);
  const [songs, setSongs] = useState<SongSummary[]>([]);
  const [uploads, setUploads] = useState<Upload[] | null>(null);
  const [heartbeat, setHeartbeat] = useState<Heartbeat>(null);
  const [mode, setMode] = useState<"blob" | "local">("local");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const counter = useRef(0);

  const refresh = useCallback(
    () =>
      fetch("/api/uploads")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data) return;
          setUploads(data.uploads);
          setHeartbeat(data.heartbeat);
          setMode(data.mode);
        })
        .catch(() => {}),
    [],
  );

  useEffect(() => {
    document.title = "Upload — MixMax studio";
    void refresh();
    fetch("/api/songs")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        setSongs(data.songs);
        // Files dropped before the song list arrived get matched now.
        setItems((current) =>
          current.map((item) =>
            item.chosen || item.song !== NEW_SONG ? item : { ...item, song: matchSong(item.title, data.songs) },
          ),
        );
      })
      .catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const patch = (key: string, change: Partial<Item>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));

  const add = (files: FileList | File[]) => {
    const fresh: Item[] = [...files].map((file) => {
      const kind = guessKind(file.name);
      const title = cleanTitle(file.name);
      return {
        key: `f${counter.current++}`,
        file,
        kind,
        song: matchSong(title, songs),
        title,
        chosen: false,
        check: null,
        state: "waiting",
        progress: 0,
      };
    });
    setItems((current) => [...current, ...fresh]);
    for (const item of fresh) {
      void checkFile(item.file, item.kind).then((check) => patch(item.key, { check }));
    }
  };

  const setKind = (item: Item, kind: UploadKind) => {
    patch(item.key, { kind, check: null });
    void checkFile(item.file, kind).then((check) => patch(item.key, { check }));
  };

  const sendable = (item: Item) =>
    item.state === "waiting" &&
    item.check !== null &&
    item.check.blockers.length === 0 &&
    (item.song !== NEW_SONG || item.title.trim().length > 0);
  const ready = items.filter(sendable);

  const sendAll = async () => {
    setBusy(true);
    for (const item of ready) {
      patch(item.key, { state: "uploading", progress: 0 });
      try {
        await sendUpload(
          {
            file: item.file,
            kind: item.kind,
            song: item.song === NEW_SONG ? slugify(item.title) : item.song,
            song_title: item.song === NEW_SONG ? item.title.trim() : undefined,
            replaces: false,
            uploader: name,
            client_warnings: item.check?.warnings,
          },
          mode,
          (fraction) => patch(item.key, { progress: fraction }),
        );
        patch(item.key, { state: "done", progress: 1 });
      } catch (error) {
        patch(item.key, { state: "failed", error: (error as Error).message });
      }
    }
    setBusy(false);
    await refresh();
    setItems((current) => current.filter((item) => item.state !== "done"));
  };

  const problems = (uploads ?? []).filter((u) => u.report && u.report.verdict !== "ready");
  const live = isLive(heartbeat);

  return (
    <main className={`${styles.page} ${styles.narrow}`}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>Upload</p>
        <h1>Send in files</h1>
        <p>
          Vocals, beats, reference tracks — anything for a song. Each file is checked on the studio
          Mac before it is used, and anything wrong with it is listed here.
        </p>
      </header>

      <NameGate />

      <div
        className={styles.drop}
        data-over={over}
        role="button"
        tabIndex={0}
        aria-label="Choose files to upload"
        onClick={() => picker.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            picker.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          if (event.dataTransfer.files.length > 0) add(event.dataTransfer.files);
        }}
      >
        <strong>Drop files here, or tap to choose</strong>
        <span>WAV, AIFF, FLAC, MP3 or M4A · up to 500 MB each · as many as you like</span>
        <input
          ref={picker}
          className={styles.hiddenInput}
          type="file"
          multiple
          data-testid="file-picker"
          onChange={(event) => {
            if (event.target.files) add(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <>
          <div className={styles.queue}>
            {items.map((item) => (
              <div key={item.key} className={styles.queued}>
                <div className={styles.queuedHead}>
                  <span className={styles.fileName}>{item.file.name}</span>
                  <span className={`${styles.muted} ${styles.small}`}>
                    {(item.file.size / 1024 / 1024).toFixed(1)} MB
                    {item.check?.duration_s ? ` · ${clock(item.check.duration_s)}` : ""}
                    {item.state === "waiting" && (
                      <>
                        {" · "}
                        <button
                          className={styles.linkButton}
                          type="button"
                          onClick={() => setItems((current) => current.filter((i) => i.key !== item.key))}
                        >
                          Remove
                        </button>
                      </>
                    )}
                  </span>
                </div>

                {item.state === "waiting" && (
                  <div className={styles.queuedFields}>
                    <label>
                      What is it?
                      <select
                        className={styles.select}
                        value={item.kind}
                        onChange={(event) => setKind(item, event.target.value as UploadKind)}
                      >
                        {(Object.keys(KIND_COPY) as UploadKind[]).map((kind) => (
                          <option key={kind} value={kind}>
                            {KIND_COPY[kind]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Which song?
                      <select
                        className={styles.select}
                        value={item.song}
                        onChange={(event) => patch(item.key, { song: event.target.value, chosen: true })}
                      >
                        {songs.map((song) => (
                          <option key={song.slug} value={song.slug}>
                            {song.title}
                          </option>
                        ))}
                        <option value={NEW_SONG}>A new song…</option>
                      </select>
                    </label>
                    {item.song === NEW_SONG && (
                      <label>
                        Song title
                        <input
                          className={styles.input}
                          value={item.title}
                          maxLength={80}
                          onChange={(event) => patch(item.key, { title: event.target.value })}
                        />
                      </label>
                    )}
                  </div>
                )}

                {item.check === null && item.state === "waiting" && (
                  <span className={`${styles.muted} ${styles.small}`}>Checking…</span>
                )}
                {item.check && item.check.blockers.length + item.check.warnings.length > 0 && (
                  <ul className={styles.checks}>
                    {item.check.blockers.map((text) => (
                      <li key={text} data-tone="bad">
                        {text}
                      </li>
                    ))}
                    {item.check.warnings.map((text) => (
                      <li key={text}>{text}</li>
                    ))}
                  </ul>
                )}
                {item.state === "uploading" && (
                  <div className={styles.progress} role="progressbar" aria-valuenow={Math.round(item.progress * 100)}>
                    <div className={styles.progressFill} style={{ width: `${item.progress * 100}%` }} />
                  </div>
                )}
                {item.state === "failed" && (
                  <span className={styles.error} role="alert">
                    {item.error}{" "}
                    <button className={styles.linkButton} type="button" onClick={() => patch(item.key, { state: "waiting" })}>
                      Try again
                    </button>
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className={styles.queueBar}>
            <span className={`${styles.muted} ${styles.small}`}>
              {ready.length} of {items.length} ready to send
              {!name && " · say who you are first"}
            </span>
            <button
              className={styles.primary}
              type="button"
              disabled={ready.length === 0 || busy || !name}
              onClick={() => void sendAll()}
            >
              {busy ? "Uploading…" : `Upload ${ready.length} file${ready.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </>
      )}

      <div className={styles.sectionHead}>
        <h2>What&apos;s been sent</h2>
        {problems.length > 0 && (
          <button
            className={styles.linkButton}
            type="button"
            onClick={() => markProblemsSeen(problems.map((u) => u.id))}
          >
            Mark problems as seen
          </button>
        )}
      </div>
      <p className={styles.sectionLede}>
        {live
          ? "The studio Mac is on: new files are checked within about half a minute."
          : `The studio Mac is not syncing right now${
              heartbeat ? ` (last seen ${ago(heartbeat.at)})` : ""
            }. Files wait here until it is back.`}
      </p>

      {uploads === null ? (
        <p className={styles.muted}>Loading…</p>
      ) : uploads.length === 0 ? (
        <div className={styles.empty}>Nothing has been uploaded yet.</div>
      ) : (
        <div className={styles.uploads} aria-label="Uploaded files">
          {uploads.map((upload) => {
            const report = upload.report;
            const songSlug = report?.slug ?? slugify(upload.song);
            const known = songs.some((s) => s.slug === songSlug);
            return (
              <article key={upload.id} className={styles.upload} data-v={report?.verdict ?? "waiting"}>
                <div className={styles.uploadHead}>
                  <span className={styles.fileName}>{upload.filename}</span>
                  <span className={styles.verdict}>
                    {report ? report.verdict : "uploaded · awaiting check"}
                  </span>
                </div>
                <div className={styles.uploadMeta}>
                  {KIND_COPY[upload.kind]} for{" "}
                  {known ? <Link href={`/songs/${songSlug}`}>{upload.song_title ?? upload.song}</Link> : upload.song_title ?? upload.song}
                  {upload.replaces ? " (as a replacement)" : ""} · {upload.uploader} · {ago(upload.uploaded_at)}
                </div>
                {report && report.blockers.length + report.warnings.length > 0 && (
                  <ul className={styles.checks}>
                    {report.blockers.map((text) => (
                      <li key={text} data-tone="bad">
                        {text}
                      </li>
                    ))}
                    {report.warnings.map((text) => (
                      <li key={text}>{text}</li>
                    ))}
                  </ul>
                )}
                {report && report.verdict !== "blocked" && (report.placed || report.note) && (
                  <div className={styles.uploadMeta}>
                    {report.placed ? `In use as ${report.placed.split("/").slice(-2).join("/")}.` : report.note}
                    {report.rendered.length > 0 && ` Re-rendered ${report.rendered.join(", ")}.`}
                  </div>
                )}
                {report?.verdict === "blocked" && (
                  <div className={styles.uploadMeta}>Not used. Fix the problem above and upload it again.</div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </main>
  );
}
