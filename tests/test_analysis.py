"""M1 — vocal analysis returns finite, structurally complete numbers."""

from __future__ import annotations

import math

import pytest
from pathlib import Path

from producer.analysis import analyze_vocal

EXPECTED_KEYS = {"tempo_bpm", "pitch_min_hz", "pitch_max_hz", "dynamic_range_db"}


def test_tempo_detected_on_known_click_track(click_120_wav: Path) -> None:
    analysis = analyze_vocal(click_120_wav)
    assert abs(analysis["tempo_bpm"] - 120.0) <= 5.0, analysis


def test_all_keys_present_and_finite_on_click_track(click_120_wav: Path) -> None:
    analysis = analyze_vocal(click_120_wav)
    assert set(analysis) == EXPECTED_KEYS
    assert all(math.isfinite(v) for v in analysis.values()), analysis


def test_all_keys_present_and_finite_on_sine(target_wav: Path) -> None:
    analysis = analyze_vocal(target_wav)
    assert set(analysis) == EXPECTED_KEYS
    assert all(math.isfinite(v) for v in analysis.values()), analysis


def test_pitch_range_tracks_the_sine_frequency(target_wav: Path) -> None:
    """target.wav is a 440 Hz sine, so the voiced pitch range should sit on it."""
    analysis = analyze_vocal(target_wav)
    assert 400.0 <= analysis["pitch_min_hz"] <= 480.0, analysis
    assert 400.0 <= analysis["pitch_max_hz"] <= 480.0, analysis


def test_tempo_folds_into_a_musical_range() -> None:
    """Beat trackers land an octave out routinely: 99 BPM reported as 199."""
    from producer.analysis import TEMPO_FOLD_RANGE, fold_tempo

    low, high = TEMPO_FOLD_RANGE
    assert fold_tempo(199.0) == pytest.approx(99.5)
    assert fold_tempo(45.0) == pytest.approx(90.0)
    for raw in (30.0, 45.0, 99.0, 140.0, 199.0, 280.0):
        assert low <= fold_tempo(raw) <= high, raw
    # Genres that genuinely live fast are left alone.
    assert fold_tempo(160.0) == pytest.approx(160.0)
    assert fold_tempo(0.0) == 0.0
