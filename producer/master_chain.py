"""A mastering chain for finished mixes.

The vocal chain in `mix.py` is for a lone voice: an 80 Hz highpass and a de-ess
cut are right there and ruinous on a full track, where they thin the kick and
dull the hats. This is the other chain -- gentle, broadband, and aimed at a mix
that is already balanced.

Everything here is corrective rather than creative. Without a reference there is
no honest basis for a tonal curve, so this does not invent one: it removes what
is definitely unwanted (subsonic energy), glues what is definitely safe (slow,
low-ratio bus compression), and leaves the tone alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from pedalboard import Compressor, HighpassFilter, Pedalboard

from producer.audio import Track

# Music does not live below this. Subsonic energy costs headroom and makes a
# limiter work for nothing, but go much higher and you start thinning the kick.
SUBSONIC_HZ = 25.0


@dataclass(frozen=True)
class MasterParams:
    """The full-mix chain's settings.

    Deliberately mild. A master is the last place to be making large moves, and
    with no reference to aim at, large moves would be guesses.
    """

    subsonic_hz: float = SUBSONIC_HZ
    glue_threshold_db: float = -16.0
    glue_ratio: float = 1.6
    # Slow attack lets transients through, so the groove keeps its punch;
    # slow release means it rides the song rather than pumping on each hit.
    glue_attack_ms: float = 30.0
    glue_release_ms: float = 250.0
    width: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_MASTER_PARAMS = MasterParams()


def build_master_chain(params: MasterParams = DEFAULT_MASTER_PARAMS) -> Pedalboard:
    """Subsonic cleanup, then gentle glue.

    The highpass is cascaded because pedalboard's is a single pole: 6 dB per
    octave leaves most of a 10 Hz rumble intact when the corner is at 25 Hz.
    Two in series give 12 dB per octave, which actually removes it while
    staying far enough below the kick to leave it alone.
    """
    return Pedalboard([
        HighpassFilter(cutoff_frequency_hz=params.subsonic_hz),
        HighpassFilter(cutoff_frequency_hz=params.subsonic_hz),
        Compressor(
            threshold_db=params.glue_threshold_db,
            ratio=params.glue_ratio,
            attack_ms=params.glue_attack_ms,
            release_ms=params.glue_release_ms,
        ),
    ])


def apply_width(samples: np.ndarray, width: float) -> np.ndarray:
    """Mid/side width control.

    A mono file has no side signal, so this cannot widen one -- it would have to
    invent the difference, and the usual tricks (delay or phase decorrelation)
    buy width by damaging mono fold-down. Left alone instead.
    """
    if samples.ndim != 2 or samples.shape[1] != 2 or width == 1.0:
        return samples
    left, right = samples[:, 0], samples[:, 1]
    mid, side = (left + right) / 2.0, (left - right) / 2.0
    side = side * float(width)
    return np.stack([mid + side, mid - side], axis=1).astype(np.float32)


def is_effectively_mono(track: Track, correlation_floor: float = 0.999) -> bool:
    """True for a mono file, or a stereo one whose channels are identical."""
    if track.channels < 2:
        return True
    left, right = track.samples[:, 0], track.samples[:, 1]
    if left.std() == 0 or right.std() == 0:
        return True
    corr = float(np.corrcoef(left, right)[0, 1])
    return bool(np.isfinite(corr) and corr > correlation_floor)


def master_full_mix(
    path: str | Path,
    out_path: str | Path,
    params: MasterParams = DEFAULT_MASTER_PARAMS,
) -> dict:
    """Run a finished mix through the mastering chain.

    Loudness is not set here -- `normalize_to_target` does that afterwards, so
    the chain and the level are separable and each can be judged on its own.
    """
    track = Track.load(path)
    mono_source = is_effectively_mono(track)

    samples = np.ascontiguousarray(track.samples, dtype=np.float32)
    processed = np.asarray(
        build_master_chain(params)(samples, track.sample_rate, reset=True), dtype=np.float32
    )
    processed = apply_width(processed, params.width)

    written = Track(
        path=Path(path), samples=processed, sample_rate=track.sample_rate
    ).write(out_path)

    return {
        "output": str(written),
        "params": params.to_dict(),
        "mono_source": mono_source,
        "width_applied": params.width if not mono_source else 1.0,
    }
