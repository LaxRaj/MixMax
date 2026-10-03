"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Player } from "@/components/Player";
import { Ranker } from "@/components/Ranker";
import styles from "@/components/Form.module.css";
import { downloadCsv, toCsv, type LabelResponse } from "@/lib/csv";
import { assertBlind, type Manifest } from "@/lib/manifest";
import { useBlindPlayer } from "@/lib/useBlindPlayer";

export default function Page() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/test.json")
      .then((r) => {
        if (!r.ok) throw new Error(`No test manifest (${r.status})`);
        return r.json();
      })
      .then((data: Manifest) => {
        assertBlind(data);
        setManifest(data);
      })
      .catch((err: Error) => setLoadError(err.message));
  }, []);

  if (loadError) {
    return (
      <main className={styles.page}>
        <div className={styles.lede}>
          <h1>Nothing to listen to</h1>
          <p>{loadError}</p>
        </div>
      </main>
    );
  }

  if (!manifest) {
    return (
      <main className={styles.page}>
        <p style={{ color: "var(--text-faint)" }}>Loading…</p>
      </main>
    );
  }

  return <Test manifest={manifest} />;
}

function Test({ manifest }: { manifest: Manifest }) {
  const player = useBlindPlayer(manifest);
  const labels = manifest.labels;

  const [listener, setListener] = useState("");
  const [headphones, setHeadphones] = useState(false);
  const [scores, setScores] = useState<Record<string, number | null>>(() =>
    Object.fromEntries(labels.map((l) => [l, null])),
  );
  const [notes, setNotes] = useState<Record<string, string>>(() =>
    Object.fromEntries(labels.map((l) => [l, ""])),
  );
  const [ranking, setRanking] = useState<string[]>(labels);
  const [submitted, setSubmitted] = useState(false);

  // Space toggles, number keys switch version -- the comparison should never
  // require looking away from what you are hearing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (e.code === "Space") {
        e.preventDefault();
        void player.toggle();
        return;
      }
      const index = Number(e.key) - 1;
      if (Number.isInteger(index) && index >= 0 && index < labels.length) {
        e.preventDefault();
        player.select(labels[index]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [labels, player]);

  const missing = useMemo(() => {
    const unscored = labels.filter((l) => scores[l] === null);
    const problems: string[] = [];
    if (!listener.trim()) problems.push("your name");
    if (unscored.length) problems.push(`a score for ${unscored.join(", ")}`);
    return problems;
  }, [labels, listener, scores]);

  const submit = useCallback(() => {
    const responses: LabelResponse[] = labels.map((label) => ({
      label,
      score: scores[label],
      notes: notes[label],
    }));
    const csv = toCsv({
      slug: manifest.slug,
      listener: listener.trim(),
      headphones,
      responses,
      ranking,
      switchCount: player.switchCount,
    });
    downloadCsv(`${manifest.slug}-${listener.trim().toLowerCase().replace(/\s+/g, "-")}.csv`, csv);
    setSubmitted(true);
  }, [headphones, labels, listener, manifest.slug, notes, player.switchCount, ranking, scores]);

  if (submitted) {
    return (
      <main className={styles.page}>
        <div className={`${styles.done} rise`}>
          <h2>Thank you</h2>
          <p>
            Your scoresheet has been downloaded. Send that file back and you&apos;re done — it
            drops straight into the results.
          </p>
          <div className={styles.actions}>
            <button className={styles.ghost} onClick={() => setSubmitted(false)}>
              Back to the test
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <>
      <Player player={player} labels={labels} />

      <main className={styles.page}>
        <div className={`${styles.lede} rise`}>
          <h1>Which of these sounds finished?</h1>
          <p>
            {labels.length} versions of the same recording, processed differently. About five
            minutes.
          </p>
          <div className={styles.note}>
            They&apos;re volume-matched, so you&apos;re judging the sound and not which is
            loudest, and the names are meaningless on purpose. Headphones or real speakers if you
            can — laptop speakers hide most of what&apos;s being tested.
          </div>
        </div>

        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <span className={styles.step}>01</span>
            <h2>Score each one</h2>
          </div>
          <p className={styles.sectionSub}>
            Listen to all {labels.length} before scoring. Tap a letter above to switch instantly —
            it keeps playing from the same spot.
          </p>

          {labels.map((label) => (
            <div key={label} className={styles.card} data-active={label === player.active}>
              <div className={styles.cardHead}>
                <span className={styles.badge}>{label}</span>
                {label === player.active && player.isPlaying && (
                  <span className={styles.playing}>playing</span>
                )}
              </div>

              <div className={styles.scale} role="radiogroup" aria-label={`Score for ${label}`}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    className={styles.dot}
                    data-on={scores[label] === n}
                    role="radio"
                    aria-checked={scores[label] === n}
                    onClick={() => setScores((s) => ({ ...s, [label]: n }))}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div className={styles.scaleEnds}>
                <span>not a release</span>
                <span>sounds finished</span>
              </div>

              <textarea
                className={styles.notes}
                placeholder="Anything you noticed — harsh, muddy, thin, boxy, squashed?"
                value={notes[label]}
                onChange={(e) => setNotes((n) => ({ ...n, [label]: e.target.value }))}
              />
            </div>
          ))}
        </section>

        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <span className={styles.step}>02</span>
            <h2>Rank them</h2>
          </div>
          <p className={styles.sectionSub}>Best at the top. No ties.</p>
          <Ranker ranking={ranking} onChange={setRanking} />
        </section>

        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <span className={styles.step}>03</span>
            <h2>About you</h2>
          </div>

          <div className={styles.field}>
            <label htmlFor="listener">Your name</label>
            <input
              id="listener"
              className={styles.input}
              value={listener}
              onChange={(e) => setListener(e.target.value)}
              placeholder="So we know whose notes are whose"
              autoComplete="name"
            />
          </div>

          <label className={styles.check}>
            <input
              type="checkbox"
              checked={headphones}
              onChange={(e) => setHeadphones(e.target.checked)}
            />
            I listened on headphones or proper speakers
          </label>

          <button className={styles.submit} onClick={submit} disabled={missing.length > 0}>
            Finish and download my answers
          </button>
          {missing.length > 0 && (
            <p className={styles.missing}>Still needed: {missing.join(" · ")}</p>
          )}
        </section>

        <p className={styles.foot}>
          Please don&apos;t try to work out which is which, or compare notes before sending — that
          defeats the point of the letters.
        </p>
      </main>
    </>
  );
}
