"""Render to an exact loudness target, without breaching true peak.

Mastering through `matchering` inherits its loudness from the reference, which
is fine when you trust the reference and useless when you need to hit a
specific number. This stage exists so the same source can be rendered at
several published targets and compared.

Hitting a LUFS target is not a single gain change: limiting to protect true
peak alters the loudness it was meant to preserve. So this converges — measure,
correct, limit, measure again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
from pedalboard import Limiter, Pedalboard

from producer.audio import Track
from producer.benchmark import _true_peak_dbtp

# Converge to within this of the target, in LU.
TOLERANCE_LU = 0.2
MAX_PASSES = 10

# The limiter acts on sample peaks; true peak is measured on 4x oversampled
# audio and sits above them by an amount that depends on the material. Dense
# mixes have been seen a full dB higher, so rather than guess a margin, start
# conservatively and tighten it until the measured true peak actually fits.
TRUE_PEAK_TOLERANCE_DB = 0.05

# The limiter is pushed, not asked to do the whole job: pedalboard's Limiter
# saturates toward 0 dBFS under heavy input gain rather than holding its
# threshold, so the output is trimmed to the true-peak ceiling afterwards.
LIMITER_THRESHOLD_DB = -0.3
LIMITER_RELEASE_MS = 100.0

# Loudness rises sub-linearly with gain into a limiter, so the search is a
# bisection rather than an arithmetic correction.
SEARCH_HEADROOM_DB = 36.0
SEARCH_STEPS = 12


@dataclass
class LoudnessResult:
    """What a normalization pass actually achieved."""

    target_lufs: float
    achieved_lufs: float
    true_peak_dbtp: float
    gain_applied_db: float
    limited: bool
    passes: int

    @property
    def on_target(self) -> bool:
        return abs(self.achieved_lufs - self.target_lufs) <= TOLERANCE_LU

    def to_dict(self) -> dict:
        return {
            "target_lufs": round(self.target_lufs, 2),
            "achieved_lufs": round(self.achieved_lufs, 2),
            "true_peak_dbtp": round(self.true_peak_dbtp, 2),
            "gain_applied_db": round(self.gain_applied_db, 2),
            "limited": self.limited,
            "passes": self.passes,
            "on_target": self.on_target,
        }


def _measure_lufs(samples: np.ndarray, sample_rate: int) -> float:
    meter = pyln.Meter(sample_rate)
    if len(samples) < meter.block_size * sample_rate:
        return float("-inf")
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(meter.integrated_loudness(samples))


def _true_peak(samples: np.ndarray, sample_rate: int, path: Path) -> float:
    return _true_peak_dbtp(Track(path=path, samples=samples, sample_rate=sample_rate))


def _limit_and_trim(
    base: np.ndarray, gain_db: float, sample_rate: int, ceiling_dbtp: float, path: Path
) -> tuple[np.ndarray, float]:
    """Push `gain_db` into the limiter, then trim the result to the ceiling."""
    board = Pedalboard([
        Limiter(threshold_db=LIMITER_THRESHOLD_DB, release_ms=LIMITER_RELEASE_MS)
    ])
    driven = (base * (10.0 ** (gain_db / 20.0))).astype(np.float32)
    limited = np.asarray(board(driven, sample_rate, reset=True), dtype=np.float32)

    peak = _true_peak(limited, sample_rate, path)
    trimmed = (limited * (10.0 ** ((ceiling_dbtp - peak) / 20.0))).astype(np.float32)
    return trimmed, _measure_lufs(trimmed, sample_rate)


def normalize_to_target(
    path: str | Path,
    out_path: str | Path,
    target_lufs: float,
    ceiling_dbtp: float = -1.0,
    allow_limiting: bool = True,
) -> LoudnessResult:
    """Write `path` at `target_lufs`, keeping true peak under `ceiling_dbtp`.

    The peak ceiling is a hard constraint -- it is what stops lossy encoders
    distorting -- while the loudness target is best effort, because a loud
    target is not always reachable and the result says so when it is not.

    Plain gain is tried first. It is transparent, where limiting trades away
    exactly the dynamics a listening test is meant to judge, so limiting only
    engages when the target cannot be reached without it.
    """
    source = Track.load(path)
    original_lufs = _measure_lufs(source.samples, source.sample_rate)
    if not np.isfinite(original_lufs):
        raise ValueError(f"{Path(path).name} is too short or quiet to measure")

    base = source.samples.astype(np.float32)
    here = Path(path)
    pure_gain = target_lufs - original_lufs
    scaled = (base * (10.0 ** (pure_gain / 20.0))).astype(np.float32)
    peak = _true_peak(scaled, source.sample_rate, here)

    limited = False
    passes = 1

    if peak <= ceiling_dbtp + TRUE_PEAK_TOLERANCE_DB:
        # Gain alone gets there with headroom to spare. Nothing is altered.
        samples = scaled
    elif not allow_limiting:
        pure_gain -= peak - ceiling_dbtp
        samples = (base * (10.0 ** (pure_gain / 20.0))).astype(np.float32)
    else:
        limited = True
        # Lower bound: the loudest plain gain that still fits under the ceiling.
        low = pure_gain - (peak - ceiling_dbtp)
        high = low + SEARCH_HEADROOM_DB
        samples, achieved = _limit_and_trim(base, low, source.sample_rate, ceiling_dbtp, here)

        for passes in range(1, SEARCH_STEPS + 1):
            # Land at or just under the target, never over. Overshooting a
            # published target can trip a stricter peak ceiling -- Spotify asks
            # for -2 dBTP on anything louder than -14 LUFS -- so a master that
            # misses on the loud side fails a rule it would otherwise pass.
            if target_lufs - TOLERANCE_LU <= achieved <= target_lufs:
                break
            middle = (low + high) / 2.0
            samples, achieved = _limit_and_trim(
                base, middle, source.sample_rate, ceiling_dbtp, here
            )
            if achieved < target_lufs:
                low = middle
            else:
                high = middle
        pure_gain = (low + high) / 2.0

    achieved = _measure_lufs(samples, source.sample_rate)
    written = Track(
        path=here, samples=samples, sample_rate=source.sample_rate
    ).write(out_path)

    return LoudnessResult(
        target_lufs=target_lufs,
        achieved_lufs=achieved,
        true_peak_dbtp=_true_peak(samples, source.sample_rate, written),
        gain_applied_db=pure_gain,
        limited=limited,
        passes=passes,
    )
