"use client";

import { useEffect, useRef, useState } from "react";
import { addNote, removeNote, setPatternLength, updateNote, updateTrack } from "@/lib/daw/edit";
import { snap } from "@/lib/daw/schedule";
import {
  BEATS_PER_BAR,
  STEP_BEATS,
  SYNTH_PRESETS,
  WAVES,
  synthPreset,
  type Note,
  type SynthParams,
  type SynthTrack,
  type Wave,
} from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { PatternBar } from "./StepSequencer";
import { usePlayhead } from "./TransportBar";
import styles from "./daw.module.css";

const LOW = 24; // C1
const HIGH = 96; // C7
const ROW = 14;
const BEAT = 88;
const NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const BLACK = new Set([1, 3, 6, 8, 10]);

export const noteName = (pitch: number): string => `${NAMES[pitch % 12]}${Math.floor(pitch / 12) - 1}`;

const SLIDERS: { key: keyof SynthParams; label: string; min: number; max: number; step: number; help: string }[] = [
  { key: "attack", label: "Attack", min: 0.001, max: 1.5, step: 0.001, help: "How quickly a note reaches full level" },
  { key: "decay", label: "Decay", min: 0.02, max: 3, step: 0.01, help: "How long it takes to settle after the attack" },
  { key: "sustain", label: "Sustain", min: 0, max: 1, step: 0.01, help: "The level a held note settles at" },
  { key: "release", label: "Release", min: 0.02, max: 3, step: 0.01, help: "How long it rings after the note ends" },
  { key: "cutoff", label: "Cutoff", min: 80, max: 12000, step: 10, help: "Lower is darker" },
  { key: "resonance", label: "Resonance", min: 0.1, max: 16, step: 0.1, help: "Emphasis at the cutoff" },
  { key: "env", label: "Filter env", min: 0, max: 5, step: 0.1, help: "How far each note opens the filter, in octaves" },
  { key: "octave", label: "Octave", min: -3, max: 3, step: 1, help: "Shift every note" },
  { key: "voices", label: "Voices", min: 1, max: 3, step: 1, help: "Stacked oscillators" },
  { key: "detune", label: "Detune", min: 0, max: 40, step: 1, help: "Spread between stacked voices, in cents" },
  { key: "sub", label: "Sub", min: 0, max: 1, step: 0.01, help: "A sine an octave below" },
  { key: "drop", label: "Pitch drop", min: 0, max: 24, step: 1, help: "Semitones each note falls from — the 808 thump" },
  { key: "drive", label: "Drive", min: 0, max: 1, step: 0.01, help: "Saturation" },
];

