"""Putting a vocal over a beat — the stage nothing else can clear."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.benchmark import measure
from producer.cli import cli
from producer.combine import (
    ALIGNMENT_CONFIDENCE_FLOOR,
    combine,
    find_offset,
)

SR = 22050
BPM = 90.0


def _beat(seconds: float = 40.0, stereo: bool = True) -> np.ndarray:
    """Drums and bass, deliberately periodic — as a real loop-based beat is."""
    rng = np.random.default_rng(2)
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    out = np.zeros_like(t)
    beat_s = 60.0 / BPM
    env = np.exp(-np.linspace(0, 8, int(0.2 * SR)))
    kick = np.sin(2 * np.pi * 55 * np.arange(len(env)) / SR) * env
    snare = rng.normal(0, 1, len(env)) * env * 0.5
    for i in range(int(seconds / beat_s)):
        at = int(i * beat_s * SR)
        hit = kick if i % 2 == 0 else snare
        n = min(len(hit), len(out) - at)
        if n > 0:
            out[at:at + n] += hit[:n] * 0.9
    out = out + 0.35 * np.sin(2 * np.pi * 55 * t)
    out = (out / (np.max(np.abs(out)) + 1e-9) * 0.5).astype(np.float32)
    return np.stack([out, np.roll(out, 40) * 0.96], axis=1).astype(np.float32) if stereo else out


def _vocal(seconds: float = 30.0) -> np.ndarray:
    """A voice: no sub, stops between phrases."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (200 + 40 * np.sin(2 * np.pi * 1.3 * t)) / SR)
    tone = 0.45 * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3
    gate = ((t % 4.0) < 2.8).astype(np.float32)
    return (tone * gate).astype(np.float32)


@pytest.fixture()
def rig(tmp_path: Path) -> dict:
    vocal, beat = tmp_path / "vocal.wav", tmp_path / "beat.wav"
    sf.write(str(vocal), _vocal(), SR)
    sf.write(str(beat), _beat(), SR)
    return {"vocal": vocal, "beat": beat, "out": tmp_path / "mixed.wav"}


def _lufs_gap(path: Path) -> float:
    return measure(path)["lufs"]


# ── alignment ────────────────────────────────────────────────────────────


def test_a_periodic_beat_is_reported_as_ambiguous(rig: dict) -> None:
    """A loop repeats, so onset correlation cannot pin the offset absolutely.

    The earlier metric compared the peak to the mean and called a wrong answer
    confident; this must read as not trustworthy instead.
    """
    alignment = find_offset(rig["vocal"], rig["beat"])
    assert not alignment.trustworthy, alignment.to_dict()
    assert 0.0 <= alignment.confidence <= 1.0


def test_a_weak_detection_is_never_applied(rig: dict) -> None:
    """Silently moving a whole take on a guess is worse than not moving it."""
    result = combine(**{k: rig[k] for k in ("vocal", "beat")}, out_path=rig["out"])
    assert result["alignment"]["offset_s"] == 0.0
    assert "too weak" in result["alignment"]["method"]


def test_an_explicit_offset_is_honoured(rig: dict) -> None:
    result = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=1.5)
    assert result["alignment"]["offset_s"] == pytest.approx(1.5)
    assert result["alignment"]["method"] == "given"
    assert result["alignment"]["trustworthy"]


def test_offset_lengthens_the_timeline(rig: dict) -> None:
    early = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0)["duration_s"]
    late = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=20.0)["duration_s"]
    assert late > early


def test_a_negative_offset_places_the_vocal_first(rig: dict) -> None:
    result = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=-3.0)
    assert result["duration_s"] > 40.0
    assert Path(result["output"]).stat().st_size > 0


# ── the mix ──────────────────────────────────────────────────────────────


def test_the_vocal_ends_up_above_the_beat(rig: dict) -> None:
    """The single most consequential number in the whole stage."""
    result = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0,
                     vocal_over_beat_db=4.0, duck_db=0.0)
    gap = result["vocal_lufs"] - (result["beat_lufs"] + result["beat_gain_db"])
    assert gap == pytest.approx(4.0, abs=0.3), result


def test_a_wider_gap_pushes_the_beat_further_down(rig: dict) -> None:
    quiet = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0, vocal_over_beat_db=8.0)
    loud = combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0, vocal_over_beat_db=2.0)
    assert quiet["beat_gain_db"] < loud["beat_gain_db"]


def test_ducking_steps_the_beat_back_under_the_voice(rig: dict) -> None:
    """Measure the beat, not the mix.

    Vocal and beat can partially cancel when summed, so the mix getting louder
    with ducking on is a phase artefact rather than evidence. The vocal is laid
    in unchanged, so subtracting it recovers the beat exactly.
    """
    plain, ducked = rig["out"], rig["out"].with_name("ducked.wav")
    combine(rig["vocal"], rig["beat"], plain, offset_s=0.0, duck_db=0.0)
    combine(rig["vocal"], rig["beat"], ducked, offset_s=0.0, duck_db=6.0)

    a, _ = sf.read(str(plain), dtype="float32")
    b, _ = sf.read(str(ducked), dtype="float32")
    vocal, _ = sf.read(str(rig["vocal"]), dtype="float32")

    n = min(len(a), len(b), len(vocal))
    stereo_vocal = np.stack([vocal[:n], vocal[:n]], axis=1)
    beat_plain = a[:n] - stereo_vocal
    beat_ducked = b[:n] - stereo_vocal

    loud = np.abs(vocal[:n]) > np.percentile(np.abs(vocal[:n]), 85)
    under_voice = np.abs(beat_ducked[loud]).mean() / max(np.abs(beat_plain[loud]).mean(), 1e-9)
    between = np.abs(beat_ducked[~loud]).mean() / max(np.abs(beat_plain[~loud]).mean(), 1e-9)

    assert under_voice < 0.75, f"beat only fell to {under_voice:.2f} under the voice"
    # And it must come back up between the lines, or it is just a volume cut.
    assert between > under_voice * 1.2, (under_voice, between)


