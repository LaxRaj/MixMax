"""M5 — the before/after comparison plot."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from producer.cli import cli
from producer.report_plot import plot_comparison

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_plot_writes_a_real_png(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    png = plot_comparison(target_wav, reference_wav, tmp_path / "cmp.png")
    assert png.exists()
    assert png.read_bytes()[:8] == PNG_MAGIC


def test_plot_creates_missing_parent_dirs(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    png = plot_comparison(target_wav, reference_wav, tmp_path / "a" / "b" / "cmp.png")
    assert png.exists()


def test_master_plot_flag(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    out = tmp_path / "mastered.wav"
    result = CliRunner().invoke(
        cli,
        [
            "master",
            "--vocal", str(target_wav),
            "--reference", str(reference_wav),
            "--out", str(out),
            "--plot",
        ],
    )
    assert result.exit_code == 0, result.output
    png = out.with_suffix(".comparison.png")
    assert png.exists()
    assert png.read_bytes()[:8] == PNG_MAGIC