function SynthPanel({ track }: { track: SynthTrack }) {
  const ws = useWs();
  const { synth } = track;
  const set = (patch: Partial<SynthParams>) =>
    ws.apply(updateTrack(track.id, { synth: { ...synth, ...patch } } as Partial<SynthTrack>), `synth:${track.id}:${Object.keys(patch)[0]}`);

  return (
    <div className={styles.synth}>
      <label className={styles.field}>
        <span>Sound</span>
        <select
          className={styles.tinySelect}
          aria-label="Synth sound"
          value={SYNTH_PRESETS[synth.preset] ? synth.preset : ""}
          onChange={(event) => event.target.value && ws.apply(updateTrack(track.id, { synth: synthPreset(event.target.value) } as Partial<SynthTrack>))}
        >
          {!SYNTH_PRESETS[synth.preset] && <option value="">Custom</option>}
          {Object.keys(SYNTH_PRESETS).map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Wave</span>
        <select className={styles.tinySelect} aria-label="Waveform" value={synth.wave} onChange={(event) => set({ wave: event.target.value as Wave })}>
          {WAVES.map((wave) => (
            <option key={wave} value={wave}>{wave}</option>
          ))}
        </select>
      </label>
      {SLIDERS.map((slider) => (
        <label key={slider.key} className={styles.knob} title={slider.help}>
          <span>{slider.label}</span>
          <input
            type="range"
            className={styles.miniRange}
            min={slider.min}
            max={slider.max}
            step={slider.step}
            aria-label={slider.label}
            value={synth[slider.key] as number}
            onChange={(event) => set({ [slider.key]: Number(event.target.value) })}
          />
        </label>
      ))}
    </div>
  );
}

export function PianoRoll({ track }: { track: SynthTrack }) {
  const ws = useWs();
  const patternId = ws.patternOf(track.id);
  const pattern = track.patterns.find((p) => p.id === patternId);
  const [selected, setSelected] = useState<string | null>(null);
  const lastLength = useRef(0.5);
  const scroller = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const cursor = useRef<HTMLDivElement>(null);
  const byTouch = useRef(false);

  // Open on the notes that are there, or around middle C if there are none.
  useEffect(() => {
    const box = scroller.current;
    if (!box || !pattern) return;
    const pitches = pattern.notes.map((n) => n.pitch);
    const centre = pitches.length ? (Math.min(...pitches) + Math.max(...pitches)) / 2 : 60;
    box.scrollTop = Math.max(0, (HIGH - centre) * ROW - box.clientHeight / 2);
    // Only when a different pattern is opened, not on every edit to it.
  }, [pattern?.id]);

  usePlayhead((beat) => {
    const element = cursor.current;
    if (!element || !pattern) return;
    const clip = ws.playing
      ? track.clips.find((c) => c.pattern === pattern.id && beat >= c.start && beat < c.start + c.length)
      : undefined;
    element.style.opacity = clip ? "1" : "0";
    if (clip) element.style.transform = `translateX(${(((beat - (clip.start - clip.offset)) % pattern.length) * BEAT).toFixed(1)}px)`;
  });

  if (!pattern) return null;
  const rows = HIGH - LOW + 1;

  const at = (event: { clientX: number; clientY: number }) => {
    const box = grid.current!.getBoundingClientRect();
    return {
      beat: (event.clientX - box.left) / BEAT,
      pitch: Math.max(LOW, Math.min(HIGH, HIGH - Math.floor((event.clientY - box.top) / ROW))),
    };
  };

  const drag = (move: (event: PointerEvent) => void) => {
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  /** Press on empty grid: a note appears, and dragging right before letting go sets its length. */
  const draw = (event: React.PointerEvent | React.MouseEvent, touch = false) => {
    // On a touchscreen a press is also how you scroll, so a note waits for a finished tap.
    if ("pointerType" in event) {
      byTouch.current = event.pointerType === "touch";
      if (byTouch.current) return;
    } else if (!touch) return;
    if (event.button !== 0) return;
    const { beat, pitch } = at(event);
    const start = Math.floor(beat / STEP_BEATS) * STEP_BEATS;
    if (start >= pattern.length) return;
    let id = "";
    ws.apply(addNote(track.id, pattern.id, { pitch, start, length: lastLength.current, velocity: 0.8 }, (made) => (id = made)));
    if (!id) return;
    setSelected(id);
    ws.previewNote(track.id, pitch);
    if (touch) return;
    const key = `draw:${id}`;
    drag((e) => {
      const length = Math.max(STEP_BEATS, snap(at(e).beat - start, STEP_BEATS));
      lastLength.current = length;
      ws.apply(updateNote(track.id, pattern.id, id, { length }), key);
    });
  };

  const grab = (event: React.PointerEvent, note: Note, mode: "move" | "length") => {
    if (event.button !== 0) return;
    event.stopPropagation();
    setSelected(note.id);
    const origin = at(event);
    const key = `note:${note.id}:${mode}:${Date.now()}`;
    let heard = note.pitch;
    drag((e) => {
      const here = at(e);
      if (mode === "length") {
        const length = Math.max(STEP_BEATS, snap(note.length + here.beat - origin.beat, STEP_BEATS));
        lastLength.current = length;
        ws.apply(updateNote(track.id, pattern.id, note.id, { length }), key);
        return;
      }
      const pitch = note.pitch + (here.pitch - origin.pitch);
      ws.apply(updateNote(track.id, pattern.id, note.id, { pitch, start: snap(note.start + here.beat - origin.beat, STEP_BEATS) }), key);
      if (pitch !== heard) {
        heard = pitch;
        ws.previewNote(track.id, pitch);
      }
    });
  };

  const remove = (id: string) => {
    ws.apply(removeNote(track.id, pattern.id, id));
    setSelected(null);
  };

  return (
    <div className={styles.editor}>
      <PatternBar track={track}>
        <label className={styles.field}>
          <span>Length</span>
          <select
            className={styles.tinySelect}
            aria-label="Pattern length"
            value={pattern.length}
            onChange={(event) => ws.apply(setPatternLength(track.id, pattern.id, Number(event.target.value)))}
          >
            {[1, 2, 4, 8].map((bars) => (
              <option key={bars} value={bars * BEATS_PER_BAR}>{bars} bar{bars > 1 ? "s" : ""}</option>
            ))}
          </select>
        </label>
        <button className={styles.chip} disabled={!selected} onClick={() => selected && remove(selected)}>
          Delete note
        </button>
      </PatternBar>

      <div
        className={styles.roll}
        ref={scroller}
        tabIndex={0}
        aria-label="Piano roll"
        onKeyDown={(event) => {
          if ((event.key === "Delete" || event.key === "Backspace") && selected) {
            event.preventDefault();
            event.stopPropagation();
            remove(selected);
          }
        }}
      >
        <div className={styles.rollInner} style={{ height: rows * ROW, width: `calc(48px + ${pattern.length * BEAT}px)` }}>
          <div className={styles.keys}>
            {Array.from({ length: rows }, (_, i) => HIGH - i).map((pitch) => (
              <button
                key={pitch}
                className={styles.key}
                data-black={BLACK.has(pitch % 12)}
                style={{ height: ROW }}
                aria-label={`Key ${noteName(pitch)}`}
                tabIndex={-1}
                onPointerDown={() => ws.previewNote(track.id, pitch)}
              >
                {pitch % 12 === 0 ? noteName(pitch) : ""}
              </button>
            ))}
          </div>
          <div
            className={styles.rollGrid}
            ref={grid}
            data-testid="piano-grid"
            style={{ width: pattern.length * BEAT, ["--row" as string]: `${ROW}px`, ["--beat" as string]: `${BEAT}px` }}
            onPointerDown={draw}
            onClick={(event) => byTouch.current && event.target === event.currentTarget && draw(event, true)}
          >
            {Array.from({ length: rows }, (_, i) => HIGH - i)
              .filter((pitch) => BLACK.has(pitch % 12))
              .map((pitch) => (
                <span key={pitch} className={styles.blackRow} style={{ top: (HIGH - pitch) * ROW, height: ROW }} />
              ))}
            {pattern.notes.map((note) => (
              <div
                key={note.id}
                className={styles.note}
                role="button"
                aria-label={`${noteName(note.pitch)} at beat ${note.start + 1}`}
                aria-pressed={selected === note.id}
                style={{
                  top: (HIGH - note.pitch) * ROW,
                  left: note.start * BEAT,
                  width: Math.max(6, note.length * BEAT - 1),
                  height: ROW - 1,
                  ["--c" as string]: track.color,
                }}
                onPointerDown={(event) => grab(event, note, "move")}
                onDoubleClick={() => remove(note.id)}
              >
                <span className={styles.noteEdge} onPointerDown={(event) => grab(event, note, "length")} />
              </div>
            ))}
            <div className={styles.rollCursor} ref={cursor} />
          </div>
        </div>
      </div>
      <p className={styles.hint}>
        Click to add a note and drag before letting go to set its length. Drag a note to move it, its right
        edge to resize it, double-click to delete it.
      </p>
      <SynthPanel track={track} />
    </div>
  );
}
