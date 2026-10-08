"use client";

import { useRef } from "react";
import { updateTrack } from "@/lib/daw/edit";
import type { Track } from "@/lib/daw/types";
import { useWs } from "@/lib/daw/useWorkstation";
import { usePlayhead } from "./TransportBar";
import styles from "./daw.module.css";

const FLOOR_DB = -54;

type Control = { label: string; min: number; max: number; step: number; value: number; unit: string; onChange: (value: number) => void };

function Strip({ name, colour, selected, controls, children, onSelect, meter }: {
  name: string;
  colour: string;
  selected?: boolean;
  controls: Control[];
  children?: React.ReactNode;
  onSelect?: () => void;
  meter: (element: HTMLDivElement | null) => void;
}) {
  return (
    <section className={styles.strip} data-selected={selected} style={{ ["--c" as string]: colour }} aria-label={`${name} channel`}>
      <header className={styles.stripHead}>
        <button className={styles.stripName} onClick={onSelect}>{name}</button>
        <div className={styles.meter} aria-hidden="true">
          <div className={styles.meterFill} ref={meter} />
        </div>
      </header>
      <div className={styles.stripButtons}>{children}</div>
      {controls.map((control) => (
        <label key={control.label} className={styles.knob}>
          <span>
            {control.label}
            <b className="mono">
              {control.unit === "%" ? Math.round(control.value * 100) : control.value.toFixed(control.step < 1 ? 1 : 0)}
              {control.unit}
            </b>
          </span>
          <input
            type="range"
            className={styles.miniRange}
            min={control.min}
            max={control.max}
            step={control.step}
            aria-label={`${name} ${control.label}`}
            value={control.value}
            // Double-click puts a control back where it started.
            onDoubleClick={() => control.onChange(0)}
            onChange={(event) => control.onChange(Number(event.target.value))}
          />
        </label>
      ))}
    </section>
  );
}

export function Mixer() {
  const ws = useWs();
  const { project } = ws;
  const meters = useRef(new Map<string, HTMLDivElement>());
  const held = useRef(new Map<string, number>());

  usePlayhead(() => {
    for (const [id, element] of meters.current) {
      const now = ws.level(id === "master" ? null : id);
      // Fall back slowly, so a short peak can be read.
      const shown = Math.max(now, (held.current.get(id) ?? FLOOR_DB) - 1.2);
      held.current.set(id, shown);
      const fraction = Math.max(0, Math.min(1, (shown - FLOOR_DB) / -FLOOR_DB));
      element.style.transform = `scaleX(${fraction.toFixed(3)})`;
      element.dataset.hot = String(shown > -1);
    }
  });

  const bind = (id: string) => (element: HTMLDivElement | null) => {
    if (element) meters.current.set(id, element);
    else meters.current.delete(id);
  };
  const set = (track: Track, patch: Partial<Track>, key: string) => ws.apply(updateTrack(track.id, patch), `${key}:${track.id}`);

  return (
    <div className={styles.mixer} data-testid="mixer">
      {project.tracks.map((track) => (
        <Strip
          key={track.id}
          name={track.name}
          colour={track.color}
          selected={ws.selection.track === track.id}
          onSelect={() => ws.select(track.id)}
          meter={bind(track.id)}
          controls={[
            { label: "Volume", unit: " dB", min: -40, max: 6, step: 0.5, value: Math.max(-40, track.volume_db), onChange: (v) => set(track, { volume_db: v }, "vol") },
            { label: "Pan", unit: "", min: -1, max: 1, step: 0.05, value: track.pan, onChange: (v) => set(track, { pan: v }, "pan") },
            { label: "Low", unit: " dB", min: -15, max: 15, step: 0.5, value: track.eq.low, onChange: (v) => set(track, { eq: { ...track.eq, low: v } }, "low") },
            { label: "Mid", unit: " dB", min: -15, max: 15, step: 0.5, value: track.eq.mid, onChange: (v) => set(track, { eq: { ...track.eq, mid: v } }, "mid") },
            { label: "High", unit: " dB", min: -15, max: 15, step: 0.5, value: track.eq.high, onChange: (v) => set(track, { eq: { ...track.eq, high: v } }, "high") },
            { label: "Reverb", unit: "%", min: 0, max: 1, step: 0.01, value: track.reverb, onChange: (v) => set(track, { reverb: v }, "rev") },
            { label: "Echo", unit: "%", min: 0, max: 1, step: 0.01, value: track.delay, onChange: (v) => set(track, { delay: v }, "dly") },
          ]}
        >
          <button className={styles.toggle} data-tone="mute" aria-pressed={track.mute} onClick={() => set(track, { mute: !track.mute }, "m")}>
            M
          </button>
          <button className={styles.toggle} data-tone="solo" aria-pressed={track.solo} onClick={() => set(track, { solo: !track.solo }, "s")}>
            S
          </button>
        </Strip>
      ))}
      <Strip
        name="Master"
        colour="var(--text)"
        meter={bind("master")}
        controls={[
          {
            label: "Volume", unit: " dB", min: -40, max: 6, step: 0.5, value: Math.max(-40, project.master.volume_db),
            onChange: (v) => ws.apply((p) => ({ ...p, master: { ...p.master, volume_db: v } }), "master-vol"),
          },
        ]}
      >
        <button
          className={styles.chip}
          aria-pressed={project.master.limiter}
          onClick={() => ws.apply((p) => ({ ...p, master: { ...p.master, limiter: !p.master.limiter } }))}
          title="Holds the loudest moments down so the mix can sit louder overall"
        >
          Limiter
        </button>
      </Strip>
    </div>
  );
}
