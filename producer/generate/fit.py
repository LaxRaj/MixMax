"""Make a candidate backing sit under the vocal, or say why it cannot.

A generated backing is the one part of a song this pipeline does not control.
`fit` is the check in front of it: lock its tempo to the vocal's, look for a
key clash, find where the vocal sits on its grid. A candidate that cannot be
repaired within small, named limits is rejected with the reason, because a
confident mix of two things that do not belong together is the worst output
this could produce.

What cannot be measured is not checked, and the report says so. A vocal with
no measurable tempo or key (an a cappella rap has neither) gets a candidate
that passed nothing on those counts, not one that passed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as pyln
from pedalboard import time_stretch
from scipy.signal import butter, sosfiltfilt

from producer.audio import Track
from producer.combine import (
    DEFAULT_VOCAL_OVER_BEAT_DB,
    Alignment,
    align_to_grid,
    find_offset,
)
from producer.generate.spec import (
    ANALYSIS_SR,
    HOP,
    VocalSpec,
    analyze_spec,
    estimate_tempo,
)
from producer.library import MAJOR_PROFILE, MINOR_PROFILE, PITCH_CLASSES

# Beyond this a time-stretch is audible on drums (smeared transients, flammed
# hits), and a candidate that far off was written at a different tempo rather
# than slightly missing this one. A default, not yet tuned against listeners.
MAX_STRETCH = 0.08

# Tempos closer than this are left alone: 0.2% is 0.3s of drift over a
# 2.5-minute song, about what the tempo measurement itself can resolve.
TEMPO_LOCK_DEADBAND = 0.002

# Key clash is how much better the candidate's harmony fits its own best key
# than the vocal's key (or its relative), as a gap in Krumhansl-Schmuckler
# correlation. Measured: the right key 0.00, a key a fifth away (one note
# different) 0.10, F# minor under an A minor vocal 0.46, and real tonal tracks
# transposed by one or two semitones 0.2-1.0. A default, not yet tuned against
# listeners.
KEY_CLASH_MAX = 0.2

# A larger pitch shift changes the character of the instruments, not just the key.
MAX_SHIFT_SEMITONES = 2

# Below this the candidate's own best key barely fits it: mostly drums, or
# harmony too vague to clash with anything.
WEAK_TONALITY = 0.35

# The band a voice is understood in.
VOCAL_BAND_HZ = (1000.0, 4000.0)

# Candidate energy in the vocal band, relative to the vocal's, once balanced as
# `combine` would balance them. Above this it is noted; it is never a rejection,
# because no listening result here says where masking becomes a fault.
MASKING_NOTE_DB = -3.0

# The vocal counts as active within this of its own loud frames.
ACTIVE_BELOW_PEAK_DB = 25.0


@dataclass
class FitResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)    # why it was rejected
    notes: list[str] = field(default_factory=list)      # what was done, and what was not checked
    output: str | None = None
    vocal_tempo_bpm: float | None = None
    candidate_tempo_bpm: float | None = None
    tempo_ratio: float | None = None
    stretch: float = 1.0                                # playback-rate factor applied
    vocal_key: str | None = None
    candidate_key: str | None = None
    key_clash_score: float | None = None
    pitch_shift_semitones: int = 0
    alignment: dict | None = None
    offset_s: float = 0.0                               # the offset to mix at; 0 unless trusted
    vocal_masking_db: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── key ─────────────────────────────────────────────────────────────────────


def _all_keys() -> list[str]:
    return [f"{name} {mode}" for name in PITCH_CLASSES for mode in ("major", "minor")]


def _relative(key: str) -> str:
    name, mode = key.split()
    index = PITCH_CLASSES.index(name)
    if mode == "minor":
        return f"{PITCH_CLASSES[(index + 3) % 12]} major"
    return f"{PITCH_CLASSES[(index - 3) % 12]} minor"


def _correlation(profile: np.ndarray, key: str) -> float:
    name, mode = key.split()
    template = MAJOR_PROFILE if mode == "major" else MINOR_PROFILE
    score = float(np.corrcoef(profile, np.roll(template, PITCH_CLASSES.index(name)))[0, 1])
    return score if math.isfinite(score) else -1.0


def harmony_profile(y: np.ndarray, sr: int) -> np.ndarray | None:
    """Average chroma of the pitched part of a mix; None if there is none."""
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=sr, norm=None)
    profile = chroma.mean(axis=1)
    if not np.all(np.isfinite(profile)) or float(np.std(profile)) == 0.0:
        return None
    return profile


def best_key(profile: np.ndarray, shift: int = 0) -> tuple[str, float]:
    """The key that fits a harmony profile best, and how well it fits."""
    shifted = np.roll(profile, shift)
    key = max(_all_keys(), key=lambda k: _correlation(shifted, k))
    return key, round(_correlation(shifted, key), 3)


def key_clash(profile: np.ndarray, vocal_key: str, shift: int = 0) -> float:
    """How much better the candidate fits its own best key than the vocal's.

    0 when no key fits the candidate better than the vocal's does. A relative
    major or minor shares every note, so it counts as the same key. `shift`
    asks the question of the candidate transposed by that many semitones.
    """
    shifted = np.roll(profile, shift)
    _, best = best_key(profile, shift)
    here = max(_correlation(shifted, vocal_key), _correlation(shifted, _relative(vocal_key)))
    return round(max(best - here, 0.0), 3)


# ── tempo ───────────────────────────────────────────────────────────────────


def fold_ratio(ratio: float) -> float:
    """Half and double time are the same groove; bring the ratio next to 1."""
    while ratio > math.sqrt(2.0):
        ratio /= 2.0
    while ratio < 1.0 / math.sqrt(2.0):
        ratio *= 2.0
    return ratio


# ── masking ─────────────────────────────────────────────────────────────────


def _lufs(y: np.ndarray, sr: int) -> float:
    meter = pyln.Meter(sr)
    if len(y) < meter.block_size * sr:
        return float("-inf")
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(meter.integrated_loudness(y))


def vocal_masking_db(vocal: np.ndarray, candidate: np.ndarray, sr: int, offset_s: float) -> float | None:
    """Candidate level over vocal level in the vocal band, while the vocal is active.

    Both are first balanced the way `combine` balances them, so the number
    describes the mix that would be made rather than two unrelated files.
    """
    shift = int(round(offset_s * sr))
    if shift > 0:
        candidate = candidate[shift:]
    elif shift < 0:
        vocal = vocal[-shift:]
    n = min(len(vocal), len(candidate))
    if n < sr:
        return None
    vocal, candidate = vocal[:n], candidate[:n]

    vocal_lufs, candidate_lufs = _lufs(vocal, sr), _lufs(candidate, sr)
    if not (math.isfinite(vocal_lufs) and math.isfinite(candidate_lufs)):
        return None
    candidate = candidate * 10.0 ** (((vocal_lufs - DEFAULT_VOCAL_OVER_BEAT_DB) - candidate_lufs) / 20.0)

    sos = butter(4, VOCAL_BAND_HZ, btype="bandpass", fs=sr, output="sos")
    vocal_band = librosa.feature.rms(y=sosfiltfilt(sos, vocal), hop_length=HOP)[0]
    candidate_band = librosa.feature.rms(y=sosfiltfilt(sos, candidate), hop_length=HOP)[0]

    level = librosa.feature.rms(y=vocal, hop_length=HOP)[0]
    active = level > level.max() * 10.0 ** (-ACTIVE_BELOW_PEAK_DB / 20.0)
    if not active.any():
        return None
    vocal_power = float(np.mean(np.square(vocal_band[active])))
    candidate_power = float(np.mean(np.square(candidate_band[active])))
    if vocal_power <= 0 or candidate_power <= 0:
        return None
    return round(10.0 * math.log10(candidate_power / vocal_power), 1)


# ── the fit ─────────────────────────────────────────────────────────────────


def _spec_values(spec: VocalSpec | dict | None, vocal: Path) -> tuple[float | None, str | None]:
    if spec is None:
        spec = analyze_spec(vocal)
    if isinstance(spec, VocalSpec):
        return spec.tempo_bpm, spec.key
    return spec.get("tempo_bpm"), spec.get("key")


def _mono(path: Path) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=ANALYSIS_SR, mono=True)
    return np.ascontiguousarray(y, dtype=np.float32)


def fit_candidate(
    vocal: str | Path,
    candidate: str | Path,
    spec: VocalSpec | dict | None = None,
    out_path: str | Path | None = None,
) -> FitResult:
    """Fit `candidate` to `vocal`. Writes the fitted audio to `out_path` if it passes.

    Without `out_path` nothing is rendered: the report says what would be done,
    and alignment and masking are measured on the candidate as it stands.

    The alignment is reported and the offset to mix at is returned; the audio
    itself is not moved in time, so `combine` remains the one place a vocal is
    laid over a beat.
    """
    vocal, candidate = Path(vocal), Path(candidate)
    vocal_tempo, vocal_key = _spec_values(spec, vocal)
    result = FitResult(passed=True, vocal_tempo_bpm=vocal_tempo, vocal_key=vocal_key)

    candidate_y = _mono(candidate)
    sr = ANALYSIS_SR

    # Tempo: stretch within the limit, reject beyond it.
    candidate_tempo, periodicity, why = estimate_tempo(candidate_y, sr)
    result.candidate_tempo_bpm = candidate_tempo
    if vocal_tempo is None:
        result.notes.append(
            "Tempo not checked: the vocal has no measurable tempo, so the candidate's "
            + (f"{candidate_tempo:.1f} BPM is unverified against it."
               if candidate_tempo is not None else "tempo is unverified against it.")
        )
    elif candidate_tempo is None:
        result.notes.append(f"Tempo not checked: the candidate has {why}.")
    else:
        ratio = fold_ratio(vocal_tempo / candidate_tempo)
        result.tempo_ratio = round(ratio, 4)
        off = abs(ratio - 1.0)
        if off > MAX_STRETCH:
            result.reasons.append(
                f"Tempo: the candidate is at {candidate_tempo:.1f} BPM and the vocal at "
                f"{vocal_tempo:.1f} BPM, {off * 100:.1f}% apart. Stretching more than "
                f"{MAX_STRETCH * 100:.0f}% is audible, so it was not attempted."
            )
        elif off > TEMPO_LOCK_DEADBAND:
            result.stretch = round(ratio, 5)
            result.notes.append(
                f"Tempo: stretched {candidate_tempo:.1f} -> {vocal_tempo:.1f} BPM "
                f"({(ratio - 1.0) * 100:+.1f}%)."
            )

    # Key: shift within the limit, reject beyond it.
    profile = harmony_profile(candidate_y, sr)
    if profile is None:
        result.notes.append("Key not checked: the candidate has no pitched content.")
    else:
        own_key, own_fit = best_key(profile)
        # A key and its relative share every note; chroma cannot tell them apart.
        own_named = f"{own_key} (or its relative, {_relative(own_key)})"
        result.candidate_key = own_key if own_fit >= WEAK_TONALITY else None
        if vocal_key is None:
            result.notes.append(
                "Key not checked: the vocal has no measured key, so a clash with the "
                + (f"candidate's {own_named} cannot be ruled out."
                   if result.candidate_key else "candidate cannot be ruled out.")
            )
        else:
            clash = key_clash(profile, vocal_key)
            result.key_clash_score = clash
            if own_fit < WEAK_TONALITY:
                result.notes.append(
                    f"Key: the candidate's harmony is faint (best key fits at {own_fit:.2f}), "
                    "so there is little for the vocal to clash with."
                )
            if clash > KEY_CLASH_MAX:
                options = sorted(
                    (s for s in range(-MAX_SHIFT_SEMITONES, MAX_SHIFT_SEMITONES + 1) if s != 0),
                    key=lambda s: (abs(s), key_clash(profile, vocal_key, s)),
                )
                fix = next((s for s in options
                            if key_clash(profile, vocal_key, s) <= KEY_CLASH_MAX), None)
                if fix is None:
                    result.reasons.append(
                        f"Key: the candidate is in {own_named} and the vocal in {vocal_key} "
                        f"(clash {clash:.2f}, limit {KEY_CLASH_MAX}). No shift of "
                        f"{MAX_SHIFT_SEMITONES} semitones or less fixes it."
                    )
                else:
                    result.pitch_shift_semitones = fix
                    result.key_clash_score = key_clash(profile, vocal_key, fix)
                    result.notes.append(
                        f"Key: the candidate was in {own_named} against the vocal's {vocal_key} "
                        f"(clash {clash:.2f}); shifted {fix:+d} semitone(s), clash now "
                        f"{result.key_clash_score:.2f}."
                    )

    if result.reasons:
        result.passed = False
        return result

    # Render the repaired candidate (or pass it through untouched).
    track = Track.load(candidate)
    fitted = candidate
    if out_path is not None:
        out_path = Path(out_path)
        samples = track.samples
        if result.stretch != 1.0 or result.pitch_shift_semitones != 0:
            channels_first = np.ascontiguousarray(
                samples[None, :] if samples.ndim == 1 else samples.T, dtype=np.float32
            )
            stretched = time_stretch(
                channels_first, track.sample_rate,
                stretch_factor=result.stretch,
                pitch_shift_in_semitones=float(result.pitch_shift_semitones),
            )
            samples = stretched[0] if samples.ndim == 1 else stretched.T
        fitted = Track(path=candidate, samples=samples.astype(np.float32),
                       sample_rate=track.sample_rate).write(out_path)
        result.output = str(fitted)

    # Where the vocal sits on the fitted candidate's grid. Never apply a weak one.
    alignment: Alignment = align_to_grid(vocal, fitted)
    if not alignment.trustworthy:
        alignment = find_offset(vocal, fitted)
    result.alignment = alignment.to_dict()
    if alignment.trustworthy:
        result.offset_s = alignment.offset_s
    else:
        result.notes.append(
            f"Alignment not applied: {alignment.method} (confidence "
            f"{alignment.confidence:.2f}). The vocal is laid at the start of the backing."
        )

    result.vocal_masking_db = vocal_masking_db(_mono(vocal), _mono(fitted), sr, result.offset_s)
    if result.vocal_masking_db is not None and result.vocal_masking_db > MASKING_NOTE_DB:
        result.notes.append(
            f"Masking: in the {VOCAL_BAND_HZ[0] / 1000:g}-{VOCAL_BAND_HZ[1] / 1000:g} kHz band "
            f"the backing sits {result.vocal_masking_db:+.1f} dB against the vocal while it "
            "sings. Expect the words to be harder to follow."
        )
    return result
