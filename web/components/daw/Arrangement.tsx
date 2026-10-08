"use client";

import { useEffect, useMemo, useRef } from "react";
import { addPatternClip, moveClip, trimClipEnd, trimClipStart, updateTrack } from "@/lib/daw/edit";
import { clipBeats, contentEnd, snap } from "@/lib/daw/schedule";
import {
  BEATS_PER_BAR,
  DRUM_VOICES,
  STEP_BEATS,
  type AudioClip,
  type DrumPattern,
  type NotePattern,
  type PatternClip,
  type Track,
} from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { usePlayhead } from "./TransportBar";
import styles from "./daw.module.css";

const GRIDS = [
  { value: BEATS_PER_BAR, label: "Bar" },
  { value: 1, label: "Beat" },
  { value: 0.5, label: "1/8" },
  { value: STEP_BEATS, label: "1/16" },
  { value: 0, label: "Off" },
];
const ZOOMS = [8, 12, 18, 28, 42, 64, 96];

const svgUrl = (body: string, width: number, height: number): string =>
  `url("data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 ${width} ${height}' preserveAspectRatio='none'>${body}</svg>`,
  )}")`;

// One picture per pattern, tiled across its clips by CSS — so a 64-bar clip costs the same as a 1-bar one.
const thumbs = new WeakMap<object, string>();
function thumbnail(pattern: DrumPattern | NotePattern, kind: Track["kind"]): string {
  const cached = thumbs.get(pattern);
  if (cached) return cached;
  let made: string;
  if (kind === "drums") {
    const drum = pattern as DrumPattern;
    const marks = DRUM_VOICES.flatMap((voice, row) =>
      drum.lanes[voice].flatMap((velocity, step) =>
        velocity > 0
          ? [`<rect x='${step + 0.15}' y='${row + 0.2}' width='0.7' height='0.6' fill='#000' fill-opacity='${0.35 + velocity * 0.45}'/>`]
          : [],
      ),
    );
    made = svgUrl(marks.join(""), drum.steps, DRUM_VOICES.length);
  } else {
    const part = pattern as NotePattern;
    const pitches = part.notes.map((n) => n.pitch);
    const low = Math.min(...pitches, 127) - 1;
    const span = Math.max(12, Math.max(...pitches, 0) - low + 2);
    const marks = part.notes.map(
      (n) => `<rect x='${n.start}' y='${span - (n.pitch - low) - 1}' width='${Math.max(0.1, n.length - 0.04)}' height='1' fill='#000' fill-opacity='0.7'/>`,
    );
    made = svgUrl(marks.join(""), part.length, span);
  }
  thumbs.set(pattern, made);
  return made;
}

function Wave({ clip, width }: { clip: AudioClip; width: number }) {
  const ws = useWs();
  const canvas = useRef<HTMLCanvasElement>(null);
  const peaks = ws.peaks(clip.media);
  const total = ws.project.media.find((m) => m.id === clip.media)?.duration_s ?? 0;
  // Browsers cap canvas width; past that the picture is stretched rather than lost.
  const pixels = Math.max(1, Math.min(4000, Math.round(width)));

  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext("2d");
    if (!element || !ctx) return;
    ctx.clearRect(0, 0, element.width, element.height);
    if (!peaks || total <= 0) return;
    const buckets = peaks.length / 2;
    const mid = element.height / 2;
    ctx.fillStyle = "rgba(0, 0, 0, 0.62)";
    for (let x = 0; x < pixels; x++) {
      const t = clip.offset_s + (x / pixels) * clip.duration_s;
      const bucket = Math.min(buckets - 1, Math.max(0, Math.floor((t / total) * buckets)));
      const top = mid - peaks[bucket * 2 + 1] * mid;
      const bottom = mid - peaks[bucket * 2] * mid;
      ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
    }
  }, [peaks, total, pixels, clip.offset_s, clip.duration_s]);

  return <canvas ref={canvas} className={styles.wave} width={pixels} height={64} aria-hidden="true" />;
}

