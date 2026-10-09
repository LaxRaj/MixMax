"""G1 — VocalSpec measures what it can and leaves the rest blank."""

from __future__ import annotations

import json
import math
from pathlib import Path

import librosa
import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.generate.spec import KEY_CONFIDENCE_FLOOR, VocalSpec, analyze_spec

SR = 22050


def _write(path: Path, samples: np.ndarray) -> Path:
    sf.write(str(path), samples.astype(np.float32), SR)
    return path


def arpeggio(midi_notes: list[int], note_s: float = 0.5, repeats: int = 3) -> np.ndarray:
    """A sine melody whose notes spell out a key."""
    n = int(SR * note_s)
    window = np.hanning(n)
    notes = [
        0.3 * np.sin(2 * np.pi * librosa.midi_to_hz(m) * np.arange(n) / SR) * window
        for m in midi_notes * repeats
    ]
    return np.concatenate(notes)


def a_cappella(seconds: float = 14.0) -> np.ndarray:
    """A voice-like tone in phrases of uneven length: pitched, but with no pulse."""
    rng = np.random.default_rng(5)
    t = np.arange(int(SR * seconds)) / SR
    freq = 220.0 + 25.0 * np.sin(2 * np.pi * 0.23 * t) + 6.0 * np.sin(2 * np.pi * 5.1 * t)
    tone = 0.3 * np.sin(np.cumsum(2 * np.pi * freq / SR))
    gate = np.zeros_like(t)
    at = 0.0
    while at < seconds:
        length = rng.uniform(0.9, 2.6)
        a, b = int(at * SR), int(min(at + length, seconds) * SR)
        gate[a:b] = np.hanning(b - a) if b - a > 1 else 0.0
        at += length + rng.uniform(0.2, 0.9)
    return tone * gate


# A natural minor: A C E up and down, then the rest of the scale.
A_MINOR = [57, 60, 64, 69, 64, 60, 57, 59, 60, 62, 64, 57]


def _all_numbers(value) -> list[float]:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _all_numbers(v)]
    return [n for v in value for n in _all_numbers(v)]


def test_known_key_is_detected_above_the_floor(tmp_path: Path) -> None:
    spec = analyze_spec(_write(tmp_path / "arp.wav", arpeggio(A_MINOR)))

    assert spec.key == "A minor"
    assert spec.key_confidence >= KEY_CONFIDENCE_FLOOR
    assert "key" not in spec.unmeasured
    low, high = spec.voiced_range_hz
    assert low == pytest.approx(220.0, rel=0.05) and high == pytest.approx(440.0, rel=0.05)
    assert spec.contour_unit == "bar"
    assert "tonic of A minor" in spec.contour_reference


def test_noise_has_no_key(tmp_path: Path) -> None:
    for seed in range(5):   # seed 4 clears the key floor without the tonal gate
        noise = np.random.default_rng(seed).normal(0, 0.1, SR * 8)
        spec = analyze_spec(_write(tmp_path / f"noise{seed}.wav", noise))
        assert spec.key is None
        assert spec.unmeasured["key"]


def test_a_single_held_note_has_no_key(target_wav: Path) -> None:
    # One pitch fits a major and a minor key equally; choosing would be a coin toss.
    spec = analyze_spec(target_wav)
    assert spec.key is None
    assert spec.key_confidence < KEY_CONFIDENCE_FLOOR


def test_click_track_tempo_is_measured(click_120_wav: Path) -> None:
    spec = analyze_spec(click_120_wav)
    assert spec.tempo_bpm == pytest.approx(120.0, abs=5.0)
    assert "tempo_bpm" not in spec.unmeasured


def test_tempo_is_always_folded(tmp_path: Path) -> None:
    from tests.conftest import click_track

    spec = analyze_spec(_write(tmp_path / "fast.wav", click_track(200.0, 10.0, SR)))
    assert spec.tempo_bpm is not None
    assert 65.0 <= spec.tempo_bpm <= 185.0


def test_a_cappella_has_no_tempo(tmp_path: Path) -> None:
    spec = analyze_spec(_write(tmp_path / "acappella.wav", a_cappella()))

    assert spec.tempo_bpm is None
    assert "pulse" in spec.unmeasured["tempo_bpm"]
    assert spec.voiced_range_hz is not None
    # No tempo means no bars, and the contour says which unit it used instead.
    assert spec.contour_unit.endswith("window")
    assert "BPM" not in spec.to_prompt_hints()


def test_every_value_is_finite_or_none(tmp_path: Path, target_wav: Path, click_120_wav: Path) -> None:
    paths = [
        target_wav, click_120_wav,
        _write(tmp_path / "arp.wav", arpeggio(A_MINOR)),
        _write(tmp_path / "noise.wav", np.random.default_rng(1).normal(0, 0.1, SR * 6)),
        _write(tmp_path / "silence.wav", np.zeros(SR * 4)),
    ]
    for path in paths:
        payload = analyze_spec(path).to_dict()
        json.dumps(payload, allow_nan=False)   # raises on NaN or inf
        assert all(math.isfinite(n) for n in _all_numbers(payload)), path


def test_silence_measures_nothing_and_says_so(tmp_path: Path) -> None:
    spec = analyze_spec(_write(tmp_path / "silence.wav", np.zeros(SR * 4)))
    assert spec.tempo_bpm is None and spec.key is None and spec.voiced_range_hz is None
    assert {"tempo_bpm", "key", "voiced_range_hz", "melody_contour"} <= set(spec.unmeasured)


def test_prompt_hints_omit_what_was_not_measured() -> None:
    measured = VocalSpec(
        duration_s=95.0, tempo_bpm=98.0, tempo_confidence=0.6, key="F# minor",
        key_confidence=0.2, voiced_range_hz=[180.0, 420.0],
        sections=[{"label": "A"}, {"label": "B"}, {"label": "A"}],
    )
    hints = measured.to_prompt_hints()
    assert "around 98 BPM" in hints and "F# minor" in hints and "A-B-A" in hints

    blank = VocalSpec(duration_s=30.0, tempo_bpm=None, tempo_confidence=0.1, key=None,
                      key_confidence=0.0, voiced_range_hz=None)
    assert "BPM" not in blank.to_prompt_hints()
    assert "minor" not in blank.to_prompt_hints() and "major" not in blank.to_prompt_hints()
    assert "None" not in blank.to_prompt_hints()


def test_lyrics_and_language_are_taken_as_given(tmp_path: Path, target_wav: Path) -> None:
    spec = analyze_spec(target_wav)
    assert spec.lyrics is None and spec.language is None
    assert {"lyrics", "language"} <= set(spec.unmeasured)

    spec = analyze_spec(target_wav, lyrics="line one\nline two", language="Hindi")
    assert spec.lyrics == "line one\nline two" and spec.language == "Hindi"
    assert "Hindi" in spec.to_prompt_hints()


def test_spec_cli_prints_and_writes_json(tmp_path: Path, target_wav: Path) -> None:
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("hello")
    out = tmp_path / "spec.json"
    result = CliRunner().invoke(cli, [
        "spec", "--vocal", str(target_wav), "--lyrics", str(lyrics), "--out", str(out),
    ])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed == json.loads(out.read_text())
    assert printed["lyrics"] == "hello"
    assert printed["tempo_bpm"] is None
