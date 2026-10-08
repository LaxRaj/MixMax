"use client";

import { useRef } from "react";
import {
  addPattern,
  addPatternClip,
  fillPattern,
  removePattern,
  renamePattern,
  setPatternSteps,
  setStep,
  updateTrack,
} from "@/lib/daw/edit";
import {
  BEATS_PER_BAR,
  DRUM_LABELS,
  DRUM_PRESETS,
  DRUM_VOICES,
  KITS,
  KIT_LABELS,
  STEP_BEATS,
  type DrumTrack,
  type KitName,
  type SynthTrack,
} from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { usePlayhead } from "./TransportBar";
import styles from "./daw.module.css";

const NORMAL = 0.8;
const ACCENT = 1;
const GHOST = 0.45;

/** Which pattern is being edited, and the buttons that make, copy, place and delete patterns. */
export function PatternBar({ track, children }: { track: DrumTrack | SynthTrack; children?: React.ReactNode }) {
  const ws = useWs();
  const current = ws.patternOf(track.id);
  const pattern = track.patterns.find((p) => p.id === current);
  const add = (copyOf?: string) => {
    let made = "";
    ws.apply(addPattern(track.id, copyOf, (id) => (made = id)));
    if (made) ws.editPattern(track.id, made);
  };

  return (
    <div className={styles.patternBar}>
      <div className={styles.tabs} role="tablist" aria-label="Patterns">
        {track.patterns.map((p) => (
          <button
            key={p.id}
            role="tab"
            className={styles.tab}
            aria-selected={p.id === current}
            onClick={() => ws.editPattern(track.id, p.id)}
          >
            {p.name}
          </button>
        ))}
        <button className={styles.tab} onClick={() => add()} title="A new, empty pattern">
          + New
        </button>
      </div>
      {pattern && (
        <div className={styles.patternTools}>
          <input
            className={styles.patternName}
            aria-label="Pattern name"
            value={pattern.name}
            maxLength={40}
            onChange={(event) => ws.apply(renamePattern(track.id, pattern.id, event.target.value), `pname:${pattern.id}`)}
          />
          {children}
          <button className={styles.chip} onClick={() => add(pattern.id)} title="A copy to vary, leaving this one as it is">
            Copy
          </button>
          <button
            className={styles.chip}
            onClick={() => {
              // Land on a bar line: a pattern dropped mid-bar is almost never what was meant.
              const bar = Math.floor(ws.position() / BEATS_PER_BAR + 1e-6) * BEATS_PER_BAR;
              ws.apply(addPatternClip(track.id, pattern.id, bar));
            }}
            title="Put this pattern on the timeline at the playhead. You can also double-click the track's lane."
          >
            Place at playhead
          </button>
          <button
            className={styles.chip}
            data-tone="danger"
            disabled={track.patterns.length <= 1}
            onClick={() => ws.apply(removePattern(track.id, pattern.id))}
            title="Delete this pattern and every clip of it"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

export function StepSequencer({ track }: { track: DrumTrack }) {
  const ws = useWs();
  const patternId = ws.patternOf(track.id);
  const pattern = track.patterns.find((p) => p.id === patternId);
  const cursor = useRef<HTMLDivElement>(null);

  usePlayhead((beat) => {
    const element = cursor.current;
    if (!element || !pattern) return;
    // Show the step being played, if a clip of this pattern is under the playhead.
    const clip = ws.playing
      ? track.clips.find((c) => c.pattern === pattern.id && beat >= c.start && beat < c.start + c.length)
      : undefined;
    if (!clip) {
      element.style.opacity = "0";
      return;
    }
    const step = Math.floor((beat - (clip.start - clip.offset)) / STEP_BEATS) % pattern.steps;
    element.style.opacity = "1";
    element.style.gridColumn = String(step + 2);
  });

  if (!pattern) return null;

  const toggle = (voice: (typeof DRUM_VOICES)[number], step: number, event: React.MouseEvent) => {
    const now = pattern.lanes[voice][step];
    const wanted = event.shiftKey ? ACCENT : event.altKey ? GHOST : NORMAL;
    const next = now > 0 && (now === wanted || (!event.shiftKey && !event.altKey)) ? 0 : wanted;
    ws.apply(setStep(track.id, pattern.id, voice, step, next));
    if (next > 0 && !ws.playing) ws.previewDrum(track.id, voice, next);
  };

  return (
    <div className={styles.editor}>
      <PatternBar track={track}>
        <label className={styles.field}>
          <span>Kit</span>
          <select
            className={styles.tinySelect}
            aria-label="Drum kit"
            value={track.kit}
            onChange={(event) => ws.apply(updateTrack(track.id, { kit: event.target.value as KitName }))}
          >
            {KITS.map((kit) => (
              <option key={kit} value={kit}>{KIT_LABELS[kit]}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Length</span>
          <select
            className={styles.tinySelect}
            aria-label="Pattern length"
            value={pattern.steps}
            onChange={(event) => ws.apply(setPatternSteps(track.id, pattern.id, Number(event.target.value)))}
          >
            <option value={16}>1 bar</option>
            <option value={32}>2 bars</option>
          </select>
        </label>
        <label className={styles.field}>
          <span>Start from</span>
          <select
            className={styles.tinySelect}
            aria-label="Fill with a starter beat"
            value=""
            onChange={(event) => {
              if (event.target.value === "clear") ws.apply(fillPattern(track.id, pattern.id, null));
              else if (event.target.value) ws.apply(fillPattern(track.id, pattern.id, event.target.value));
            }}
          >
            <option value="">Choose…</option>
            {Object.keys(DRUM_PRESETS).map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
            <option value="clear">Empty</option>
          </select>
        </label>
      </PatternBar>

      <div className={styles.stepScroll}>
        <div
          className={styles.steps}
          style={{ gridTemplateColumns: `minmax(84px, auto) repeat(${pattern.steps}, minmax(26px, 1fr))` }}
          data-testid="steps"
        >
          <div className={styles.stepCursor} ref={cursor} style={{ gridRow: `1 / span ${DRUM_VOICES.length}` }} />
          {DRUM_VOICES.map((voice, row) => (
            <div key={voice} className={styles.stepRow} style={{ display: "contents" }}>
              <button
                className={styles.voice}
                style={{ gridRow: row + 1, gridColumn: 1 }}
                onClick={() => ws.previewDrum(track.id, voice)}
                title="Hear it"
              >
                {DRUM_LABELS[voice]}
              </button>
              {pattern.lanes[voice].map((velocity, step) => (
                <button
                  key={step}
                  className={styles.step}
                  style={{ gridRow: row + 1, gridColumn: step + 2, ["--v" as string]: velocity }}
                  data-beat={Math.floor(step / 4) % 2 === 0}
                  data-on={velocity > 0}
                  aria-pressed={velocity > 0}
                  aria-label={`${DRUM_LABELS[voice]} step ${step + 1}`}
                  onClick={(event) => toggle(voice, step, event)}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
      <p className={styles.hint}>
        Click a step to switch it on. Shift-click for an accent, Alt-click for a ghost note. Changes are
        heard the next time the loop comes round.
      </p>
    </div>
  );
}
