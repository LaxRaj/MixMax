"""Arrangement analysis, and delivering into platform normalisation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.standards import STANDARDS, delivery_ceiling, evaluate
from producer.structure import (
    LOW_END_DROP_DB,
    analyze_structure,
    build_structure_report,
)

SR = 22050  # analysis only; halves the time these tests take


def _section(seconds: float, bass: float, bright: float, density: int, seed: int) -> np.ndarray:
    """A stretch of 'music' with controllable low end, brightness and busyness."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    out = 0.3 * np.sin(2 * np.pi * 220 * t) + bright * 0.25 * np.sin(2 * np.pi * 3000 * t)
    out = out + bass * 0.5 * np.sin(2 * np.pi * 55 * t)
    step = max(1, int(SR * 60 / (density * 60)))
    env = np.exp(-np.linspace(0, 9, int(0.08 * SR)))
    hit = np.sin(2 * np.pi * 80 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(hit), step):
        out[start:start + len(hit)] += hit * (0.6 + 0.2 * bass)
    return ((out / 2.4) + rng.normal(0, 0.002, len(t))).astype(np.float32)


@pytest.fixture()
def arranged(tmp_path: Path) -> Path:
    """A track with a quiet intro, a loud body, a stripped break, and an outro."""
    parts = [
        _section(8, bass=0.1, bright=0.2, density=2, seed=1),   # intro
        _section(20, bass=1.0, bright=0.8, density=6, seed=2),  # body
        _section(12, bass=0.05, bright=0.9, density=6, seed=3),  # break: no bass
        _section(20, bass=1.0, bright=0.8, density=6, seed=4),  # body again
        _section(8, bass=0.2, bright=0.3, density=2, seed=5),   # outro
    ]
    path = tmp_path / "arranged.wav"
    sf.write(str(path), np.concatenate(parts), SR)
    return path


@pytest.fixture()
def flat_loop(tmp_path: Path) -> Path:
    """Ninety seconds of exactly one idea."""
    path = tmp_path / "loop.wav"
    sf.write(str(path), np.tile(_section(10, 1.0, 0.8, 6, seed=7), 9), SR)
    return path


def test_sections_are_found_and_ordered(arranged: Path) -> None:
    s = analyze_structure(arranged)
    assert len(s.sections) >= 3
    assert [x.index for x in s.sections] == list(range(len(s.sections)))
    for before, after in zip(s.sections[:-1], s.sections[1:]):
        assert after.start_s == pytest.approx(before.end_s, abs=0.01)
    assert s.sections[0].start_s == 0.0
    assert s.sections[-1].end_s == pytest.approx(s.duration_s, abs=0.5)


def test_sections_cover_the_whole_track(arranged: Path) -> None:
    s = analyze_structure(arranged)
    assert sum(x.duration_s for x in s.sections) == pytest.approx(s.duration_s, abs=0.5)


def test_repetition_counts_every_section(arranged: Path) -> None:
    s = analyze_structure(arranged)
    assert sum(s.repetition.values()) == len(s.sections)
    assert s.sections[0].label == "A", "labels should start at the intro"


def test_a_quiet_intro_produces_a_lift(arranged: Path) -> None:
    s = analyze_structure(arranged)
    assert any(t["kind"] == "lift" for t in s.transitions), s.transitions


def test_a_stripped_break_is_visible_in_the_low_end(arranged: Path) -> None:
    """The break has no bass; some section must measure much lower."""
    s = analyze_structure(arranged)
    lows = [x.low_energy_db for x in s.sections]
    assert max(lows) - min(lows) >= LOW_END_DROP_DB, lows


def test_a_flat_loop_is_called_out(flat_loop: Path) -> None:
    s = analyze_structure(flat_loop)
    joined = " ".join(s.notes).lower()
    assert "one long idea" in joined or "loop" in joined, s.notes


def test_a_track_with_no_low_end_movement_is_told_where_to_drop(flat_loop: Path) -> None:
    s = analyze_structure(flat_loop)
    joined = " ".join(s.notes).lower()
    assert "low end" in joined and "drop" in joined, s.notes


def test_short_tracks_are_flagged(flat_loop: Path) -> None:
    s = analyze_structure(flat_loop)
    assert any("short of a typical single" in n for n in s.notes), s.notes


def test_report_includes_search_parameters(arranged: Path) -> None:
    """Tempo and key are what a beat marketplace actually filters on."""
    report = build_structure_report(analyze_structure(arranged))
    assert "BPM" in report and "Finding compatible material" in report
    # It must not imply it searched a catalogue it cannot reach.
    assert "queries a streaming catalogue" in report


def test_structure_command(tmp_path: Path, arranged: Path) -> None:
    out, raw = tmp_path / "a.md", tmp_path / "a.json"
    result = CliRunner().invoke(
        cli, ["structure", "--source", str(arranged), "--out", str(out), "--json", str(raw)]
    )
    assert result.exit_code == 0, result.output
    assert "repetition:" in result.output
    assert out.exists() and raw.exists()


# ── delivering into normalisation ────────────────────────────────────────


def test_headroom_is_left_for_the_platform_to_lift() -> None:
    """A master sitting on the target with no headroom cannot be raised."""
    assert delivery_ceiling(-16.0, STANDARDS["spotify"]) == pytest.approx(-3.0)
    assert delivery_ceiling(-18.0, STANDARDS["spotify"]) == pytest.approx(-5.0)


def test_a_master_at_target_keeps_the_normal_ceiling() -> None:
    assert delivery_ceiling(-14.0, STANDARDS["spotify"]) == pytest.approx(-1.0)


def test_a_loud_master_gets_the_stricter_ceiling() -> None:
    assert delivery_ceiling(-10.0, STANDARDS["spotify"]) == pytest.approx(-2.0)


def test_platforms_that_never_lift_keep_their_ceiling() -> None:
    """Apple Sound Check only turns down, so headroom buys nothing there."""
    assert delivery_ceiling(-20.0, STANDARDS["apple_music"]) == pytest.approx(-1.0)


def test_the_headroom_trick_actually_lands_on_target() -> None:
    """The whole point: -16 dynamics arriving at -14 playback loudness."""
    ceiling = delivery_ceiling(-16.0, STANDARDS["spotify"])
    measurement = {
        "lufs": -16.0, "true_peak_dbtp": ceiling,
        "crest_factor_db": 14.0, "band_balance_db": {},
    }
    result = evaluate(measurement, STANDARDS["spotify"])
    assert result.delivered_lufs == pytest.approx(-14.0, abs=0.1)
    assert result.true_peak_after_norm_dbtp == pytest.approx(-1.0, abs=0.1)
    assert result.conforms


def test_deliver_command(tmp_path: Path) -> None:
    src = tmp_path / "mix.wav"
    sf.write(str(src), np.concatenate([_section(12, 1.0, 0.8, 6, seed=2)] * 2), SR)

    out = tmp_path / "master.wav"
    result = CliRunner().invoke(
        cli, ["deliver", "--source", str(src), "--out", str(out), "--lufs", "-16"]
    )
    assert result.exit_code == 0, result.output
    assert "Spotify lifts it" in result.output
    assert out.stat().st_size > 0
