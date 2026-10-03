"""Arrangement editing: a longer song from the parts already there."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.arrange import (
    BEATS_PER_BAR,
    Arrangement,
    BarGrid,
    Segment,
    plan_extension,
    render_arrangement,
)
from producer.cli import cli
from producer.structure import analyze_structure

SR = 22050
BPM = 120.0


def _part(bars: float, bass: float, bright: float, seed: int) -> np.ndarray:
    bar_s = BEATS_PER_BAR * 60.0 / BPM
    seconds = bars * bar_s
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    out = 0.3 * np.sin(2 * np.pi * 220 * t) + bright * 0.25 * np.sin(2 * np.pi * 3000 * t)
    out = out + bass * 0.55 * np.sin(2 * np.pi * 55 * t)
    step = int(SR * 60 / BPM / 2)
    env = np.exp(-np.linspace(0, 9, int(0.07 * SR)))
    hit = np.sin(2 * np.pi * 90 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(hit), step):
        out[start:start + len(hit)] += hit * 0.7
    return ((out / 2.4) + rng.normal(0, 0.002, len(t))).astype(np.float32)


@pytest.fixture()
def song(tmp_path: Path) -> Path:
    path = tmp_path / "song.wav"
    sf.write(str(path), np.concatenate([
        _part(4, 0.1, 0.2, 1),    # intro
        _part(16, 1.0, 0.8, 2),   # hook
        _part(12, 0.9, 0.5, 3),   # verse
        _part(16, 1.0, 0.8, 4),   # hook
        _part(4, 0.2, 0.3, 5),    # outro
    ]), SR)
    return path


def _low_share(samples: np.ndarray, sr: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / sr)
    power = np.square(spectrum, dtype=np.float64)
    return float(10 * np.log10(max(power[freqs < 150].sum() / power.sum(), 1e-20)))


# ── bar grid ─────────────────────────────────────────────────────────────


def test_grid_finds_the_tempo(song: Path) -> None:
    grid = BarGrid.from_audio(song)
    assert abs(grid.tempo_bpm - BPM) < 6.0, grid.tempo_bpm
    assert grid.bar_s == pytest.approx(BEATS_PER_BAR * 60.0 / grid.tempo_bpm)


def test_snapping_lands_on_bar_lines(song: Path) -> None:
    """An edit off the bar line is what makes a rearrangement sound wrong."""
    grid = BarGrid.from_audio(song)
    for t in (3.3, 7.9, 12.1, 20.4):
        snapped = grid.snap(t)
        offset = (snapped - grid.first_beat_s) % grid.bar_s
        assert min(offset, grid.bar_s - offset) < 1e-6, (t, snapped)
        assert abs(snapped - t) <= grid.bar_s / 2 + 1e-6


def test_snapping_stays_inside_the_track(song: Path) -> None:
    grid = BarGrid.from_audio(song)
    assert grid.snap(-10.0) >= 0.0
    assert grid.snap(grid.duration_s + 30) <= grid.duration_s


# ── rendering ────────────────────────────────────────────────────────────


def test_segments_are_concatenated_in_order(tmp_path: Path, song: Path) -> None:
    out = tmp_path / "out.wav"
    arrangement = Arrangement([
        Segment(0.0, 4.0, role="a"),
        Segment(8.0, 12.0, role="b"),
    ])
    info = render_arrangement(song, arrangement, out)
    assert info["segments"] == 2
    assert info["duration_s"] == pytest.approx(8.0, abs=0.2)


def test_rendering_can_make_a_track_longer_than_its_source(tmp_path: Path, song: Path) -> None:
    """Repeating a section is how an edit outruns the material it came from."""
    out = tmp_path / "out.wav"
    source_s = sf.info(str(song)).duration
    passes = int(source_s // 20) + 2
    arrangement = Arrangement([Segment(0.0, 20.0, role=f"pass {i}") for i in range(passes)])
    info = render_arrangement(song, arrangement, out)
    assert info["duration_s"] > info["source_duration_s"]


def test_highpass_removes_the_low_end(tmp_path: Path, song: Path) -> None:
    """The breakdown: same music, bass gone."""
    plain, stripped = tmp_path / "plain.wav", tmp_path / "stripped.wav"
    render_arrangement(song, Arrangement([Segment(8.0, 16.0)]), plain)
    render_arrangement(song, Arrangement([Segment(8.0, 16.0, highpass_hz=230.0)]), stripped)

    a, sr = sf.read(str(plain), dtype="float32")
    b, _ = sf.read(str(stripped), dtype="float32")
    assert _low_share(b, sr) < _low_share(a, sr) - 6.0


def test_a_sweep_changes_the_spectrum_over_time(tmp_path: Path, song: Path) -> None:
    out = tmp_path / "riser.wav"
    render_arrangement(
        song, Arrangement([Segment(8.0, 14.0, highpass_sweep=(200.0, 2500.0))]), out
    )
    y, sr = sf.read(str(out), dtype="float32")
    first, last = y[: len(y) // 4], y[-len(y) // 4:]
    # The sweep climbs, so the end holds far less low end than the start.
    assert _low_share(last, sr) < _low_share(first, sr) - 4.0


def test_output_never_clips(tmp_path: Path, song: Path) -> None:
    out = tmp_path / "out.wav"
    render_arrangement(song, Arrangement([Segment(8.0, 16.0, gain_db=24.0)]), out)
    y, _ = sf.read(str(out), dtype="float32")
    assert float(np.max(np.abs(y))) <= 0.999 + 1e-6


def test_joins_do_not_click(tmp_path: Path, song: Path) -> None:
    """A hard cut between distant parts leaves a step; the crossfade removes it."""
    out = tmp_path / "out.wav"
    render_arrangement(song, Arrangement([Segment(0.0, 4.0), Segment(16.0, 20.0)]), out)
    y, sr = sf.read(str(out), dtype="float32")
    jump = float(np.max(np.abs(np.diff(y))))
    assert jump < 0.35, f"sample-to-sample jump of {jump:.3f} suggests a click"


def test_an_empty_arrangement_is_refused(tmp_path: Path, song: Path) -> None:
    with pytest.raises(ValueError, match="no audio"):
        render_arrangement(song, Arrangement([]), tmp_path / "out.wav")


# ── planning ─────────────────────────────────────────────────────────────


def test_plan_reaches_the_target_length(song: Path) -> None:
    structure = analyze_structure(song)
    grid = BarGrid.from_audio(song)
    arrangement, _ = plan_extension(structure, grid, target_duration_s=120.0)
    assert arrangement.duration_s > structure.duration_s
    assert arrangement.duration_s >= 100.0


def test_plan_includes_a_bridge_and_a_drop(song: Path) -> None:
    structure = analyze_structure(song)
    arrangement, why = plan_extension(structure, BarGrid.from_audio(song))
    roles = " ".join(s.role for s in arrangement.segments)

    assert "bridge" in roles
    assert "drop" in roles
    assert any(s.highpass_hz for s in arrangement.segments), "the bridge must strip the low end"
    assert any(s.highpass_sweep for s in arrangement.segments), "the riser must sweep"
    assert why, "every decision should be explained"


def test_plan_segments_land_on_bar_lines(song: Path) -> None:
    structure = analyze_structure(song)
    grid = BarGrid.from_audio(song)
    arrangement, _ = plan_extension(structure, grid)
    for segment in arrangement.segments[:-1]:   # the outro keeps the true end
        offset = (segment.source_start_s - grid.first_beat_s) % grid.bar_s
        assert min(offset, grid.bar_s - offset) < 1e-6, segment.role


def test_plan_never_reads_past_the_source(song: Path) -> None:
    structure = analyze_structure(song)
    grid = BarGrid.from_audio(song)
    arrangement, _ = plan_extension(structure, grid)
    for segment in arrangement.segments:
        assert 0.0 <= segment.source_start_s < segment.source_end_s <= grid.duration_s + 1e-6


def test_a_rendered_plan_has_an_audible_breakdown(tmp_path: Path, song: Path) -> None:
    """End to end: the analyser should find the drop the planner built."""
    structure = analyze_structure(song)
    out = tmp_path / "extended.wav"
    arrangement, _ = plan_extension(structure, BarGrid.from_audio(song))
    render_arrangement(song, arrangement, out)

    after = analyze_structure(out)
    lows = [s.low_energy_db for s in after.sections]
    assert max(lows) - min(lows) >= 5.0, lows


def test_plan_refuses_a_track_it_cannot_read(tmp_path: Path) -> None:
    path = tmp_path / "tiny.wav"
    sf.write(str(path), _part(1, 1.0, 0.5, 9), SR)
    structure = analyze_structure(path)
    if len(structure.sections) >= 3:
        pytest.skip("fixture segmented further than expected")
    with pytest.raises(ValueError, match="not enough sections"):
        plan_extension(structure, BarGrid.from_audio(path))


def test_arrange_command(tmp_path: Path, song: Path) -> None:
    out = tmp_path / "extended.wav"
    result = CliRunner().invoke(
        cli, ["arrange", "--source", str(song), "--out", str(out), "--minutes", "2.0"]
    )
    assert result.exit_code == 0, result.output
    assert "bridge" in result.output
    assert out.stat().st_size > 0
    # The claim about what this is must travel with the output.
    assert "composes nothing" in result.output or "existing material" in result.output


def test_arrange_dry_run_writes_nothing(tmp_path: Path, song: Path) -> None:
    out = tmp_path / "nope.wav"
    result = CliRunner().invoke(
        cli, ["arrange", "--source", str(song), "--out", str(out), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert not out.exists()


def test_grid_folds_an_octave_error(tmp_path: Path) -> None:
    """At twice the true tempo the bar halves, and cuts land on beat 3.

    The structure analyser read a real track as 199 BPM where the beat under it
    measured 99.4; the grid must not inherit that.
    """
    from producer.analysis import TEMPO_FOLD_RANGE

    path = tmp_path / "fast.wav"
    sf.write(str(path), np.concatenate([_part(8, 1.0, 0.8, 3)] * 3), SR)
    grid = BarGrid.from_audio(path)

    low, high = TEMPO_FOLD_RANGE
    assert low <= grid.tempo_bpm <= high, grid.tempo_bpm
    assert grid.bar_s == pytest.approx(BEATS_PER_BAR * 60.0 / grid.tempo_bpm)
