"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { NameGate, useIdentity } from "@/components/Identity";
import { NoteBox } from "@/components/NoteBox";
import { ReplaceFile } from "@/components/ReplaceFile";
import { SettingsPanel } from "@/components/SettingsPanel";
import styles from "@/components/studio.module.css";
import { STATE_COPY, sectionColour } from "@/lib/compare";
import {
  ago,
  clock,
  targetKey,
  type Heartbeat,
  type Note,
  type NoteTarget,
  type SettingsRequest,
  type Song,
  type SongComponent,
} from "@/lib/song";
import { useSongAudio, type SongAudio } from "@/lib/useSongAudio";

type Payload = { song: Song; heartbeat: Heartbeat; notes: Note[]; requests: SettingsRequest[] };

const PlayIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="M4 2.5v11l9.5-5.5z" fill="currentColor" />
  </svg>
);
const PauseIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z" fill="currentColor" />
  </svg>
);

function figure(value: number | null | undefined, digits: number): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

export default function SongPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"blob" | "local">("local");
  const [section, setSection] = useState<number | null>(null);
  const audio = useSongAudio();

  const load = useCallback(
    () =>
      fetch(`/api/songs/${slug}`)
        .then((r) => {
          if (r.status === 404) throw new Error("There is no song here.");
          if (!r.ok) throw new Error(`Could not load the song (${r.status}).`);
          return r.json();
        })
        .then((next: Payload) => {
          setData(next);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [slug],
  );

  useEffect(() => {
    void load();
    fetch("/api/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((status) => status && setMode(status.mode))
      .catch(() => {});
    // Renders finish on another machine, so look again now and then.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 12_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (data) document.title = `${data.song.title} — MixMax studio`;
  }, [data]);

  // Show a note the moment it is saved, without waiting for the next poll.
  const onSaved = useCallback((note: Note | null, id: string) => {
    setData((current) => {
      if (!current) return current;
      const others = current.notes.filter((n) => n.id !== id);
      return { ...current, notes: note ? [note, ...others] : others };
    });
  }, []);

  if (error && !data) {
    return (
      <main className={styles.page}>
        <div className={styles.head}>
          <p className={styles.eyebrow}>
            <Link href="/">← Songs</Link>
          </p>
          <h1>Not found</h1>
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

  const { song, notes, requests, heartbeat } = data;
  const present = song.components.filter((c) => c.present);
  const playingComponent = present.find((c) => c.key === audio.current) ?? null;
  const timelineComponent = present.find((c) => c.key === song.timeline_component) ?? null;
  const notesFor = (target: NoteTarget) => notes.filter((n) => targetKey(n.target) === targetKey(target));

  return (
    <main className={styles.page}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>
          <Link href="/">← Songs</Link>
        </p>
        <h1>{song.title}</h1>
        <p>
          {song.kind === "vocal-only" ? "A vocal over a beat" : "Arrived as a finished mix"} ·{" "}
          {clock(song.progress.duration_s)} · {song.progress.percent}% finished
        </p>
      </header>

      <NameGate />

      <div className={styles.songTop}>
        <div className={styles.next}>
          <strong>What it needs next</strong>
          {song.progress.next_step}
        </div>
        <div className={styles.stages}>
          {song.progress.stages.map((stage) => (
            <div key={stage.key} className={styles.stage} data-s={stage.state}>
              <span className={styles.mark}>{STATE_COPY[stage.state].mark}</span>
              <span>
                <span className={styles.stageLabel}>{stage.label}</span>
                <span className={styles.stageDetail}>{stage.detail}</span>
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.sectionHead}>
        <h2>The pieces</h2>
      </div>
      <p className={styles.sectionLede}>
        Each stage of the song, in the order it is made. Play any of them and say what you hear —
        notes save as you type.
      </p>
      <div className={styles.components}>
        {song.components.map((component) => (
          <ComponentRow
            key={component.key}
            song={song}
            component={component}
            audio={audio}
            mode={mode}
            count={notesFor({ kind: "component", component: component.key }).length}
            onSaved={onSaved}
          />
        ))}
      </div>

      {timelineComponent && song.progress.arrangement.timeline.length > 0 && (
        <Arrangement
          song={song}
          component={timelineComponent}
          audio={audio}
          notes={notes}
          selected={section}
          onSelect={setSection}
          onSaved={onSaved}
        />
      )}

      <div className={styles.sectionHead}>
        <h2>Settings</h2>
      </div>
      <p className={styles.sectionLede}>
        The numbers this song is rendered with. Change any of them and the studio Mac re-renders it
        — nothing is processed in your browser.
      </p>
      <SettingsPanel song={song} requests={requests} heartbeat={heartbeat} onQueued={load} />

      <div className={styles.sectionHead}>
        <h2>Notes</h2>
        <span className={`${styles.muted} ${styles.small}`}>
          Kept in <span className={styles.code}>feedback.md</span> with the song
        </span>
      </div>
      <div className={styles.panel} style={{ marginBottom: 16 }}>
        <p className={styles.sectionTitle}>About the whole song</p>
        <NoteBox
          song={song}
          target={{ kind: "song" }}
          label="A note about the whole song"
          placeholder="What works, what doesn't, what should change next…"
          onSaved={onSaved}
        />
      </div>
      <NoteLog song={song} notes={notes} onSaved={onSaved} />

      <Transport component={playingComponent} audio={audio} />
    </main>
  );
}

function ComponentRow({
  song,
  component,
  audio,
  mode,
  count,
  onSaved,
}: {
  song: Song;
  component: SongComponent;
  audio: SongAudio;
  mode: "blob" | "local";
  count: number;
  onSaved: (note: Note | null, id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = audio.current === component.key;
  const playing = active && audio.playing;
  const m = component.measure;

  if (!component.present) {
    return (
      <div className={styles.component} data-missing="true">
        <div className={styles.componentTools}>
          <span>
            <span className={styles.componentName}>{component.label}</span> — none yet.{" "}
            {component.about}
          </span>
          <ReplaceFile slug={song.slug} component={component} mode={mode} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.component} data-playing={playing} role="group" aria-label={component.label}>
      <div className={styles.componentHead}>
        <button
          className={styles.play}
          type="button"
          aria-pressed={playing}
          aria-label={`${playing ? "Pause" : "Play"} ${component.label}`}
          onClick={() => audio.toggle(component)}
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <div>
          <div className={styles.componentName}>{component.label}</div>
          <div className={styles.componentAbout}>{component.about}</div>
        </div>
        <div className={styles.figures}>
          <div className={styles.figure}>
            <b>{figure(m?.lufs, 1)}</b>
            <span>LUFS</span>
          </div>
          <div className={styles.figure}>
            <b>{figure(m?.true_peak_dbtp, 1)}</b>
            <span>Peak dBTP</span>
          </div>
          <div className={styles.figure}>
            <b>{clock(m?.duration_s)}</b>
            <span>Length</span>
          </div>
        </div>
      </div>
      <div className={styles.componentTools}>
        <button className={styles.ghost} type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide note" : count > 0 ? `Add a note (${count} so far)` : "Add a note"}
        </button>
        {component.replace_as && <ReplaceFile slug={song.slug} component={component} mode={mode} />}
      </div>
      {open && (
        <NoteBox
          song={song}
          target={{ kind: "component", component: component.key }}
          label={`A note about ${component.label}`}
          placeholder={`What do you hear in the ${component.label.toLowerCase()}?`}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}

function Arrangement({
  song,
  component,
  audio,
  notes,
  selected,
  onSelect,
  onSaved,
}: {
  song: Song;
  component: SongComponent;
  audio: SongAudio;
  notes: Note[];
  selected: number | null;
  onSelect: (index: number | null) => void;
  onSaved: (note: Note | null, id: string) => void;
}) {
  const { timeline, transitions } = song.progress.arrangement;
  const total = timeline.reduce((sum, s) => sum + s.duration_s, 0) || 1;
  const counts = useMemo(() => {
    const map = new Map<number, number>();
    for (const note of notes) {
      if (note.target.kind === "section" && note.target.component === component.key) {
        map.set(note.target.index, (map.get(note.target.index) ?? 0) + 1);
      }
    }
    return map;
  }, [notes, component.key]);

  const chosen = selected != null ? timeline[selected] : null;
  const target: NoteTarget | null =
    chosen && selected != null
      ? {
          kind: "section",
          component: component.key,
          label: chosen.label,
          index: selected,
          start_s: chosen.start_s,
          end_s: chosen.start_s + chosen.duration_s,
        }
      : null;
  const showHead = audio.current === component.key && audio.duration > 0;

  return (
    <>
      <div className={styles.sectionHead}>
        <h2>Arrangement</h2>
        <span className={`${styles.muted} ${styles.small}`}>{component.label}</span>
      </div>
      <p className={styles.sectionLede}>
        Tap a section to jump to it and pin a note there — &ldquo;the bridge drags&rdquo; is more
        useful with a time attached.
      </p>
      <div className={styles.panel}>
        <div className={styles.timeline}>
          {timeline.map((part, index) => (
            <button
              key={index}
              type="button"
              className={styles.block}
              aria-pressed={selected === index}
              aria-label={`Section ${part.label}, ${clock(part.start_s)} to ${clock(part.start_s + part.duration_s)}`}
              style={{
                width: `${(part.duration_s / total) * 100}%`,
                background: sectionColour(part.label),
                // A breakdown reads as a gap in the bar: the low end is what left.
                opacity: 0.45 + 0.55 * Math.min(1, Math.max(0, (part.low_energy_db + 25) / 15)),
              }}
              onClick={() => {
                onSelect(index);
                audio.playAt(component, part.start_s);
              }}
            >
              {part.duration_s / total > 0.07 && <span className={styles.blockLabel}>{part.label}</span>}
              {(counts.get(index) ?? 0) > 0 && <span className={styles.blockNotes}>{counts.get(index)}</span>}
            </button>
          ))}
          {showHead && (
            <span
              className={styles.playhead}
              style={{ left: `${Math.min(100, (audio.time / total) * 100)}%` }}
            />
          )}
        </div>
        <div className={styles.ruler}>
          <span>0:00</span>
          <span>
            {song.progress.arrangement.sections} sections · {song.progress.arrangement.types} types
          </span>
          <span>{clock(total)}</span>
        </div>
        <div className={styles.markers}>
          {transitions.length === 0 ? (
            <span className={styles.marker}>no transitions</span>
          ) : (
            transitions.map((t, i) => (
              <span key={i} className={styles.marker} data-k={t.kind}>
                {t.kind} @ {clock(t.at_s)}
              </span>
            ))
          )}
        </div>

        {target && chosen && (
          <div className={styles.sectionNote}>
            <div className={styles.sectionNoteHead}>
              <span>
                <strong>Section {chosen.label}</strong> · {clock(target.start_s)}–{clock(target.end_s)}
              </span>
              <button className={styles.linkButton} type="button" onClick={() => onSelect(null)}>
                Close
              </button>
            </div>
            <NoteBox
              key={selected}
              song={song}
              target={target}
              label={`A note about section ${chosen.label} at ${clock(target.start_s)}`}
              placeholder="What happens here that should change?"
              onSaved={onSaved}
            />
          </div>
        )}
      </div>
    </>
  );
}

function NoteLog({
  song,
  notes,
  onSaved,
}: {
  song: Song;
  notes: Note[];
  onSaved: (note: Note | null, id: string) => void;
}) {
  const { name } = useIdentity();
  const labels = useMemo(() => new Map(song.components.map((c) => [c.key, c.label])), [song]);

  const remove = async (note: Note) => {
    const response = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: note.slug, id: note.id, text: "" }),
    });
    if (response.ok) onSaved(null, note.id);
  };

  if (notes.length === 0) {
    return <div className={styles.empty}>Nobody has left a note on this song yet.</div>;
  }

  return (
    <div className={styles.log} aria-label="Notes on this song">
      {notes.map((note) => {
        const target = note.target;
        return (
          <article key={note.id} className={styles.logItem}>
            <div className={styles.logHead}>
              <b>{note.author}</b>
              <span>{ago(note.updated_at)}</span>
              {target.kind === "component" && (
                <span className={styles.tag} data-k="component">
                  {labels.get(target.component) ?? target.component}
                </span>
              )}
              {target.kind === "section" && (
                <span className={styles.tag} data-k="section">
                  Section {target.label} · {clock(target.start_s)}
                </span>
              )}
              {target.kind === "song" && <span className={styles.tag}>Whole song</span>}
              {note.author === name && (
                <button className={styles.linkButton} type="button" onClick={() => void remove(note)}>
                  Delete
                </button>
              )}
            </div>
            <p className={styles.logText}>{note.text}</p>
          </article>
        );
      })}
    </div>
  );
}

function Transport({ component, audio }: { component: SongComponent | null; audio: SongAudio }) {
  if (!component) return null;
  const length = audio.duration || component.measure?.duration_s || 0;
  return (
    <div className={styles.transport} role="region" aria-label="Now playing">
      <div className={styles.transportInner}>
        <button
          className={styles.play}
          type="button"
          aria-pressed={audio.playing}
          aria-label={audio.playing ? "Pause" : "Play"}
          onClick={() => audio.toggle(component)}
        >
          {audio.playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <div className={styles.transportInfo}>
          <b>{audio.error ?? component.label}</b>
          <span className={styles.transportTime}>
            {clock(audio.time)} / {clock(length)}
          </span>
        </div>
        <input
          className={styles.seek}
          type="range"
          aria-label="Position"
          min={0}
          max={length || 1}
          step={0.1}
          value={Math.min(audio.time, length || 1)}
          onChange={(event) => audio.seek(Number(event.target.value))}
        />
      </div>
    </div>
  );
}
