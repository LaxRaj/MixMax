"""M6 — blind, loudness-matched listening tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from click.testing import CliRunner

from producer.benchmark import discover_versions
from producer.blindtest import build_blind_test, build_tally_report, tally
from producer.cli import cli


def _lufs(path: Path) -> float:
    samples, sr = sf.read(str(path), dtype="float32")
    return float(pyln.Meter(sr).integrated_loudness(samples))


def test_every_version_is_loudness_matched(tmp_path: Path, versions_dir: Path) -> None:
    """The whole point: no listener should be able to pick one by volume."""
    result = build_blind_test(discover_versions(versions_dir), tmp_path / "blind", seed=1)

    rendered = sorted((tmp_path / "blind" / "listen").glob("*.wav"))
    assert len(rendered) == 3
    measured = [_lufs(p) for p in rendered]
    assert max(measured) - min(measured) < 0.5, measured


def test_matching_never_boosts(tmp_path: Path, versions_dir: Path) -> None:
    """Attenuating is transparent; boosting risks clipping. Only attenuate."""
    result = build_blind_test(discover_versions(versions_dir), tmp_path / "blind", seed=1)
    assert all(v["applied_gain_db"] <= 0.01 for v in result["versions"]), result["versions"]


def test_no_rendered_version_clips(tmp_path: Path, versions_dir: Path) -> None:
    build_blind_test(discover_versions(versions_dir), tmp_path / "blind", seed=1)
    for path in (tmp_path / "blind" / "listen").glob("*.wav"):
        samples, _ = sf.read(str(path), dtype="float32")
        assert np.max(np.abs(samples)) < 1.0, path


def test_key_is_written_outside_the_shared_folder(tmp_path: Path, versions_dir: Path) -> None:
    """If the key ships with the audio, the test isn't blind."""
    out_dir = tmp_path / "blind"
    result = build_blind_test(discover_versions(versions_dir), out_dir, seed=1)

    key_path = Path(result["key_path"])
    assert key_path.exists()
    assert out_dir not in key_path.parents
    assert not any(p.suffix == ".json" for p in out_dir.rglob("*"))


def test_listening_folder_leaks_no_source_names(tmp_path: Path, versions_dir: Path) -> None:
    out_dir = tmp_path / "blind"
    build_blind_test(discover_versions(versions_dir), out_dir, seed=1)

    names = [p.name for p in (out_dir / "listen").iterdir()]
    assert sorted(names) == ["A.wav", "B.wav", "C.wav"]

    shared_text = "\n".join(
        p.read_text() for p in out_dir.rglob("*") if p.suffix in {".md", ".csv"}
    )
    for source in ("producer", "rival", "original"):
        assert source not in shared_text, f"'{source}' leaked into the shared folder"


def test_labels_are_shuffled_by_seed(tmp_path: Path, versions_dir: Path) -> None:
    versions = discover_versions(versions_dir)
    a = build_blind_test(versions, tmp_path / "a", seed=1)["mapping"]
    b = build_blind_test(versions, tmp_path / "b", seed=1)["mapping"]
    assert a == b, "same seed should reproduce the same assignment"


def test_scoresheet_has_a_row_per_version(tmp_path: Path, versions_dir: Path) -> None:
    build_blind_test(discover_versions(versions_dir), tmp_path / "blind", seed=1)
    with (tmp_path / "blind" / "SCORESHEET.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert [r["label"] for r in rows] == ["A", "B", "C"]


def test_blind_test_needs_two_versions(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli, ["blindtest", "--versions-dir", str(tmp_path), "--out-dir", str(tmp_path / "o")]
    )
    assert result.exit_code != 0
    assert "at least two" in result.output


def test_tally_unblinds_and_aggregates(tmp_path: Path, versions_dir: Path) -> None:
    out_dir = tmp_path / "blind"
    result = build_blind_test(discover_versions(versions_dir), out_dir, seed=1)
    mapping = result["mapping"]
    winner_label = next(l for l, s in mapping.items() if s == "rival")

    # Two listeners both rank `rival` first.
    responses = tmp_path / "responses"
    responses.mkdir()
    for listener in ("ana", "bo"):
        with (responses / f"{listener}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["listener", "label", "release_ready_1_5", "rank", "notes"])
            for label in mapping:
                is_winner = label == winner_label
                w.writerow([listener, label, 5 if is_winner else 2,
                            1 if is_winner else 2, "warm" if is_winner else "harsh"])

    tallied = tally(result["key_path"], responses)
    assert tallied["listeners"] == ["ana", "bo"]
    assert tallied["by_source"]["rival"]["wins"] == 2
    assert tallied["by_source"]["rival"]["mean_score"] == 5.0
    assert tallied["by_source"]["producer"]["wins"] == 0

    report = build_tally_report(tallied)
    assert "rival" in report and "Caution" in report  # <3 listeners


def test_tally_command_rejects_empty_responses(tmp_path: Path, versions_dir: Path) -> None:
    result = build_blind_test(discover_versions(versions_dir), tmp_path / "blind", seed=1)
    empty = tmp_path / "empty.csv"
    empty.write_text("listener,label,release_ready_1_5,rank,notes\n")

    invoked = CliRunner().invoke(
        cli, ["tally", "--key", str(result["key_path"]), "--responses", str(empty)]
    )
    assert invoked.exit_code != 0
    assert "No usable rows" in invoked.output


def test_blindtest_command_end_to_end(tmp_path: Path, versions_dir: Path) -> None:
    invoked = CliRunner().invoke(
        cli,
        ["blindtest", "--versions-dir", str(versions_dir),
         "--out-dir", str(tmp_path / "blind"), "--seed", "7"],
    )
    assert invoked.exit_code == 0, invoked.output
    assert "Keep the key private" in invoked.output
    assert (tmp_path / "blind" / "INSTRUCTIONS.md").exists()
