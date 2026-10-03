"""The vocal mix chain — `MixChain` — built on Spotify's `pedalboard`."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class ChainParams:
    """Every knob on the vocal chain.

    The defaults are the hand-picked starting values, deliberately conservative.
    `producer tune` searches this space against a measured target; nothing here
    is learned until you run it.
    """

    highpass_hz: float = 80.0
    gate_threshold_db: float = -40.0
    comp_threshold_db: float = -18.0
    comp_ratio: float = 3.0
    deess_hz: float = DE_ESS_HZ
    deess_gain_db: float = -4.0
    deess_q: float = 2.0
    reverb_room: float = 0.15
    reverb_wet: float = 0.08

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChainParams":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    @classmethod
    def load(cls, path: str | Path) -> "ChainParams":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path


DEFAULT_PARAMS = ChainParams()


def build_vocal_chain(params: ChainParams = DEFAULT_PARAMS) -> Pedalboard:
    """The vocal-specific processing chain, in signal order."""
    return Pedalboard([
        # 1. Remove rumble, mic handling and HVAC below the vocal fundamental.
        HighpassFilter(cutoff_frequency_hz=params.highpass_hz),
        # 2. Gate the gaps between phrases so room noise doesn't ride up under compression.
        NoiseGate(threshold_db=params.gate_threshold_db, ratio=10.0, attack_ms=1.0, release_ms=100.0),
        # 3. Even out performance dynamics before any upward gain.
        Compressor(
            threshold_db=params.comp_threshold_db,
            ratio=params.comp_ratio,
            attack_ms=5.0,
            release_ms=120.0,
        ),
        # 4. De-ess: a narrow cut on the sibilance band rather than a full de-esser.
        PeakFilter(cutoff_frequency_hz=params.deess_hz, gain_db=params.deess_gain_db, q=params.deess_q),
        # 5. Subtle space. dry_level is raised off its 0.4 default so the chain
        #    stays near unity gain instead of dropping ~8 dB.
        Reverb(
            room_size=params.reverb_room,
            wet_level=params.reverb_wet,
            dry_level=1.0 - params.reverb_wet,
            width=1.0,
        ),
    ])


def mix_vocal(
    path: str | Path, out_path: str | Path, params: ChainParams = DEFAULT_PARAMS
) -> Path:
    """Run the vocal at `path` through the chain at its native rate."""
    track = Track.load(path)
    board = build_vocal_chain(params)

    # pedalboard wants float32 and reads (samples, channels) for 2-D input.
    samples = np.ascontiguousarray(track.samples, dtype=np.float32)
    processed = board(samples, track.sample_rate, reset=True)

    return Track(
        path=Path(path),
        samples=np.asarray(processed, dtype=np.float32),
        sample_rate=track.sample_rate,
    ).write(out_path)
