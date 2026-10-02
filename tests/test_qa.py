"""M3 — the QA gate: clipped audio fails, clean audio passes."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from producer.cli import cli
from producer.qa import run_qa

EXPECTED_KEYS = {"lufs", "clipping_detected", "mono_compatible", "pass", "flags"}


def test_clipped_fixture_fails(clipped_wav: Path) -> None:
    report = run_qa(clipped_wav)
    assert report["clipping_detected"] is True, report
    assert report["pass"] is False, report
    assert any("clipping" in f for f in report["flags"]), report


def test_clean_fixture_passes(clean_wav: Path) -> None:
    report = run_qa(clean_wav)
    assert report["clipping_detected"] is False, report
    assert report["pass"] is True, report
    assert report["flags"] == [], report
    assert -16.0 <= report["lufs"] <= -9.0, report


def test_report_shape(clean_wav: Path) -> None:
    assert set(run_qa(clean_wav)) == EXPECTED_KEYS


def test_out_of_phase_stereo_flagged(out_of_phase_wav: Path) -> None:
    report = run_qa(out_of_phase_wav)
    assert report["mono_compatible"] is False, report
    assert report["pass"] is False, report
    assert any("mono" in f for f in report["flags"]), report


def test_qa_command_emits_json(clean_wav: Path) -> None:
    result = CliRunner().invoke(cli, ["qa", "--file", str(clean_wav)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["pass"] is True


def test_master_prints_qa_automatically(tmp_path: Path, target_wav: Path, reference_wav: Path) -> None:
    out = tmp_path / "mastered.wav"
    result = CliRunner().invoke(
        cli,
        ["master", "--vocal", str(target_wav), "--reference", str(reference_wav), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "QA" in result.output
    assert "LUFS" in result.output
