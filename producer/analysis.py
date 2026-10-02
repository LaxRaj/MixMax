"""Vocal analysis — the `VocalAnalysis` stage of the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import librosa
import numpy as np

from producer.audio import Track

# pyin needs a search range; these bracket the human singing voice generously.
PITCH_FMIN = float(librosa.note_to_hz("C2"))   # ~65 Hz
PITCH_FMAX = float(librosa.note_to_hz("C7"))   # ~2093 Hz


@dataclass
class VocalAnalysis:
    """Tempo, pitch range and dynamic range for one vocal."""

    tempo_bpm: float
    pitch_min_hz: float
    pitch_max_hz: float
    dynamic_range_db: float

    def to_dict(self) -> dict:
        return asdict(self)


def _finite(value: float, fallback: float = 0.0) -> float:
    """Guard every number we emit: downstream JSON and reports must stay clean."""
    value = float(value)
    return value if np.isfinite(value) else fallback


def _tempo(y: np.ndarray, sr: int) -> float:
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa may hand back a 0-d or 1-element array depending on version.
    return _finite(np.atleast_1d(tempo)[0])


def _pitch_range(y: np.ndarray, sr: int) -> tuple[float, float]:
    f0, voiced_flag, _ = librosa.pyin(y=y, fmin=PITCH_FMIN, fmax=PITCH_FMAX, sr=sr)
    voiced = f0[voiced_flag & np.isfinite(f0)] if f0 is not None else np.array([])
    if voiced.size == 0:
        # Unvoiced or pure-noise input: report a collapsed range rather than NaN.
        return 0.0, 0.0
    return _finite(voiced.min()), _finite(voiced.max())


def _dynamic_range_db(y: np.ndarray, sr: int) -> float:
    """Spread between the loudest and a quiet-but-not-silent RMS frame, in dB."""
    rms = librosa.feature.rms(y=y)[0]
    rms = rms[rms > 0]
    if rms.size == 0:
        return 0.0
    loud = np.percentile(rms, 95)
    quiet = np.percentile(rms, 5)
    if quiet <= 0:
        return 0.0
    return _finite(20.0 * np.log10(loud / quiet))


def analyze_vocal(path: str | Path) -> dict:
    """Analyze the vocal at `path`, returning plain JSON-safe values."""
    track = Track.load(path)
    y = np.ascontiguousarray(track.mono(), dtype=np.float32)
    sr = track.sample_rate

    pitch_min, pitch_max = _pitch_range(y, sr)
    analysis = VocalAnalysis(
        tempo_bpm=round(_tempo(y, sr), 2),
        pitch_min_hz=round(pitch_min, 2),
        pitch_max_hz=round(pitch_max, 2),
        dynamic_range_db=round(_dynamic_range_db(y, sr), 2),
    )
    return analysis.to_dict()
