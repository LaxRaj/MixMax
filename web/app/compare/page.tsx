"use client";

import { useEffect, useState } from "react";
import styles from "./compare.module.css";
import {
  STATE_COPY,
  sectionColour,
  type Comparison,
  type Measure,
  type TrackProgress,
} from "@/lib/compare";

export default function ComparePage() {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Song development";
    fetch("/compare.json")
      .then((r) => {
        if (!r.ok) throw new Error(`No comparison data (${r.status})`);
        return r.json();
      })
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <main className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Development</p>
          <h1>No data yet</h1>
        </header>
        <div className={styles.empty}>
          <code>producer compare --workspace comparisons</code>
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className={styles.page}>
        <p style={{ color: "var(--text-faint)" }}>Loading…</p>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>Development</p>
        <h1>How finished is each one?</h1>
        <p>
          A compliant master of a bare vocal is still a bare vocal. This tracks completion
          rather than conformance — which stages each track has cleared, and what is actually
          stopping the rest.
        </p>
      </header>

      <div className={styles.grid}>
        {data.tracks.map((track) => (
          <TrackCard key={track.slug} track={track} />
        ))}
      </div>

      <p className={styles.foot}>
        Read-only. The CLI is the engine; this renders <code>compare.json</code>.
      </p>
    </main>
  );
}

function TrackCard({ track }: { track: TrackProgress }) {
  const { raw, final } = track.measurements;
  const total = track.arrangement.timeline.reduce((n, s) => n + s.duration_s, 0) || 1;

  return (
    <article className={styles.card}>
      <div>
        <div className={styles.cardHead}>
          <span className={styles.slug}>{track.slug}</span>
          <span className={styles.kind}>
            {track.kind} · {(track.duration_s / 60).toFixed(2)} min
          </span>
        </div>
        <div className={styles.pct}>
          <span className={styles.pctBig}>{track.percent}%</span>
        </div>
        <div className={styles.pctBar}>
          <div className={styles.pctFill} style={{ width: `${track.percent}%` }} />
        </div>
      </div>

      <div>
        <p className={styles.sectionTitle}>Stages</p>
        <div className={styles.stages}>
          {track.stages.map((stage) => (
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

      <div>
        <p className={styles.sectionTitle}>Arrangement</p>
        <div className={styles.timeline}>
          {track.arrangement.timeline.map((section, i) => (
            <div
              key={i}
              className={styles.block}
              style={{
                width: `${(section.duration_s / total) * 100}%`,
                background: sectionColour(section.label),
                // A breakdown reads as a gap in the bar: the low end is what left.
                opacity: 0.45 + 0.55 * Math.min(1, Math.max(0, (section.low_energy_db + 25) / 15)),
              }}
              title={`${section.label} · ${section.duration_s.toFixed(0)}s · low ${section.low_energy_db.toFixed(1)} dB`}
            >
              {section.duration_s / total > 0.07 && (
                <span className={styles.blockLabel}>{section.label}</span>
              )}
            </div>
          ))}
        </div>
        <div className={styles.ruler}>
          <span>0:00</span>
          <span>{track.arrangement.sections} sections · {track.arrangement.types} types</span>
          <span>
            {Math.floor(track.duration_s / 60)}:
            {String(Math.round(track.duration_s % 60)).padStart(2, "0")}
          </span>
        </div>
        <div className={styles.markers}>
          {track.arrangement.transitions.length === 0 ? (
            <span className={styles.marker}>no transitions</span>
          ) : (
            track.arrangement.transitions.map((t, i) => (
              <span key={i} className={styles.marker} data-k={t.kind}>
                {t.kind} @ {Math.floor(t.at_s / 60)}:
                {String(Math.round(t.at_s % 60)).padStart(2, "0")}
              </span>
            ))
          )}
        </div>
      </div>

      <div>
        <p className={styles.sectionTitle}>Raw → {track.measurements.final_file}</p>
        <div className={styles.tableWrap}>
          <table>
            <thead>
              <tr>
                <th>Measure</th>
                <th>Raw</th>
                <th>Final</th>
              </tr>
            </thead>
            <tbody>
              <Row label="LUFS" raw={raw.lufs} final={final.lufs} digits={1} />
              <Row label="True peak" raw={raw.true_peak_dbtp} final={final.true_peak_dbtp}
                   digits={2} better={(a, b) => b < a} />
              <Row label="Crest" raw={raw.crest_factor_db} final={final.crest_factor_db} digits={1} />
              <Row label="Loudness range" raw={raw.lra} final={final.lra} digits={1} />
            </tbody>
          </table>
        </div>
      </div>

      <div className={styles.next}>
        <strong>Next</strong>
        {track.next_step}
      </div>
    </article>
  );
}

function Row({
  label, raw, final, digits, better,
}: {
  label: string;
  raw: number | null;
  final: number | null;
  digits: number;
  better?: (raw: number, final: number) => boolean;
}) {
  const improved =
    better && raw != null && final != null && Number.isFinite(raw) && Number.isFinite(final)
      ? better(raw, final)
      : false;
  return (
    <tr>
      <td>{label}</td>
      <td>{fmt(raw, digits)}</td>
      <td className={improved ? styles.better : undefined}>{fmt(final, digits)}</td>
    </tr>
  );
}

function fmt(v: number | null | undefined, digits: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}
