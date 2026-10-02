"""The automated QA gate — `QAReport` — the thing that says ship or don't."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyloudnorm as pyln

from producer.audio import Track

# Thresholds. These are reasonable streaming-adjacent defaults, NOT learned
# values -- the plan tunes them against real listening feedback from batch runs.
CLIPPING_CEILING = 0.999          # full-scale sample magnitude counted as clipped
LUFS_MIN = -16.0                  # quieter than this and it won't sit with other tracks
LUFS_MAX = -9.0                   # louder than this and streaming will turn it down
MONO_CORRELATION_MIN = -0.5       # below this, the mix partially cancels in mono


@dataclass
class QAReport:
    """Pass/fail plus the specific reasons, in plain words."""

    lufs: float
    clipping_detected: bool
    mono_compatible: bool
    passed: bool
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        # `pass` is a reserved word, so the dataclass field is `passed`.
        return {
            "lufs": self.lufs,
            "clipping_detected": self.clipping_detected,
            "mono_compatible": self.mono_compatible,
            "pass": self.passed,
            "flags": list(self.flags),
        }


def _integrated_lufs(track: Track) -> float:
    """Integrated loudness, or -inf for audio too short/quiet to measure."""
    meter = pyln.Meter(track.sample_rate)
    if track.duration_s < meter.block_size:
        return float("-inf")
    with np.errstate(divide="ignore", invalid="ignore"):
        lufs = meter.integrated_loudness(track.samples)
    return float(lufs)


def _is_clipping(samples: np.ndarray) -> bool:
    return bool(np.any(np.abs(samples) >= CLIPPING_CEILING))


def _mono_compatible(track: Track) -> bool:
    """Mono fold-down check. Mono input is compatible by definition."""
    if track.channels < 2:
        return True
    left = track.samples[:, 0].astype(np.float64)
    right = track.samples[:, 1].astype(np.float64)
    if left.std() == 0 or right.std() == 0:
        return True  # a silent/constant channel carries no phase information
    correlation = float(np.corrcoef(left, right)[0, 1])
    if not np.isfinite(correlation):
        return True
    return correlation > MONO_CORRELATION_MIN


def run_qa(path: str | Path) -> dict:
    """Run every QA check on the file at `path`."""
    track = Track.load(path)

    lufs = _integrated_lufs(track)
    clipping_detected = _is_clipping(track.samples)
    mono_compatible = _mono_compatible(track)
    loudness_ok = bool(np.isfinite(lufs) and LUFS_MIN <= lufs <= LUFS_MAX)

    flags: list[str] = []
    if clipping_detected:
        flags.append("clipping: samples hit full scale")
    if not loudness_ok:
        if not np.isfinite(lufs):
            flags.append("loudness: too short or too quiet to measure")
        elif lufs < LUFS_MIN:
            flags.append(f"loudness: {lufs:.1f} LUFS is quieter than {LUFS_MIN:.0f}")
        else:
            flags.append(f"loudness: {lufs:.1f} LUFS is louder than {LUFS_MAX:.0f}")
    if not mono_compatible:
        flags.append("mono: channels partially cancel when folded to mono")

    report = QAReport(
        lufs=round(lufs, 2) if np.isfinite(lufs) else lufs,
        clipping_detected=clipping_detected,
        mono_compatible=mono_compatible,
        passed=not clipping_detected and loudness_ok and mono_compatible,
        flags=flags,
    )
    return report.to_dict()
