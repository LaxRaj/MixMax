"""The dashboard aggregate, and its evidence ledger."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.dashboard import FITTED, GUESSED, MEASURED, PUBLISHED, REPORTED, build_dashboard
from producer.intake import run_intake
from producer.mix import ChainParams

SR = 44100


def _voice(seconds: float = 10.0, amp: float = 0.4) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (220.0 + 4.0 * np.sin(2 * np.pi * 5.0 * t)) / SR)
    return (amp * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3).astype(np.float32)


def test_empty_pipeline_still_produces_a_dashboard() -> None:
    d = build_dashboard()
    assert [s["key"] for s in d["stages"]] == ["intake", "render", "benchmark", "listening"]
    assert d["tracks"] == []
    assert d["library"] is None
    assert d["listening"] is None
    assert d["evidence"], "the ledger should describe defaults even with no data"


def test_every_evidence_row_declares_a_provenance() -> None:
    valid = {PUBLISHED, MEASURED, FITTED, REPORTED, GUESSED}
    for row in build_dashboard()["evidence"]:
        assert row["provenance"] in valid, row
        assert row["name"] and row["detail"], row


def test_unchecked_defaults_are_called_guessed() -> None:
    """The point of the page: a hand-picked number must not look authoritative."""
    rows = {r["name"]: r for r in build_dashboard()["evidence"]}
    assert rows["QA loudness window"]["provenance"] == GUESSED
    assert rows["De-ess frequency"]["provenance"] == GUESSED
    assert "never checked" in rows["QA loudness window"]["detail"]


def test_verified_standards_count_as_published() -> None:
    rows = {r["name"]: r for r in build_dashboard()["evidence"]}
    assert rows["Spotify"]["provenance"] == PUBLISHED
    assert "support.spotify.com" in rows["Spotify"]["source"]


def test_unconfirmed_standards_are_reported_not_guessed() -> None:
    """A widely reported platform target is weaker than a spec, stronger than a guess."""
    rows = {r["name"]: r for r in build_dashboard()["evidence"]}
    assert rows["YouTube"]["provenance"] == REPORTED


def test_fitted_params_are_credited_as_fitted(tmp_path: Path) -> None:
    path = ChainParams(highpass_hz=137.0).save(tmp_path / "p.json")
    rows = {r["name"]: r for r in build_dashboard(chain_params_path=path)["evidence"]}

    assert rows["Highpass cutoff"]["provenance"] == FITTED
    assert "137" in rows["Highpass cutoff"]["value"]
    # Knobs the fit left alone are still just defaults.
    assert rows["De-ess frequency"]["provenance"] == GUESSED


def test_grounded_percentage_counts_everything_but_guesses() -> None:
    summary = build_dashboard()["evidence_summary"]
    counts = summary["counts"]
    grounded = sum(counts.get(k, 0) for k in (PUBLISHED, MEASURED, FITTED, REPORTED))
    assert summary["grounded_pct"] == round(100 * grounded / summary["total"])
    assert 0 <= summary["grounded_pct"] <= 100


def test_tracks_report_what_each_platform_does(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "song.wav"), _voice(), SR)
    workspace = tmp_path / "ws"
    run_intake(raw, workspace)

    from producer.workspace import render_workspace

    reference = tmp_path / "ref.wav"
    sf.write(str(reference), _voice(amp=0.5), SR)
    render_workspace(workspace, reference)

    d = build_dashboard(workspace=workspace)
    track = d["tracks"][0]
    assert track["slug"] == "song"
    assert track["has_ours"] is True
    assert track["conformance"], "every rendered master should carry conformance"
    assert all("delivered_lufs" in c for c in track["conformance"])


def test_unrendered_tracks_are_marked_not_crashed(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "song.wav"), _voice(), SR)
    workspace = tmp_path / "ws"
    run_intake(raw, workspace)

    track = build_dashboard(workspace=workspace)["tracks"][0]
    assert track["has_ours"] is False
    assert "measurement" not in track


def test_reference_is_not_listed_as_a_version(tmp_path: Path) -> None:
    """A reference in the folder is the target, not a version of the song."""
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "song.wav"), _voice(), SR)
    workspace = tmp_path / "ws"
    run_intake(raw, workspace)
    sf.write(str(workspace / "song" / "reference.wav"), _voice(amp=0.5), SR)

    track = build_dashboard(workspace=workspace)["tracks"][0]
    assert "reference" not in track["versions"]
    assert track["has_reference"] is True


def test_dashboard_command_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "dashboard.json"
    result = CliRunner().invoke(cli, ["dashboard", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Evidence:" in result.output

    data = json.loads(out.read_text())
    assert data["evidence_summary"]["total"] > 0
