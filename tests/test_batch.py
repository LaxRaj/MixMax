"""M4 — the batch harness and its markdown report."""

from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner

from producer.batch import run_batch
from producer.cli import cli


def _table_rows(report: str) -> list[str]:
    """Data rows of the first markdown table (skip header and separator)."""
    lines = [l for l in report.splitlines() if l.startswith("| ")]
    return [l for l in lines[2:] if "PASS" in l or "FAIL" in l]


def test_batch_processes_every_file(tmp_path: Path, batch_dir: Path, reference_wav: Path) -> None:
    results = run_batch(batch_dir, reference_wav, tmp_path)
    inputs = sorted(p.name for p in batch_dir.iterdir() if p.suffix == ".wav")

    assert [r["filename"] for r in results] == inputs
    for name in inputs:
        assert (tmp_path / f"{Path(name).stem}_mastered.wav").stat().st_size > 0


def test_report_has_one_row_per_input(tmp_path: Path, batch_dir: Path, reference_wav: Path) -> None:
    results = run_batch(batch_dir, reference_wav, tmp_path)
    report = (tmp_path / "report.md").read_text()

    assert len(_table_rows(report)) == len(results)
    for r in results:
        assert r["filename"] in report


def test_summary_count_matches_actual_results(tmp_path: Path, batch_dir: Path, reference_wav: Path) -> None:
    results = run_batch(batch_dir, reference_wav, tmp_path)
    report = (tmp_path / "report.md").read_text()

    expected = sum(1 for r in results if r["qa"]["pass"])
    match = re.search(r"\*\*(\d+)/(\d+) passed automated QA\*\*", report)
    assert match, report
    assert (int(match.group(1)), int(match.group(2))) == (expected, len(results))


def test_every_result_carries_analysis_and_qa(tmp_path: Path, batch_dir: Path, reference_wav: Path) -> None:
    for r in run_batch(batch_dir, reference_wav, tmp_path):
        assert set(r) == {"filename", "analysis", "qa"}
        assert r["analysis"], f"{r['filename']} produced no analysis"
        assert "pass" in r["qa"]


def test_batch_command(tmp_path: Path, batch_dir: Path, reference_wav: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "batch",
            "--input-dir", str(batch_dir),
            "--reference", str(reference_wav),
            "--out-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "passed automated QA" in result.output
    assert (tmp_path / "report.md").exists()


def test_empty_input_dir_is_not_an_error(tmp_path: Path, reference_wav: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(
        cli,
        [
            "batch",
            "--input-dir", str(empty),
            "--reference", str(reference_wav),
            "--out-dir", str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No .wav/.mp3 files found" in result.output
