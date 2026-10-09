"""Synthetic vocals and backings with known tempo and key, for the generation tests.

Nothing here sounds like music. Each signal exists because one property of it
is known exactly, so a measurement of that property has a ground truth.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SR = 22050

# Natural minor, as semitones above the tonic.
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)

A2 = 45   # MIDI


def write(path: Path, samples: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples.astype(np.float32), sr)
    return path


def _tone(midi: float, n: int, harmonics: int = 3) -> np.ndarray:
    t = np.arange(n) / SR
    freq = librosa.midi_to_hz(midi)
    return sum(np.sin(2 * np.pi * freq * (h + 1) * t) / (h + 1) for h in range(harmonics))


def melody_vocal(bpm: float = 100.0, tonic: int = A2 + 12, bars: int = 12) -> np.ndarray:
    """A sung-like line in natural minor: one note per beat, a rest every fourth bar."""
    beat = int(round(SR * 60.0 / bpm))
    degrees = [0, 2, 4, 2, 0, 4, 3, 1, 0, 2, 5, 4, 6, 4, 2, 0]
    out = np.zeros(beat * 4 * bars)
    envelope = np.concatenate([np.linspace(0, 1, beat // 20), np.exp(-np.linspace(0, 3, beat - beat // 20))])
    for i in range(4 * bars):
        if (i // 4) % 4 == 3 and i % 4 >= 2:
            continue   # breathe
        midi = tonic + MINOR_STEPS[degrees[i % len(degrees)]]
        out[i * beat:(i + 1) * beat] = 0.3 * _tone(midi, beat) * envelope
    return out


def backing(bpm: float = 100.0, tonic: int = A2, bars: int = 12, seed: int = 2) -> np.ndarray:
    """Kick, snare, a bass line and a pad, all in natural minor on `tonic`."""
    rng = np.random.default_rng(seed)
    beat = int(round(SR * 60.0 / bpm))
    n = beat * 4 * bars
    out = np.zeros(n)

    hit = np.exp(-np.linspace(0, 8, int(0.2 * SR)))
    kick = np.sin(2 * np.pi * 55 * np.arange(len(hit)) / SR) * hit
    snare = rng.normal(0, 1, len(hit)) * hit * 0.4
    for i in range(4 * bars):
        drum = kick if i % 2 == 0 else snare
        m = min(len(drum), n - i * beat)
        out[i * beat:i * beat + m] += 0.7 * drum[:m]

    # i - VI - III - VII, a bar each: every chord tone is inside the scale.
    roots = [0, 8, 3, 10]
    bar = beat * 4
    for b in range(bars):
        root = tonic + roots[b % 4]
        third = 4 if roots[b % 4] in (8, 3, 10) else 3   # major on VI, III, VII; minor on i
        out[b * bar:(b + 1) * bar] += 0.35 * _tone(root - 12, bar, harmonics=2)
        for interval in (0, third, 7):
            out[b * bar:(b + 1) * bar] += 0.12 * _tone(root + 12 + interval, bar)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.6