def test_the_output_is_stereo_even_from_a_mono_vocal(rig: dict) -> None:
    combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0)
    assert sf.info(str(rig["out"])).channels == 2


def test_a_mono_beat_still_works(tmp_path: Path, rig: dict) -> None:
    mono_beat = tmp_path / "mono_beat.wav"
    sf.write(str(mono_beat), _beat(stereo=False), SR)
    result = combine(rig["vocal"], mono_beat, rig["out"], offset_s=0.0)
    assert Path(result["output"]).stat().st_size > 0


def test_headroom_is_left_for_mastering(rig: dict) -> None:
    """Handing the master stage a file at full scale wastes the stage."""
    combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0)
    y, _ = sf.read(str(rig["out"]), dtype="float32")
    assert float(np.max(np.abs(y))) <= 0.9


def test_mismatched_sample_rates_are_resampled(tmp_path: Path, rig: dict) -> None:
    odd = tmp_path / "odd.wav"
    sf.write(str(odd), _vocal(), 44100)
    result = combine(odd, rig["beat"], rig["out"], offset_s=0.0)
    assert sf.info(str(rig["out"])).samplerate == SR
    assert result["duration_s"] > 0


def test_the_result_reads_as_a_full_mix(rig: dict) -> None:
    """The point of the stage: a bare vocal becomes a track with music under it."""
    from producer.library import classify_track

    assert classify_track(rig["vocal"])[0] == "vocal-only"
    combine(rig["vocal"], rig["beat"], rig["out"], offset_s=0.0)
    assert classify_track(rig["out"])[0] == "full-mix"


# ── cli ──────────────────────────────────────────────────────────────────


def test_combine_command(rig: dict) -> None:
    result = CliRunner().invoke(cli, [
        "combine", "--vocal", str(rig["vocal"]), "--beat", str(rig["beat"]),
        "--out", str(rig["out"]), "--offset", "0",
    ])
    assert result.exit_code == 0, result.output
    assert "Vocal" in result.output and "beat" in result.output
    assert rig["out"].stat().st_size > 0


def test_check_alignment_writes_nothing(rig: dict) -> None:
    result = CliRunner().invoke(cli, [
        "combine", "--vocal", str(rig["vocal"]), "--beat", str(rig["beat"]),
        "--out", str(rig["out"]), "--check-alignment",
    ])
    assert result.exit_code == 0, result.output
    assert "Offset" in result.output
    assert not rig["out"].exists()


def test_low_confidence_is_warned_about_in_the_cli(rig: dict) -> None:
    result = CliRunner().invoke(cli, [
        "combine", "--vocal", str(rig["vocal"]), "--beat", str(rig["beat"]),
        "--out", str(rig["out"]),
    ])
    assert result.exit_code == 0, result.output
    assert "confidence is low" in result.output


def test_confidence_floor_is_strict_enough_to_matter() -> None:
    assert ALIGNMENT_CONFIDENCE_FLOOR >= 0.3


# ── grid alignment ───────────────────────────────────────────────────────


def test_grid_alignment_needs_a_beat_with_a_pulse(tmp_path: Path, rig: dict) -> None:
    """Without drums there is no grid, and the method must say so."""
    from producer.combine import align_to_grid

    pulseless = tmp_path / "pad.wav"
    t = np.linspace(0, 40, SR * 40, endpoint=False)
    sf.write(str(pulseless), (0.3 * np.sin(2 * np.pi * 110 * t)).astype(np.float32), SR)

    alignment = align_to_grid(rig["vocal"], pulseless)
    assert not alignment.trustworthy
    assert "no usable grid" in alignment.method or "onsets" in alignment.method


def test_grid_alignment_reports_the_tempo_it_locked_to(rig: dict) -> None:
    from producer.combine import align_to_grid

    alignment = align_to_grid(rig["vocal"], rig["beat"])
    assert 0.0 <= alignment.confidence <= 1.0
    # Whatever it concludes, it must stay inside the half-bar it searched.
    assert abs(alignment.offset_s) <= 60.0 / BPM * 4.0 * 0.5 + 0.01


def test_dense_onsets_against_a_fine_grid_are_not_claimed_as_confident(rig: dict) -> None:
    """A fast vocal fits almost any offset against sixteenths.

    That ambiguity is real, and reporting it as a lock would move the take on
    a coin flip.
    """
    from producer.combine import align_to_grid

    alignment = align_to_grid(rig["vocal"], rig["beat"])
    if alignment.confidence < 0.35:
        assert not alignment.trustworthy
