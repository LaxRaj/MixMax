"use client";

import type { BlindPlayer } from "@/lib/useBlindPlayer";
import styles from "./Player.module.css";

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function Player({ player, labels }: { player: BlindPlayer; labels: string[] }) {
  const { status, error, loadedCount, totalCount, isPlaying, active, position, duration } = player;
  const pct = duration > 0 ? (position / duration) * 100 : 0;

  if (status !== "ready") {
    return (
      <div className={styles.console}>
        <div className={styles.inner}>
          <button
            className={styles.loadBtn}
            onClick={() => void player.load()}
            disabled={status === "loading"}
          >
            {status === "loading"
              ? `Loading ${loadedCount}/${totalCount}…`
              : status === "error"
                ? "Retry"
                : "Load the audio"}
          </button>
          <p className={`${styles.status} ${error ? styles.error : ""}`}>
            {error ?? "All versions load up front so switching between them is instant."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.console}>
      <div className={styles.inner}>
        <div className={styles.switcher} role="radiogroup" aria-label="Version">
          {labels.map((label, i) => (
            <button
              key={label}
              className={styles.key}
              data-active={label === active}
              role="radio"
              aria-checked={label === active}
              aria-label={`Version ${label}`}
              onClick={() => player.select(label)}
            >
              <span className={styles.lamp} aria-hidden />
              <span className={styles.keyLabel}>{label}</span>
              <span className={styles.keyHint}>key {i + 1}</span>
            </button>
          ))}
        </div>

        <div className={styles.transport}>
          <button
            className={styles.play}
            onClick={() => void player.toggle()}
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? (
              <svg width="16" height="18" viewBox="0 0 16 18" fill="currentColor" aria-hidden>
                <rect x="1" y="1" width="5" height="16" rx="1.5" />
                <rect x="10" y="1" width="5" height="16" rx="1.5" />
              </svg>
            ) : (
              <svg width="16" height="18" viewBox="0 0 16 18" fill="currentColor" aria-hidden>
                <path d="M2 1.8c0-1.1 1.2-1.8 2.1-1.2l10 7.2c.9.6.9 1.9 0 2.5l-10 7.2c-.9.6-2.1-.1-2.1-1.2V1.8Z" />
              </svg>
            )}
          </button>

          <div className={styles.timeline}>
            <input
              className={`${styles.scrub} mono`}
              style={{ ["--pct" as string]: `${pct}%` }}
              type="range"
              min={0}
              max={Math.max(duration, 0.1)}
              step={0.01}
              value={position}
              onChange={(e) => player.seek(Number(e.target.value))}
              aria-label="Position"
            />
            <div className={`${styles.readout} mono`}>
              <span>{clock(position)}</span>
              <span>loops · {clock(duration)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
