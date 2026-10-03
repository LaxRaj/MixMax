"""Fitting the chain to a measured target."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.benchmark import measure
from producer.cli import cli
from producer.mix import ChainParams, DEFAULT_PARAMS, build_vocal_chain
from producer.tune import SPACE, distance, tune_chain

SR = 44100


def _voice(seconds: float = 8.0, amp: float = 0.4, bright: float = 0.08) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (220.0 + 4.0 * np.sin(2 * np.pi * 5.0 * t)) / SR)
    tone = np.sin(phase) + 0.3 * np.sin(2 * phase)
    return (amp * (tone / 1.3 + bright * np.sin(2 * np.pi * 7000 * t))).astype(np.float32)


@pytest.fixture()
def rig(tmp_path: Path) -> dict:
    vocal = tmp_path / "vocal.wav"
    reference = tmp_path / "reference.wav"
    target = tmp_path / "target.wav"
    sf.write(str(vocal), _voice(), SR)
    sf.write(str(reference), _voice(amp=0.5, bright=0.03), SR)
    sf.write(str(target), _voice(amp=0.45, bright=0.01), SR)
    return {"vocal": vocal, "reference": reference, "target": target}


def test_params_roundtrip(tmp_path: Path) -> None:
    params = ChainParams(highpass_hz=120.0, comp_ratio=4.5)
    path = params.save(tmp_path / "p.json")
    assert ChainParams.load(path) == params


def test_defaults_unchanged_by_parameterization() -> None:
    """The refactor must not silently alter the shipped chain."""
    assert [type(p).__name__ for p in build_vocal_chain()] == [
        "HighpassFilter", "NoiseGate", "Compressor", "PeakFilter", "Reverb",
    ]
    assert DEFAULT_PARAMS.highpass_hz == 80.0
    assert DEFAULT_PARAMS.comp_threshold_db == -18.0
    assert DEFAULT_PARAMS.deess_hz == 7000.0


def test_params_change_the_chain() -> None:
    board = build_vocal_chain(ChainParams(highpass_hz=150.0, comp_ratio=5.0))
    assert board[0].cutoff_frequency_hz == pytest.approx(150.0)
    assert board[2].ratio == pytest.approx(5.0)


def test_distance_is_zero_against_itself(rig: dict) -> None:
    m = measure(rig["target"])
    assert distance(m, m) == pytest.approx(0.0, abs=1e-9)


def test_distance_grows_with_difference(rig: dict, tmp_path: Path) -> None:
    near, far = tmp_path / "near.wav", tmp_path / "far.wav"
    sf.write(str(near), _voice(amp=0.45, bright=0.015), SR)
    sf.write(str(far), _voice(amp=0.45, bright=0.30), SR)

    target = measure(rig["target"])
    assert distance(measure(near), target) < distance(measure(far), target)


def test_distance_ignores_loudness(tmp_path: Path) -> None:
    """Matchering sets loudness from the reference, so it must not be scored."""
    quiet, loud = tmp_path / "q.wav", tmp_path / "l.wav"
    base = _voice()
    sf.write(str(quiet), base * 0.2, SR)
    sf.write(str(loud), base * 0.8, SR)

    a, b = measure(quiet), measure(loud)
    assert a["lufs"] != b["lufs"]
    assert distance(a, b) == pytest.approx(0.0, abs=0.35)


def test_search_space_brackets_the_defaults() -> None:
    """A default outside its own range would make the search discard it."""
    for name, (lo, hi) in SPACE.items():
        assert lo <= getattr(DEFAULT_PARAMS, name) <= hi, name


def test_tune_never_returns_worse_than_baseline(rig: dict) -> None:
    result = tune_chain(**rig, budget=6, seed=1)
    assert result.loss <= result.baseline_loss
    assert result.evaluations == 7  # baseline + budget
    assert result.improvement >= 0.0


def test_tune_is_reproducible(rig: dict) -> None:
    a = tune_chain(**rig, budget=5, seed=3)
    b = tune_chain(**rig, budget=5, seed=3)
    assert a.params == b.params
    assert a.loss == pytest.approx(b.loss)


def test_tuned_params_stay_in_range(rig: dict) -> None:
    result = tune_chain(**rig, budget=8, seed=2)
    for name, (lo, hi) in SPACE.items():
        assert lo <= getattr(result.params, name) <= hi, name


def test_tune_command_writes_params(tmp_path: Path, rig: dict) -> None:
    out = tmp_path / "chain.json"
    result = CliRunner().invoke(
        cli,
        [
            "tune",
            "--vocal", str(rig["vocal"]),
            "--reference", str(rig["reference"]),
            "--target", str(rig["target"]),
            "--out", str(out),
            "--budget", "4",
        ],
    )
    assert result.exit_code == 0, result.output
    assert ChainParams.load(out)
    assert "Distance" in result.output
    # The caveat must always ship with the number.
    assert "blind test" in result.output


def test_mix_accepts_tuned_params(tmp_path: Path, rig: dict) -> None:
    params = ChainParams(highpass_hz=140.0)
    path = params.save(tmp_path / "p.json")
    out = tmp_path / "mixed.wav"

    result = CliRunner().invoke(
        cli, ["mix", "--vocal", str(rig["vocal"]), "--out", str(out), "--params", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 0
