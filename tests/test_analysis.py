"""M1 — vocal analysis returns finite, structurally complete numbers."""

from __future__ import annotations

import math
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
