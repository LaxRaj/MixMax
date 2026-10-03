"use client";

import { useEffect, useState } from "react";
import styles from "./dashboard.module.css";
import {
  PROVENANCE_COPY,
  PROVENANCE_ORDER,
  type Dashboard,
  type Evidence,
  type Provenance,
  type TrackState,
} from "@/lib/dashboard";

export default function DashboardPage() {
  useEffect(() => {
    document.title = "Pipeline — what do we know?";
  }, []);

  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/dashboard.json")
      .then((r) => {
        if (!r.ok) throw new Error(`No dashboard data (${r.status})`);
        return r.json();
      })
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <main className={styles.page}>
        <div className={styles.head}>
          <p className={styles.eyebrow}>Pipeline</p>
          <h1>No data yet</h1>
          <p>{error}</p>
        </div>
        <div className={styles.empty}>
          <strong>Generate it from the CLI</strong>
          <p>
            <code>producer dashboard --workspace comparisons --library reference_library.json</code>
          </p>
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

  const { counts, total, grounded_pct } = data.evidence_summary;
  const guessed = counts.guessed ?? 0;

  return (
    <main className={styles.page}>
      <header className={`${styles.head} rise`}>
        <p className={styles.eyebrow}>Pipeline</p>
        <h1>What do we actually know?</h1>
        <p>
          Every number this pipeline uses, and what backs it. Three times now a figure that
          looked authoritative turned out to be a guess — so provenance is the thing worth
          putting on screen.
        </p>

        <div className={styles.meter}>
          <div className={styles.meterTop}>
            <span className={styles.meterBig}>{grounded_pct}%</span>
            <span className={styles.meterCap}>
              of {total} numbers come from a published spec, a measured corpus, or a fit
              against one.{" "}
              {guessed > 0 && (
                <>
                  The other {guessed} {guessed === 1 ? "is a default" : "are defaults"} nobody
                  has checked against anything.
                </>
              )}
            </span>
          </div>

          <div className={styles.bar}>
            {PROVENANCE_ORDER.map((p) => {
              const n = counts[p] ?? 0;
              if (!n) return null;
              return (
                <span
                  key={p}
                  className={styles.seg}
                  data-p={p}
                  style={{ width: `${(n / total) * 100}%` }}
                  title={`${PROVENANCE_COPY[p].label}: ${n}`}
                />
              );
            })}
          </div>

          <div className={styles.key}>
            {PROVENANCE_ORDER.map((p) => (
              <span key={p} className={styles.keyItem}>
                <span className={styles.dot} data-p={p} style={dotColour(p)} />
                {PROVENANCE_COPY[p].label} ({counts[p] ?? 0})
              </span>
            ))}
          </div>
        </div>
      </header>

      <Section step="01" title="The process" sub="Each stage, and how much has been through it.">
        <div className={styles.pipeline}>
          {data.stages.map((s) => (
            <div
              key={s.key}
              className={styles.stage}
              data-complete={s.total > 0 && s.done >= s.total}
              data-empty={s.done === 0}
            >
              <div className={styles.stageLabel}>{s.label}</div>
              <div className={styles.stageCount}>
                {s.done}
                <small> / {s.total}</small>
              </div>
              <div className={styles.track}>
                <div
                  className={styles.trackFill}
                  style={{ width: `${s.total ? Math.min(100, (s.done / s.total) * 100) : 0}%` }}
                />
              </div>
              <p className={styles.stageBlurb}>{s.blurb}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section
        step="02"
        title="Evidence ledger"
        sub="Every tunable number, and where it came from. Tap a row for the detail."
      >
        <div className={styles.ledger}>
          {[...data.evidence]
            .sort(
              (a, b) =>
                PROVENANCE_ORDER.indexOf(a.provenance) - PROVENANCE_ORDER.indexOf(b.provenance),
            )
            .map((row, i) => (
              <EvidenceRow key={`${row.name}-${i}`} row={row} />
            ))}
        </div>
      </Section>

      <Section
        step="03"
        title="Our masters"
        sub="What each platform does to them on playback."
      >
        {data.tracks.length === 0 ? (
          <Empty
            title="Nothing ingested yet"
            body={
              <>
                Run <code>producer intake --input vocals --workspace comparisons</code>, then{" "}
                <code>producer render</code>.
              </>
            }
          />
        ) : (
          data.tracks.map((t) => <TrackCard key={t.slug} track={t} />)
        )}
      </Section>

      <Section
        step="04"
        title="Does it sound good?"
        sub="The one question none of the above answers."
      >
        {data.listening ? (
          <div className={styles.card}>
            <div className={styles.cardHead}>
              <span className={styles.cardTitle}>
                {data.listening.listeners.length} listener
                {data.listening.listeners.length === 1 ? "" : "s"}
              </span>
            </div>
            <div className={styles.tableWrap}>
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Mean score</th>
                    <th>1st-place votes</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.listening.by_source).map(([source, s]) => (
                    <tr key={source}>
                      <td className={styles.name}>{source}</td>
                      <td>{s.mean_score ?? "—"}</td>
                      <td>{s.wins}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <Empty
            title="No listening data"
            body={
              <>
                Everything above measures the signal. None of it can tell you whether a master
                sounds finished — only people can, and nobody has listened yet. Run{" "}
                <code>producer blindtest</code>, send the link, then{" "}
                <code>producer tally</code>.
              </>
            }
          />
        )}
      </Section>

      <p className={styles.foot}>
        Read-only. The CLI is the engine; this page renders <code>dashboard.json</code> and
        computes nothing of its own.
      </p>
    </main>
  );
}

function dotColour(p: Provenance): React.CSSProperties {
  const map: Record<Provenance, string> = {
    published: "#7fd4a0",
    measured: "#6fb8d8",
    fitted: "var(--glow)",
    reported: "#9a8a6a",
    guessed: "#5c4a3a",
  };
  return { background: map[p] };
}

function Section({
  step, title, sub, children,
}: { step: string; title: string; sub: string; children: React.ReactNode }) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <span className={styles.step}>{step}</span>
        <h2>{title}</h2>
      </div>
      <p className={styles.sectionSub}>{sub}</p>
      {children}
    </section>
  );
}

function EvidenceRow({ row }: { row: Evidence }) {
  const [open, setOpen] = useState(false);
  return (
    <button
      className={styles.row}
      data-testid="evidence-row"
      data-p={row.provenance}
      onClick={() => setOpen((v) => !v)}
      aria-expanded={open}
    >
      <span className={styles.rowName}>{row.name}</span>
      <span className={styles.rowValue}>{row.value}</span>
      <span className={styles.tag} data-p={row.provenance}>
        {PROVENANCE_COPY[row.provenance].label}
      </span>
      {open && (
        <span className={styles.detail} data-testid="evidence-detail">
          {row.detail}
          {row.source && <span className={styles.detailSource}>{row.source}</span>}
        </span>
      )}
    </button>
  );
}

function TrackCard({ track }: { track: TrackState }) {
  const m = track.measurement;
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <span className={styles.cardTitle}>{track.slug}</span>
        <span className={styles.chips}>
          {track.versions.map((v) => (
            <span key={v} className={styles.chip}>{v}</span>
          ))}
          {!track.has_reference && <span className={styles.chip}>no reference</span>}
        </span>
      </div>

      {!track.has_ours || !m ? (
        <p className={styles.sectionSub} style={{ margin: 0 }}>
          Not rendered yet — run <code>producer render</code>.
        </p>
      ) : (
        <>
          <div className={styles.metrics}>
            <Metric label="LUFS" value={fmt(m.lufs, 1)} />
            <Metric label="True peak" value={fmt(m.true_peak_dbtp, 2, " dBTP")} />
            <Metric label="Crest" value={fmt(m.crest_factor_db, 1, " dB")} />
            <Metric label="LRA" value={m.lra == null ? "—" : fmt(m.lra, 1, " LU")} />
            <Metric label="Centroid" value={fmt(m.spectral_centroid_hz, 0, " Hz")} />
          </div>

          {track.conformance && (
            <div className={styles.tableWrap}>
              <table>
                <thead>
                  <tr>
                    <th>Platform</th>
                    <th>Target</th>
                    <th>Gain</th>
                    <th>Delivered</th>
                  </tr>
                </thead>
                <tbody>
                  {track.conformance.map((c) => (
                    <tr key={c.standard}>
                      <td className={styles.name}>{c.standard}</td>
                      <td>{c.target_lufs.toFixed(0)}</td>
                      <td className={c.normalization_gain_db < -0.05 ? styles.gainDown : styles.gainNone}>
                        {c.normalization_gain_db > 0 ? "+" : ""}
                        {c.normalization_gain_db.toFixed(1)}
                      </td>
                      <td>{c.delivered_lufs.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {track.consequences?.map((c, i) => (
            <p key={i} className={styles.consequence}>{c}</p>
          ))}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.metric}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue}>{value}</div>
    </div>
  );
}

function Empty({ title, body }: { title: string; body: React.ReactNode }) {
  return (
    <div className={styles.empty}>
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}

function fmt(v: number | null | undefined, digits: number, suffix = ""): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}
