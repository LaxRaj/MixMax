"use client";

import styles from "./Form.module.css";

/**
 * Reordering uses explicit controls rather than drag-and-drop: the HTML5 drag
 * API does not fire on touch, and phones are the primary target here. Arrows
 * also give keyboard and screen-reader users the same affordance for free.
 */
export function Ranker({
  ranking,
  onChange,
}: {
  ranking: string[];
  onChange: (next: string[]) => void;
}) {
  const move = (from: number, to: number) => {
    if (to < 0 || to >= ranking.length) return;
    const next = [...ranking];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };

  return (
    <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {ranking.map((label, i) => (
        <li key={label} className={styles.rankRow}>
          <span className={`${styles.rankPos} mono`}>{i + 1}</span>
          <span className={styles.rankLabel}>{label}</span>
          <span className={styles.arrows}>
            <button
              className={styles.arrow}
              onClick={() => move(i, i - 1)}
              disabled={i === 0}
              aria-label={`Move ${label} up`}
            >
              <svg width="13" height="13" viewBox="0 0 12 12" fill="none" aria-hidden>
                <path d="M6 10V2m0 0L2.5 5.5M6 2l3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              className={styles.arrow}
              onClick={() => move(i, i + 1)}
              disabled={i === ranking.length - 1}
              aria-label={`Move ${label} down`}
            >
              <svg width="13" height="13" viewBox="0 0 12 12" fill="none" aria-hidden>
                <path d="M6 2v8m0 0 3.5-3.5M6 10 2.5 6.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </span>
        </li>
      ))}
    </ol>
  );
}
