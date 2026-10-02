"""Before/after comparison plot — the visual proof the pipeline did something."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this runs in CI and over SSH, never in a window

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from producer.audio import Track  # noqa: E402

WINDOW_S = 0.4          # short-term loudness window, matching the QA block size
FLOOR_DB = -80.0        # anything quieter is drawn at the floor, not at -inf


def _short_term_db(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Short-term RMS in dBFS, plus the time axis, for a loudness-over-time curve."""
    window = max(1, int(WINDOW_S * sample_rate))
    usable = len(samples) - (len(samples) % window)
    if usable < window:
        return np.array([0.0]), np.array([FLOOR_DB])

    frames = samples[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    times = (np.arange(len(db)) + 0.5) * window / sample_rate
    return times, np.maximum(db, FLOOR_DB)


def plot_comparison(before_path: str | Path, after_path: str | Path, out_png: str | Path) -> Path:
    """Stacked before/after waveforms plus their loudness curves, saved as PNG."""
    before, after = Track.load(before_path), Track.load(after_path)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    panels = [("Before — raw vocal", before, "#4c72b0"), ("After — mastered", after, "#c44e52")]

    fig, axes = plt.subplots(3, 1, figsize=(10, 7.5), constrained_layout=True)
    fig.suptitle("producer — before / after", fontsize=13, fontweight="bold")

    for ax, (title, track, color) in zip(axes[:2], panels):
        mono = track.mono()
        times = np.arange(len(mono)) / track.sample_rate
        ax.plot(times, mono, color=color, linewidth=0.6)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_ylim(-1.05, 1.05)
        ax.set_ylabel("amplitude")
        ax.grid(alpha=0.2)

    # Shared amplitude scale would hide the point, so loudness gets its own panel.
    for title, track, color in panels:
        times, db = _short_term_db(track.mono(), track.sample_rate)
        axes[2].plot(times, db, color=color, linewidth=1.4, label=title.split(" — ")[0])

    axes[2].set_title("Short-term loudness", fontsize=10, loc="left")
    axes[2].set_xlabel("time (s)")
    axes[2].set_ylabel("dBFS")
    axes[2].grid(alpha=0.2)
    axes[2].legend(loc="lower right", fontsize=9)

    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png
