"""What a vocal asks of its backing: tempo, key, shape — measured, or left blank.

A generator given a wrong tempo or key writes a confident backing for a song
that is not this one, and nothing downstream can repair that. So every field
here is either measured above a named floor or reported as unmeasured with the
reason. Pure analysis; nothing in this module touches the network.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np

from producer.analysis import PITCH_FMAX, PITCH_FMIN, fold_tempo
from producer.combine import MIN_GRID_PERIODICITY, measure_pulse
from producer.library import MAJOR_PROFILE, MINOR_PROFILE, PITCH_CLASSES

ANALYSIS_SR = 22050
HOP = 512

# Key confidence is the gap between the best and second-best Krumhansl-Schmuckler
# correlation. The runner-up is usually the relative or parallel key, so a small
# gap means the vocal does not say which of two keys it is in. Measured on the
# material here: a sung melody 0.13, a known-key arpeggio 0.30; rapped vocals
# 0.02-0.03, white noise 0.02, one held note 0.0003. 0.05 sits between the two
# groups, and below it the honest answer is "no key", not the top of a near-tie.
KEY_CONFIDENCE_FLOOR = 0.05

# pyin alone marks a third of white-noise frames as voiced, and their averaged
# chroma can clear the key floor by chance (margin 0.22 on one seed). Spectral
# flatness tells the two apart with room to spare: noise reads 0.56, every real
# vocal here 0.003-0.004. A frame counts as pitched only if it is also tonal.
TONAL_FLATNESS_MAX = 0.1

# Fewer pitched frames than this and there is no melody to take a key from.
MIN_VOICED_S = 2.0

# The tracked tempo is quantised to whole analysis frames (about 4% at 100 BPM).
# The mean beat interval is finer, and is used when it agrees with the tracker
# to within this ratio; a larger disagreement means the tracker lost the beat.
TEMPO_REFINE_TOLERANCE = 0.08
MIN_BEATS = 8

# Without a tempo there are no bars, so the contour falls back to fixed windows.
CONTOUR_WINDOW_S = 2.0
BEATS_PER_BAR = 4


@dataclass
class VocalSpec:
    """Measured facts about one vocal. `None` means unmeasured, never unknown-but-guessed."""

    duration_s: float
    tempo_bpm: float | None
    tempo_confidence: float
    key: str | None
    key_confidence: float
    voiced_range_hz: list[float] | None
    sections: list[dict] = field(default_factory=list)
    melody_contour: list[int | None] = field(default_factory=list)
    contour_unit: str = ""
    contour_reference: str = ""
    lyrics: str | None = None
    language: str | None = None
    unmeasured: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_hints(self) -> str:
        """Plain-language constraints for a generator. Unmeasured fields are left out."""
        hints: list[str] = []
        if self.tempo_bpm is not None:
            hints.append(f"around {self.tempo_bpm:.0f} BPM")
        if self.key is not None:
            hints.append(self.key)
        if self.sections:
            # Letters mark sections that sound alike; nothing here knows a verse from a chorus.
            hints.append("form " + "-".join(s["label"] for s in self.sections))
        if self.duration_s > 0:
            hints.append(f"about {self.duration_s:.0f} seconds long")
        if self.language:
            hints.append(f"vocal in {self.language}")
        return ", ".join(hints)


def _finite(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def estimate_key(chroma: np.ndarray) -> tuple[str | None, float, str]:
    """Krumhansl-Schmuckler over a chroma matrix: (key, confidence, why not)."""
    if chroma.size == 0:
        return None, 0.0, "no pitched material"
    profile = chroma.mean(axis=1)
    if not np.any(profile) or float(np.std(profile)) == 0.0:
        return None, 0.0, "no pitched material"

    scores: list[tuple[float, str]] = []
    for index in range(12):
        for mode, template in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            score = float(np.corrcoef(profile, np.roll(template, index))[0, 1])
            if math.isfinite(score):
                scores.append((score, f"{PITCH_CLASSES[index]} {mode}"))
    if len(scores) < 2:
        return None, 0.0, "no pitched material"

    scores.sort(reverse=True)
    (best, name), (second, runner_up) = scores[0], scores[1]
    confidence = round(max(best - second, 0.0), 3)
    if confidence < KEY_CONFIDENCE_FLOOR:
        return None, confidence, (
            f"{name} and {runner_up} fit almost equally (margin {confidence:.3f}, "
            f"floor {KEY_CONFIDENCE_FLOOR}); the vocal does not settle on one"
        )
    return name, confidence, ""


def estimate_tempo(y: np.ndarray, sr: int) -> tuple[float | None, float, str]:
    """(tempo folded into the musical range, periodicity, why not)."""
    tempo, beats, periodicity = measure_pulse(y, sr)
    periodicity = round(periodicity if math.isfinite(periodicity) else 0.0, 3)
    if not math.isfinite(tempo) or tempo <= 0:
        return None, periodicity, "no tempo could be tracked"
    if periodicity < MIN_GRID_PERIODICITY or len(beats) < MIN_BEATS:
        return None, periodicity, (
            f"no steady pulse (periodicity {periodicity:.2f}, needs {MIN_GRID_PERIODICITY}); "
            "an unaccompanied vocal has no beat to measure"
        )

    times = librosa.frames_to_time(beats, sr=sr, hop_length=HOP)
    refined = 60.0 / ((times[-1] - times[0]) / (len(times) - 1))
    if abs(refined / tempo - 1.0) <= TEMPO_REFINE_TOLERANCE:
        tempo = refined
    return round(fold_tempo(tempo), 1), periodicity, ""


def _sections(path: Path) -> tuple[list[dict], str]:
    from producer.structure import analyze_structure

    try:
        structure = analyze_structure(path)
    except Exception as exc:  # too short to segment, or a format soundfile cannot read
        return [], f"could not segment ({type(exc).__name__}: {exc})"
    return [
        {"label": s.label, "start_s": s.start_s, "end_s": s.end_s, "energy": s.energy_db}
        for s in structure.sections
    ], ""


def _contour(
    midi: np.ndarray, voiced: np.ndarray, sr: int, tempo: float | None, key: str | None
) -> tuple[list[int | None], str, str]:
    """Median pitch per bar (or per window), in semitones from a reference."""
    pitched = midi[voiced]
    if pitched.size == 0:
        return [], "", ""

    overall = float(np.median(pitched))
    if key is not None:
        # The tonic in the octave nearest the singer's own centre.
        tonic_class = PITCH_CLASSES.index(key.split()[0])
        reference = tonic_class + 12 * round((overall - tonic_class) / 12.0)
        described = f"semitones from the tonic of {key}"
    else:
        reference = overall
        described = "semitones from the vocal's own median pitch (no key measured)"

    if tempo is not None:
        window_s, unit = BEATS_PER_BAR * 60.0 / tempo, "bar"
    else:
        window_s, unit = CONTOUR_WINDOW_S, f"{CONTOUR_WINDOW_S:g}s window"
    per_window = max(int(round(window_s * sr / HOP)), 1)

    contour: list[int | None] = []
    for start in range(0, len(midi), per_window):
        chunk = midi[start:start + per_window][voiced[start:start + per_window]]
        contour.append(int(round(float(np.median(chunk)) - reference)) if chunk.size else None)
    return contour, unit, described


def analyze_spec(
    vocal: str | Path, lyrics: str | None = None, language: str | None = None
) -> VocalSpec:
    """Measure `vocal`. Lyrics and language are taken as given, never inferred."""
    path = Path(vocal)
    y, sr = librosa.load(str(path), sr=ANALYSIS_SR, mono=True)
    y = np.ascontiguousarray(y, dtype=np.float32)
    duration = len(y) / float(sr)
    unmeasured: dict[str, str] = {}

    tempo, tempo_confidence, why = estimate_tempo(y, sr)
    if tempo is None:
        unmeasured["tempo_bpm"] = why

    f0, voiced_flag, _ = librosa.pyin(y, fmin=PITCH_FMIN, fmax=PITCH_FMAX, sr=sr, hop_length=HOP)
    flatness = librosa.feature.spectral_flatness(y=y, hop_length=HOP)[0]
    frames = min(len(f0), len(flatness))
    f0, voiced_flag, flatness = f0[:frames], voiced_flag[:frames], flatness[:frames]
    voiced = voiced_flag & np.isfinite(f0) & (flatness < TONAL_FLATNESS_MAX)
    voiced_s = float(voiced.sum()) * HOP / sr

    voiced_range: list[float] | None = None
    key, key_confidence = None, 0.0
    if voiced_s < MIN_VOICED_S:
        reason = f"only {voiced_s:.1f}s of pitched sound (needs {MIN_VOICED_S:g}s)"
        unmeasured["key"] = reason
        if voiced_s == 0:
            unmeasured["voiced_range_hz"] = "no pitched sound"
    else:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
        frames = min(chroma.shape[1], len(voiced))
        key, key_confidence, why = estimate_key(chroma[:, :frames][:, voiced[:frames]])
        if key is None:
            unmeasured["key"] = why
    if voiced.any():
        pitched = f0[voiced]
        voiced_range = [round(float(pitched.min()), 1), round(float(pitched.max()), 1)]

    midi = np.where(voiced, librosa.hz_to_midi(np.where(voiced, f0, 440.0)), np.nan)
    contour, unit, reference = _contour(midi, voiced, sr, tempo, key)
    if not contour:
        unmeasured["melody_contour"] = "no pitched sound"

    sections, why = _sections(path)
    if not sections:
        unmeasured["sections"] = why or "no sections found"

    if lyrics is None:
        unmeasured["lyrics"] = "not supplied; nothing here transcribes audio"
    if language is None:
        unmeasured["language"] = "not supplied; nothing here identifies a language"

    return VocalSpec(
        duration_s=round(duration, 2),
        tempo_bpm=_finite(tempo) if tempo is not None else None,
        tempo_confidence=tempo_confidence,
        key=key,
        key_confidence=key_confidence,
        voiced_range_hz=voiced_range,
        sections=sections,
        melody_contour=contour,
        contour_unit=unit,
        contour_reference=reference,
        lyrics=lyrics,
        language=language,
        unmeasured=unmeasured,
    )