export function Arrangement({ onAdd }: { onAdd: () => void }) {
  const ws = useWs();
  const { project, view, selection } = ws;
  const ppb = view.pxPerBeat;
  const scroller = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const playhead = useRef<HTMLDivElement>(null);

  const beats = useMemo(
    () => Math.max(64, Math.ceil((contentEnd(project) + 16) / BEATS_PER_BAR) * BEATS_PER_BAR),
    [project],
  );
  const width = beats * ppb;
  const bars = beats / BEATS_PER_BAR;
  // Label every bar when there is room, otherwise every second, fourth, eighth…
  const labelEvery = [1, 2, 4, 8, 16].find((n) => n * BEATS_PER_BAR * ppb >= 44) ?? 16;

  usePlayhead((beat) => {
    const x = beat * ppb;
    if (playhead.current) playhead.current.style.transform = `translateX(${x}px)`;
    const box = scroller.current;
    const head = overlay.current?.offsetLeft ?? 0;
    // While playing, keep the playhead on screen.
    if (ws.playing && box && (x + head > box.scrollLeft + box.clientWidth - 24 || x + head < box.scrollLeft + head)) {
      box.scrollLeft = Math.max(0, x - 40);
    }
  });

  const beatAt = (clientX: number): number => {
    const origin = overlay.current?.getBoundingClientRect().left ?? 0;
    return Math.max(0, (clientX - origin) / ppb);
  };

  const onRuler = (event: React.PointerEvent) => {
    const start = beatAt(event.clientX);
    if (!event.shiftKey) {
      ws.seek(snap(start, view.grid));
      return;
    }
    // Shift-drag paints the loop, a bar at a time.
    const from = Math.floor(start / BEATS_PER_BAR) * BEATS_PER_BAR;
    const paint = (clientX: number) => {
      const to = Math.floor(beatAt(clientX) / BEATS_PER_BAR) * BEATS_PER_BAR;
      const loop = { on: true, start: Math.min(from, to), end: Math.max(from, to) + BEATS_PER_BAR };
      ws.apply((p) => ({ ...p, loop }), "loop-paint");
    };
    paint(event.clientX);
    const move = (e: PointerEvent) => paint(e.clientX);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const grab = (event: React.PointerEvent, track: Track, clip: PatternClip | AudioClip, mode: "move" | "start" | "end") => {
    if (event.button !== 0) return;
    event.stopPropagation();
    ws.select(track.id, clip.id);
    const length = track.kind === "audio" ? clipBeats(clip as AudioClip, project.bpm) : (clip as PatternClip).length;
    const origin = { x: event.clientX, y: event.clientY, start: clip.start, end: clip.start + length };
    const media = track.kind === "audio" ? project.media.find((m) => m.id === (clip as AudioClip).media) : undefined;
    const key = `clip:${clip.id}:${mode}:${Date.now()}`;
    let trackId = track.id;
    let moved = false;

    const move = (e: PointerEvent) => {
      // A click with a slightly unsteady hand is still a click.
      if (!moved && Math.abs(e.clientX - origin.x) < 4 && Math.abs(e.clientY - origin.y) < 4) return;
      moved = true;
      const grid = e.altKey ? 0 : view.grid;
      const delta = (e.clientX - origin.x) / ppb;
      if (mode === "start") {
        ws.apply(trimClipStart(trackId, clip.id, snap(origin.start + delta, grid)), key);
      } else if (mode === "end") {
        ws.apply(trimClipEnd(trackId, clip.id, snap(origin.end + delta, grid), media), key);
      } else {
        const lane = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-lane]")?.dataset.lane;
        const after = ws.apply(moveClip(trackId, clip.id, snap(origin.start + delta, grid), lane), key);
        if (lane && after.tracks.find((t) => t.id === lane)?.clips.some((c) => c.id === clip.id)) {
          trackId = lane;
          ws.select(lane, clip.id);
        }
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const zoom = (by: -1 | 1) => {
    const index = ZOOMS.findIndex((z) => z >= ppb);
    ws.setView({ pxPerBeat: ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, (index < 0 ? ZOOMS.length - 1 : index) + by))] });
  };

  return (
    <div className={styles.arrange} ref={scroller} data-testid="arrangement">
      <div className={styles.arrangeInner} style={{ width: `calc(var(--head) + ${width}px)`, ["--beat" as string]: `${ppb}px` }}>
        <div className={styles.rulerRow}>
          <div className={styles.corner}>
            <button className={styles.tiny} onClick={() => zoom(-1)} aria-label="Zoom out">−</button>
            <button className={styles.tiny} onClick={() => zoom(1)} aria-label="Zoom in">+</button>
            <select
              className={styles.tinySelect}
              aria-label="Snap to"
              title="What clips snap to. Hold Alt while dragging to ignore it."
              value={view.grid}
              onChange={(event) => ws.setView({ grid: Number(event.target.value) })}
            >
              {GRIDS.map((g) => (
                <option key={g.label} value={g.value}>{g.label}</option>
              ))}
            </select>
          </div>
          <div
            className={styles.ruler}
            style={{ width }}
            onPointerDown={onRuler}
            title="Click to move the playhead. Shift-drag to set the loop."
            data-testid="ruler"
          >
            {Array.from({ length: Math.ceil(bars / labelEvery) }, (_, i) => i * labelEvery).map((bar) => (
              <span key={bar} className={`${styles.barLabel} mono`} style={{ left: bar * BEATS_PER_BAR * ppb }}>
                {bar + 1}
              </span>
            ))}
          </div>
        </div>

        {project.tracks.map((track) => (
          <div key={track.id} className={styles.trackRow} data-selected={selection.track === track.id} data-kind={track.kind}>
            <div className={styles.trackHead} onPointerDown={() => ws.select(track.id)} style={{ ["--c" as string]: track.color }}>
              <input
                className={styles.trackName}
                aria-label="Track name"
                value={track.name}
                maxLength={40}
                onChange={(event) => ws.apply(updateTrack(track.id, { name: event.target.value }), `name:${track.id}`)}
              />
              <div className={styles.trackButtons}>
                <button
                  className={styles.toggle}
                  data-tone="mute"
                  aria-pressed={track.mute}
                  aria-label={`Mute ${track.name}`}
                  onClick={() => ws.apply(updateTrack(track.id, { mute: !track.mute }))}
                >
                  M
                </button>
                <button
                  className={styles.toggle}
                  data-tone="solo"
                  aria-pressed={track.solo}
                  aria-label={`Solo ${track.name}`}
                  onClick={() => ws.apply(updateTrack(track.id, { solo: !track.solo }))}
                >
                  S
                </button>
                <input
                  className={styles.miniRange}
                  type="range"
                  min={-40}
                  max={6}
                  step={0.5}
                  aria-label={`Volume of ${track.name}`}
                  title={`${track.volume_db.toFixed(1)} dB`}
                  value={Math.max(-40, track.volume_db)}
                  onChange={(event) => ws.apply(updateTrack(track.id, { volume_db: Number(event.target.value) }), `vol:${track.id}`)}
                />
              </div>
            </div>
            <div
              className={styles.lane}
              data-lane={track.id}
              style={{ width }}
              onPointerDown={() => ws.select(track.id)}
              onDoubleClick={(event) => {
                if (track.kind === "audio") return;
                const pattern = ws.patternOf(track.id);
                const bar = Math.floor(beatAt(event.clientX) / BEATS_PER_BAR) * BEATS_PER_BAR;
                if (pattern) ws.apply(addPatternClip(track.id, pattern, bar));
              }}
            >
              {track.clips.map((clip) => {
                const length = track.kind === "audio" ? clipBeats(clip as AudioClip, project.bpm) : (clip as PatternClip).length;
                const pattern = track.kind === "audio" ? null : track.patterns.find((p) => p.id === (clip as PatternClip).pattern);
                const tile = pattern
                  ? (track.kind === "drums" ? (pattern as DrumPattern).steps * STEP_BEATS : (pattern as NotePattern).length) * ppb
                  : 0;
                const media = track.kind === "audio" ? project.media.find((m) => m.id === (clip as AudioClip).media) : null;
                const state = media ? ws.mediaState(media.id) : "ready";
                const label = pattern?.name ?? media?.name ?? "Audio";
                return (
                  <div
                    key={clip.id}
                    className={styles.clip}
                    role="button"
                    tabIndex={0}
                    aria-label={`${label} on ${track.name}`}
                    aria-pressed={selection.clip === clip.id}
                    data-state={state}
                    style={{ left: clip.start * ppb, width: Math.max(4, length * ppb), ["--c" as string]: track.color }}
                    onPointerDown={(event) => grab(event, track, clip, "move")}
                    onFocus={() => ws.select(track.id, clip.id)}
                    onDoubleClick={(event) => event.stopPropagation()}
                  >
                    {pattern && (
                      <span
                        className={styles.clipArt}
                        style={{
                          backgroundImage: thumbnail(pattern, track.kind),
                          backgroundSize: `${tile}px 100%`,
                          backgroundPositionX: -(clip as PatternClip).offset * ppb,
                        }}
                      />
                    )}
                    {track.kind === "audio" && <Wave clip={clip as AudioClip} width={length * ppb} />}
                    <span className={styles.clipName}>
                      {state === "loading" ? "Loading… " : state === "error" ? "Missing: " : ""}
                      {label}
                    </span>
                    <span className={styles.handle} data-edge="start" onPointerDown={(event) => grab(event, track, clip, "start")} />
                    <span className={styles.handle} data-edge="end" onPointerDown={(event) => grab(event, track, clip, "end")} />
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {project.tracks.length === 0 && (
          <div className={styles.blank}>
            <p>
              <strong>Nothing here yet.</strong> Start with a drum beat, play a part on a synth, or bring in a
              vocal to build around.
            </p>
            <button className={styles.export} onClick={onAdd}>
              Add the first track
            </button>
          </div>
        )}

        <div className={styles.overlay} ref={overlay} style={{ width }}>
          <div
            className={styles.loopBand}
            data-on={project.loop.on}
            style={{ left: project.loop.start * ppb, width: (project.loop.end - project.loop.start) * ppb }}
          />
          <div className={styles.playhead} ref={playhead} />
        </div>
      </div>
    </div>
  );
}
