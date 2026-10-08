"use client";

import { useEffect, useRef, useState } from "react";
import { addDrumTrack, addEmptyAudioTrack, addSynthTrack } from "@/lib/daw/edit";
import { DRUM_PRESETS, SYNTH_PRESETS, type Project } from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import type { Song, SongSummary } from "@/lib/song";
import styles from "./daw.module.css";

/** Select whatever track the edit just added. */
function newest(before: Project, after: Project): string | null {
  const known = new Set(before.tracks.map((t) => t.id));
  return after.tracks.find((t) => !known.has(t.id))?.id ?? null;
}

export function AddTrack({ onClose }: { onClose: () => void }) {
  const ws = useWs();
  const picker = useRef<HTMLInputElement>(null);
  const [songs, setSongs] = useState<SongSummary[] | null>(null);
  const [open, setOpen] = useState<Song | null>(null);

  useEffect(() => {
    fetch("/api/songs")
      .then((r) => (r.ok ? r.json() : { songs: [] }))
      .then((data) => setSongs(data.songs ?? []))
      .catch(() => setSongs([]));
  }, []);

  const add = (edit: Parameters<typeof ws.apply>[0]) => {
    const before = ws.project;
    const id = newest(before, ws.apply(edit));
    if (id) ws.select(id);
    onClose();
  };

  const openSong = (slug: string) =>
    fetch(`/api/songs/${slug}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => data && setOpen(data.song))
      .catch(() => {});

  return (
    <div className={styles.scrim} onPointerDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={styles.dialog} role="dialog" aria-label="Add a track" aria-modal="true">
        <header className={styles.dialogHead}>
          <h2>Add a track</h2>
          <button className={styles.iconButton} onClick={onClose} aria-label="Close">×</button>
        </header>

        <section className={styles.choice}>
          <h3>Drums</h3>
          <p>A drum machine with a step grid. Pick a beat to start from and change it.</p>
          <div className={styles.choices}>
            {Object.keys(DRUM_PRESETS).map((name) => (
              <button key={name} className={styles.chip} onClick={() => add(addDrumTrack(name))}>
                {name}
              </button>
            ))}
          </div>
        </section>

        <section className={styles.choice}>
          <h3>Instrument</h3>
          <p>A synth you write parts for on a piano roll.</p>
          <div className={styles.choices}>
            {Object.keys(SYNTH_PRESETS).map((name) => (
              <button key={name} className={styles.chip} onClick={() => add(addSynthTrack(name))}>
                {name}
              </button>
            ))}
          </div>
        </section>

        <section className={styles.choice}>
          <h3>Audio</h3>
          <p>A file from this device, or an empty track to record the microphone onto.</p>
          <div className={styles.choices}>
            <button className={styles.chip} onClick={() => picker.current?.click()}>
              Import a file…
            </button>
            <button className={styles.chip} onClick={() => add(addEmptyAudioTrack())}>
              Empty track for recording
            </button>
            <input
              ref={picker}
              type="file"
              accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.aif,.aiff"
              className={styles.hiddenInput}
              aria-label="Import an audio file"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                onClose();
                void ws.importFile(file);
              }}
            />
          </div>
        </section>

        <section className={styles.choice}>
          <h3>From a song</h3>
          <p>
            Bring a song&apos;s vocal, beat or master in to build around. These are the streaming copies the
            studio published — right for working against, not for sending back.
          </p>
          {songs === null ? (
            <p className={styles.hint}>Loading…</p>
          ) : songs.length === 0 ? (
            <p className={styles.hint}>No songs have been published by the studio Mac yet.</p>
          ) : (
            <div className={styles.choices}>
              {songs.map((song) => (
                <button key={song.slug} className={styles.chip} aria-pressed={open?.slug === song.slug} onClick={() => void openSong(song.slug)}>
                  {song.title}
                </button>
              ))}
            </div>
          )}
          {open && (
            <div className={styles.choices} aria-label={`Pieces of ${open.title}`}>
              {open.components.filter((c) => c.present && c.audio).map((component) => (
                <button
                  key={component.key}
                  className={styles.chip}
                  data-tone="add"
                  onClick={() => {
                    onClose();
                    void ws.addSongAudio(`${open.title} — ${component.label}`, component.audio!, open.slug);
                  }}
                >
                  + {component.label}
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
