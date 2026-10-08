"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useIdentity } from "@/components/Identity";
import { encodeWav, type Bounce } from "@/lib/daw/render";
import { audible } from "@/lib/daw/schedule";
import type { Track } from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { clock, slugify, type SongSummary, type UploadKind } from "@/lib/song";
import { sendUpload } from "@/lib/uploadClient";
import styles from "./daw.module.css";

const NEW_SONG = "__new__";

type Sent = { song: string; kind: UploadKind; replaces: boolean };

export function ExportDialog({ onClose }: { onClose: () => void }) {
  const ws = useWs();
  const { project } = ws;
  const { name } = useIdentity();

  /** A track made only of a song's published audio — the thing not to send back. */
  const borrowed = useMemo(() => {
    const fromSong = new Set(project.media.filter((m) => m.from === "song").map((m) => m.id));
    return (track: Track) => track.kind === "audio" && track.clips.length > 0 && track.clips.every((c) => fromSong.has(c.media));
  }, [project.media]);

  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(project.tracks.filter((t) => audible(t, project) && !borrowed(t)).map((t) => t.id)),
  );
  const [range, setRange] = useState<"all" | "loop">("all");
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ bounce: Bounce; blob: Blob } | null>(null);

  const [songs, setSongs] = useState<SongSummary[]>([]);
  const [song, setSong] = useState(NEW_SONG);
  const [title, setTitle] = useState(project.name);
  const [kind, setKind] = useState<UploadKind>("beat");
  const [replaces, setReplaces] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [sent, setSent] = useState<Sent | null>(null);

  useEffect(() => {
    fetch("/api/songs")
      .then((r) => (r.ok ? r.json() : { songs: [] }))
      .then((data) => setSongs(data.songs ?? []))
      .catch(() => {});
  }, []);

  // Any change to what is being exported makes the last render stale.
  const invalidate = () => {
    setResult(null);
    setSent(null);
    setError(null);
  };

  const render = async (): Promise<{ bounce: Bounce; blob: Blob } | null> => {
    if (result) return result;
    setWorking("Rendering…");
    setError(null);
    try {
      const bounce = await ws.bounce({
        tracks: chosen,
        ...(range === "loop" ? { from: project.loop.start, to: project.loop.end } : {}),
      });
      const made = { bounce, blob: encodeWav(bounce.buffer) };
      setResult(made);
      return made;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setWorking(null);
    }
  };

  const fileName = `${slugify(project.name)}.wav`;

  const download = async () => {
    const made = await render();
    if (!made) return;
    const url = URL.createObjectURL(made.blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  };

  const send = async () => {
    if (!name) {
      setError("Type your name at the top of the page first, so the studio knows who sent this.");
      return;
    }
    const isNew = song === NEW_SONG;
    if (isNew && !title.trim()) {
      setError("Give the new song a name.");
      return;
    }
    const made = await render();
    if (!made) return;
    setWorking("Sending…");
    setProgress(0);
    try {
      await sendUpload(
        {
          file: new File([made.blob], fileName, { type: "audio/wav" }),
          kind,
          song: isNew ? slugify(title) : song,
          song_title: isNew ? title.trim() : undefined,
          replaces: !isNew && replaces,
          uploader: name,
        },
        ws.mode,
        setProgress,
      );
      setSent({ song: isNew ? title.trim() : (songs.find((s) => s.slug === song)?.title ?? song), kind, replaces: !isNew && replaces });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(null);
      setProgress(null);
    }
  };

  const leftOut = project.tracks.filter((t) => !audible(t, project));

  return (
    <div className={styles.scrim} onPointerDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={styles.dialog} role="dialog" aria-label="Export" aria-modal="true">
        <header className={styles.dialogHead}>
          <h2>Export</h2>
          <button className={styles.iconButton} onClick={onClose} aria-label="Close">×</button>
        </header>

        <section className={styles.choice}>
          <h3>What goes in</h3>
          <div className={styles.checks}>
            {project.tracks.map((track) => {
              const silent = !audible(track, project);
              return (
                <label key={track.id} className={styles.check} data-off={silent}>
                  <input
                    type="checkbox"
                    disabled={silent}
                    checked={!silent && chosen.has(track.id)}
                    onChange={(event) => {
                      const next = new Set(chosen);
                      if (event.target.checked) next.add(track.id);
                      else next.delete(track.id);
                      setChosen(next);
                      invalidate();
                    }}
                  />
                  <span>
                    {track.name}
                    {silent && <em> — muted</em>}
                    {borrowed(track) && !silent && <em> — a song&apos;s streaming copy</em>}
                  </span>
                </label>
              );
            })}
          </div>
          {project.tracks.some((t) => borrowed(t)) && (
            <p className={styles.hint}>
              Tracks brought in from a song start unticked. They are lossy copies; the studio Mac has the
              originals and will mix your new parts with those.
            </p>
          )}
          {leftOut.length > 0 && <p className={styles.hint}>Muted tracks, and tracks silenced by a solo, are never exported.</p>}
          <div className={styles.choices}>
            <label className={styles.check}>
              <input type="radio" name="range" checked={range === "all"} onChange={() => { setRange("all"); invalidate(); }} />
              <span>The whole timeline</span>
            </label>
            <label className={styles.check}>
              <input type="radio" name="range" checked={range === "loop"} onChange={() => { setRange("loop"); invalidate(); }} />
              <span>Only the loop (bars {project.loop.start / 4 + 1}–{project.loop.end / 4})</span>
            </label>
          </div>
        </section>

        <section className={styles.choice}>
          <h3>Save it</h3>
          <div className={styles.choices}>
            <button className={styles.export} onClick={() => void download()} disabled={working !== null || chosen.size === 0}>
              Download WAV
            </button>
          </div>
          {result && (
            <p className={styles.hint} data-testid="bounce-facts">
              {clock(result.bounce.duration_s)} long, 24-bit, 44.1 kHz. Peak{" "}
              <span className="mono">{result.bounce.peak_db.toFixed(1)} dBFS</span>
              {result.bounce.trimmed_db > 0 &&
                ` — the mix was turned down ${result.bounce.trimmed_db.toFixed(1)} dB as a whole so it does not clip.`}
            </p>
          )}
        </section>

        <section className={styles.choice}>
          <h3>Send it to the studio</h3>
          <p>
            It goes through the same file check as any upload. Loudness and mastering are still the
            pipeline&apos;s job, on the studio Mac.
          </p>
          <div className={styles.formRow}>
            <label className={styles.field}>
              <span>As</span>
              <select className={styles.tinySelect} aria-label="Send as" value={kind} onChange={(event) => { setKind(event.target.value as UploadKind); setSent(null); }}>
                <option value="beat">A beat</option>
                <option value="vocal">A vocal</option>
                <option value="reference">A reference</option>
                <option value="other">Something else</option>
              </select>
            </label>
            <label className={styles.field}>
              <span>For</span>
              <select className={styles.tinySelect} aria-label="For which song" value={song} onChange={(event) => { setSong(event.target.value); setSent(null); }}>
                <option value={NEW_SONG}>A new song…</option>
                {songs.map((s) => (
                  <option key={s.slug} value={s.slug}>{s.title}</option>
                ))}
              </select>
            </label>
            {song === NEW_SONG && (
              <input
                className={styles.patternName}
                aria-label="New song name"
                placeholder="Song name"
                value={title}
                maxLength={80}
                onChange={(event) => setTitle(event.target.value)}
              />
            )}
          </div>
          {song !== NEW_SONG && kind !== "other" && (
            <label className={styles.check}>
              <input type="checkbox" checked={replaces} onChange={(event) => setReplaces(event.target.checked)} />
              <span>
                Replace the song&apos;s current {kind} with this, and re-render everything built on it.{" "}
                <em>Unticked, the file is kept beside the song but not used.</em>
              </span>
            </label>
          )}
          <div className={styles.choices}>
            <button className={styles.export} onClick={() => void send()} disabled={working !== null || chosen.size === 0 || sent !== null}>
              Send to the studio
            </button>
            {working && (
              <span className={styles.hint} role="status">
                {working}
                {progress !== null && ` ${Math.round(progress * 100)}%`}
              </span>
            )}
          </div>
          {sent && (
            <p className={styles.sent} role="status">
              Sent as {sent.kind === "other" ? "a file" : `a ${sent.kind}`} for <strong>{sent.song}</strong>. The
              studio Mac checks it on its next sync — <Link href="/upload">see the result on Upload</Link>.
            </p>
          )}
        </section>

        {error && <p className={styles.dialogError} role="alert">{error}</p>}
      </div>
    </div>
  );
}
