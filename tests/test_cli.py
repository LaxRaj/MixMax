"""M0 — the walking skeleton: `producer master` runs end-to-end."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from producer.cli import cli


def test_master_writes_a_non_empty_wav(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    out = tmp_path / "mastered.wav"
    result = CliRunner().invoke(
        cli,
        [
            "master",
            "--vocal", str(target_wav),
            "--reference", str(reference_wav),
            "--out", str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.stat().st_size > 0
    assert str(out) in result.output


def test_master_requires_an_existing_vocal(tmp_path: Path, reference_wav: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "master",
            "--vocal", str(tmp_path / "nope.wav"),
            "--reference", str(reference_wav),
            "--out", str(tmp_path / "out.wav"),
        ],
    )

    assert result.exit_code != 0
