"""The vocal mix chain — `MixChain` — built on Spotify's `pedalboard`."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from pedalboard import (
    Compressor,
    HighpassFilter,
    NoiseGate,
    Pedalboard,
    PeakFilter,
    Reverb,
)

from producer.audio import Track

# De-essing band: sibilance in most voices lives around here.
DE_ESS_HZ = 7000.0


def build_vocal_chain() -> Pedalboard:
    """The vocal-specific processing chain, in signal order.

    Deliberately conservative: these are defaults meant to be tuned against real
    listening feedback (see the plan's riskiest assumption), not learned values.
    """
    return Pedalboard([
        # 1. Remove rumble, mic handling and HVAC below the vocal fundamental.
        HighpassFilter(cutoff_frequency_hz=80.0),
        # 2. Gate the gaps between phrases so room noise doesn't ride up under compression.
        NoiseGate(threshold_db=-40.0, ratio=10.0, attack_ms=1.0, release_ms=100.0),
        # 3. Even out performance dynamics before any upward gain.
        Compressor(threshold_db=-18.0, ratio=3.0, attack_ms=5.0, release_ms=120.0),
        # 4. De-ess: a narrow cut on the sibilance band rather than a full de-esser.
        PeakFilter(cutoff_frequency_hz=DE_ESS_HZ, gain_db=-4.0, q=2.0),
        # 5. Subtle space. dry_level is raised off its 0.4 default so the chain
        #    stays near unity gain instead of dropping ~8 dB.
        Reverb(room_size=0.15, wet_level=0.08, dry_level=0.92, width=1.0),
    ])


def mix_vocal(path: str | Path, out_path: str | Path) -> Path:
    """Run the vocal at `path` through the chain at its native rate."""
    track = Track.load(path)
    board = build_vocal_chain()

    # pedalboard wants float32 and reads (samples, channels) for 2-D input.
    samples = np.ascontiguousarray(track.samples, dtype=np.float32)
    processed = board(samples, track.sample_rate, reset=True)

    return Track(
        path=Path(path),
        samples=np.asarray(processed, dtype=np.float32),
        sample_rate=track.sample_rate,
    ).write(out_path)
