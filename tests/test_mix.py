"""M2 — the vocal chain processes audio without destroying or truncating it."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.mix import build_vocal_chain, mix_vocal

RMS_EPSILON = 1e-4


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def test_chain_has_every_stage() -> None:
    board = build_vocal_chain()
    stages = [type(p).__name__ for p in board]
    assert stages == [
        "HighpassFilter",
        "NoiseGate",
        "Compressor",
        "PeakFilter",
        "Reverb",
    ], stages


def test_mix_preserves_duration_and_is_not_silent(tmp_path: Path, target_wav: Path) -> None:
    out = tmp_path / "mixed.wav"
    mix_vocal(target_wav, out)

    assert out.exists()
    original, sr_in = sf.read(str(target_wav), dtype="float32")
    mixed, sr_out = sf.read(str(out), dtype="float32")

    assert sr_out == sr_in
    # Tolerance of one second of samples, for any filter/latency padding.
    assert abs(len(mixed) - len(original)) <= sr_in
    assert _rms(mixed) > RMS_EPSILON, "chain output is silent"


def test_mix_actually_changes_the_signal(tmp_path: Path, target_wav: Path) -> None:
    out = tmp_path / "mixed.wav"
    mix_vocal(target_wav, out)

    original, _ = sf.read(str(target_wav), dtype="float32")
    mixed, _ = sf.read(str(out), dtype="float32")
    n = min(len(original), len(mixed))
    assert not np.allclose(original[:n], mixed[:n], atol=1e-5), "chain was a no-op"


def test_mix_command(tmp_path: Path, target_wav: Path) -> None:
    out = tmp_path / "cli_mixed.wav"
    result = CliRunner().invoke(
        cli, ["mix", "--vocal", str(target_wav), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 0


def test_master_premix_runs_end_to_end(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    out = tmp_path / "premixed_master.wav"
    result = CliRunner().invoke(
        cli,
        [
            "master",
            "--vocal", str(target_wav),
            "--reference", str(reference_wav),
            "--out", str(out),
            "--premix",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Pre-mixed" in result.output
    assert out.stat().st_size > 0
