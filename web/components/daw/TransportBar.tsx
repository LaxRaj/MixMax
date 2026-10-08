"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { loopAll } from "@/lib/daw/edit";
import { barBeat, beatsToSeconds } from "@/lib/daw/schedule";
import { BEATS_PER_BAR, LIMITS } from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import styles from "./daw.module.css";

const SAVE_COPY = {
  saved: "Saved",
  dirty: "Unsaved",
  saving: "Saving…",
  error: "Not saved — retry",
  conflict: "Changed elsewhere",
} as const;

function clockMs(seconds: number): string {
  const whole = Math.max(0, seconds);
  return `${Math.floor(whole / 60)}:${(whole % 60).toFixed(1).padStart(4, "0")}`;
}

/** Call `draw` with the playhead every frame. Writes to the DOM directly: 60 React renders a second is too many. */
export function usePlayhead(draw: (beat: number) => void): void {
  const ws = useWs();
  const latest = useRef(draw);
  latest.current = draw;
  const position = ws.position;
  useEffect(() => {
    let frame = 0;
    const loop = () => {
      latest.current(position());
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [position]);
}

export function TransportBar({ onAdd, onExport }: { onAdd: () => void; onExport: () => void }) {
  const ws = useWs();
  const { project } = ws;
  const readout = useRef<HTMLSpanElement>(null);
  const time = useRef<HTMLSpanElement>(null);
  const taps = useRef<number[]>([]);
  const [bpmDraft, setBpmDraft] = useState<string | null>(null);

  usePlayhead((beat) => {
    if (readout.current) readout.current.textContent = barBeat(beat);
    if (time.current) time.current.textContent = clockMs(beatsToSeconds(beat, project.bpm));
  });

  const setBpm = (value: number) => {
    if (!Number.isFinite(value)) return;
    const bpm = Math.min(LIMITS.bpm[1], Math.max(LIMITS.bpm[0], Math.round(value * 10) / 10));
    ws.apply((p) => (p.bpm === bpm ? p : { ...p, bpm }), "bpm");
  };

  const tap = () => {
    const now = performance.now();
    const recent = taps.current.filter((t) => now - t < 2500);
    recent.push(now);
    taps.current = recent.slice(-6);
    if (taps.current.length >= 3) {
      const gaps = taps.current.slice(1).map((t, i) => t - taps.current[i]);
      setBpm(60000 / (gaps.reduce((a, b) => a + b, 0) / gaps.length));
    }
  };

  const setLoop = (patch: Partial<typeof project.loop>) =>
    ws.apply((p) => {
      const loop = { ...p.loop, ...patch };
      if (loop.end <= loop.start) loop.end = loop.start + BEATS_PER_BAR;
      return { ...p, loop };
    }, "loop");

  return (
    <div className={styles.transport} role="toolbar" aria-label="Transport">
      <div className={styles.transportGroup}>
        <Link href="/produce" className={styles.back} aria-label="All projects">
          ←
        </Link>
        <input
          className={styles.title}
          aria-label="Project name"
          value={project.name}
          maxLength={80}
          onChange={(event) => ws.apply((p) => ({ ...p, name: event.target.value }), "name")}
        />
      </div>

      <div className={styles.transportGroup}>
        <button
          className={styles.playButton}
          aria-pressed={ws.playing}
          aria-label={ws.playing ? "Stop" : "Play"}
          title="Play / stop (Space)"
          onClick={ws.toggle}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            {ws.playing ? <path d="M3.5 3.5h9v9h-9z" fill="currentColor" /> : <path d="M4 2.5v11l9.5-5.5z" fill="currentColor" />}
          </svg>
        </button>
        <button
          className={styles.iconButton}
          aria-label="Back to the start"
          title="Back to the start (Enter)"
          onClick={() => ws.seek(project.loop.on ? project.loop.start : 0)}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 3h2v10H3zM13 3v10L6 8z" fill="currentColor" />
          </svg>
        </button>
        <button
          className={styles.iconButton}
          data-tone="record"
          aria-pressed={ws.recording}
          aria-label={ws.recording ? "Stop recording" : "Record"}
          title="Record the microphone onto the selected audio track, or a new one"
          onClick={() => void ws.record()}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="5" fill="currentColor" />
          </svg>
        </button>
        <span className={styles.readout} aria-label="Position">
          <span ref={readout} className="mono" data-testid="position">1.1.1</span>
          <span ref={time} className={`mono ${styles.readoutTime}`}>0:00.0</span>
        </span>
      </div>

      <div className={styles.transportGroup}>
        <label className={styles.field}>
          <span>BPM</span>
          <input
            className={`${styles.number} mono`}
            type="number"
            inputMode="decimal"
            min={LIMITS.bpm[0]}
            max={LIMITS.bpm[1]}
            step={1}
            aria-label="Tempo in beats per minute"
            value={bpmDraft ?? String(project.bpm)}
            onChange={(event) => {
              setBpmDraft(event.target.value);
              const value = Number(event.target.value);
              // Typing "9" on the way to "92" must not snap to the minimum.
              if (value >= LIMITS.bpm[0] && value <= LIMITS.bpm[1]) setBpm(value);
            }}
            onBlur={() => setBpmDraft(null)}
          />
        </label>
        <button className={styles.chip} onClick={tap} title="Tap along to set the tempo">
          Tap
        </button>
        <label className={styles.field} title="Pushes every other sixteenth later, for a shuffled feel">
          <span>Swing</span>
          <input
            className={styles.miniRange}
            type="range"
            min={0}
            max={1}
            step={0.05}
            aria-label="Swing"
            value={project.swing}
            onChange={(event) => ws.apply((p) => ({ ...p, swing: Number(event.target.value) }), "swing")}
          />
        </label>
        <button
          className={styles.chip}
          aria-pressed={project.metronome}
          onClick={() => ws.apply((p) => ({ ...p, metronome: !p.metronome }))}
          title="Click on every beat. Never part of an export."
        >
          Click
        </button>
      </div>

      <div className={styles.transportGroup}>
        <button
          className={styles.chip}
          aria-pressed={project.loop.on}
          onClick={() => setLoop({ on: !project.loop.on })}
          title="Repeat the bars between the two numbers (L)"
        >
          Loop
        </button>
        <label className={styles.field}>
          <span>bars</span>
          <input
            className={`${styles.number} ${styles.narrow} mono`}
            type="number"
            min={1}
            aria-label="Loop from bar"
            value={project.loop.start / BEATS_PER_BAR + 1}
            onChange={(event) => {
              const bar = Math.max(1, Math.floor(Number(event.target.value) || 1));
              setLoop({ start: (bar - 1) * BEATS_PER_BAR });
            }}
          />
          <span>–</span>
          <input
            className={`${styles.number} ${styles.narrow} mono`}
            type="number"
            min={1}
            aria-label="Loop to bar"
            value={project.loop.end / BEATS_PER_BAR}
            onChange={(event) => {
              const bar = Math.max(1, Math.floor(Number(event.target.value) || 1));
              setLoop({ end: bar * BEATS_PER_BAR });
            }}
          />
        </label>
        <button className={styles.chip} onClick={() => ws.apply(loopAll)} title="Loop everything on the timeline">
          All
        </button>
      </div>

      <div className={`${styles.transportGroup} ${styles.transportEnd}`}>
        <button className={styles.iconButton} onClick={ws.undo} disabled={!ws.canUndo} aria-label="Undo" title="Undo (⌘Z)">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6.5 3 3 6.5 6.5 10M3.5 6.5H10a3 3 0 0 1 0 6H7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button className={styles.iconButton} onClick={ws.redo} disabled={!ws.canRedo} aria-label="Redo" title="Redo (⇧⌘Z)">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M9.5 3 13 6.5 9.5 10M12.5 6.5H6a3 3 0 0 0 0 6h3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button
          className={styles.saveState}
          data-s={ws.save}
          onClick={ws.saveNow}
          title={ws.saveMessage ?? "Projects save themselves a moment after each change."}
          data-testid="save-state"
        >
          {SAVE_COPY[ws.save]}
        </button>
        <button className={styles.chip} data-tone="add" onClick={onAdd}>
          + Track
        </button>
        <button className={styles.export} onClick={onExport}>
          Export
        </button>
      </div>
    </div>
  );
}
