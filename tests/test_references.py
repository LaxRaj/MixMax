"""Per-track references: each song can target its own sound."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.benchmark import RESERVED_STEMS, discover_versions
from producer.cli import cli
from producer.intake import run_intake
from producer.workspace import (
    MissingReference,
    find_track_reference,
    plan_references,
    render_workspace,
    resolve_reference,
)

SR = 44100


def _voice(seconds: float = 12.0, amp: float = 0.4) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (220.0 + 4.0 * np.sin(2 * np.pi * 5.0 * t)) / SR)
    return (amp * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3).astype(np.float32)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "ballad.wav"), _voice(), SR)
    sf.write(str(raw / "rap track.wav"), _voice(amp=0.3), SR)
    ws = tmp_path / "ws"
    run_intake(raw, ws)
    return ws


@pytest.fixture()
def fallback_ref(tmp_path: Path) -> Path:
    path = tmp_path / "fallback_ref.wav"
    sf.write(str(path), _voice(seconds=10, amp=0.5), SR)
    return path


def _put_reference(song_dir: Path, name: str = "reference.wav", amp: float = 0.45) -> Path:
    path = song_dir / name
    sf.write(str(path), _voice(seconds=10, amp=amp), SR)
    return path


def test_per_track_reference_is_found(workspace: Path) -> None:
    song = workspace / "ballad"
    assert find_track_reference(song) is None
    ref = _put_reference(song)
    assert find_track_reference(song) == ref


def test_per_track_reference_wins_over_fallback(workspace: Path, fallback_ref: Path) -> None:
    song = workspace / "ballad"
    own = _put_reference(song)
    assert resolve_reference(song, fallback_ref) == own


def test_fallback_used_when_track_has_none(workspace: Path, fallback_ref: Path) -> None:
    assert resolve_reference(workspace / "ballad", fallback_ref) == fallback_ref


def test_missing_reference_raises(workspace: Path) -> None:
    with pytest.raises(MissingReference):
        resolve_reference(workspace / "ballad", None)


def test_non_wav_reference_is_accepted(workspace: Path) -> None:
    song = workspace / "ballad"
    _put_reference(song, name="reference.flac")
    assert find_track_reference(song).suffix == ".flac"


def test_symlinked_reference_works(workspace: Path, fallback_ref: Path) -> None:
    """Several tracks can share one reference without copying it everywhere."""
    link = workspace / "ballad" / "reference.wav"
    link.symlink_to(fallback_ref)
    assert resolve_reference(workspace / "ballad", None) == link


def test_reference_is_not_treated_as_a_version(workspace: Path) -> None:
    """The whole reason the stem is reserved."""
    song = workspace / "ballad"
    _put_reference(song)
    assert "reference" in RESERVED_STEMS
    assert set(discover_versions(song)) == {"original"}


def test_plan_reports_missing_before_rendering(workspace: Path) -> None:
    _put_reference(workspace / "ballad")
    resolved, missing = plan_references(workspace, None)

    assert [d.name for d, _ in resolved] == ["ballad"]
    assert [d.name for d in missing] == ["rap-track"]


def test_render_refuses_rather_than_half_finishing(workspace: Path) -> None:
    """A partial render leaves a workspace that silently lies about itself."""
    _put_reference(workspace / "ballad")
    with pytest.raises(MissingReference):
        render_workspace(workspace, None)

    assert not (workspace / "ballad" / "producer.wav").exists()


def test_render_uses_each_track_s_own_reference(workspace: Path, fallback_ref: Path) -> None:
    own = _put_reference(workspace / "ballad")
    results = {r["slug"]: r for r in render_workspace(workspace, fallback_ref)}

    assert results["ballad"]["reference"] == str(own)
    assert results["ballad"]["reference_source"] == "per-track"
    assert results["rap-track"]["reference"] == str(fallback_ref)
    assert results["rap-track"]["reference_source"] == "fallback"
    assert all(Path(r["output"]).stat().st_size > 0 for r in results.values())


def test_different_references_produce_different_masters(tmp_path: Path) -> None:
    """Per-track references only matter if they actually change the output."""
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "song.wav"), _voice(), SR)

    bright = tmp_path / "bright.wav"
    dark = tmp_path / "dark.wav"
    t = np.linspace(0, 10, SR * 10, endpoint=False)
    sf.write(str(bright), (0.4 * np.sin(2 * np.pi * 220 * t) + 0.25 * np.sin(2 * np.pi * 8000 * t)).astype(np.float32), SR)
    sf.write(str(dark), (0.4 * np.sin(2 * np.pi * 220 * t) + 0.002 * np.sin(2 * np.pi * 8000 * t)).astype(np.float32), SR)

    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    run_intake(raw, ws_a)
    run_intake(raw, ws_b)
    render_workspace(ws_a, bright)
    render_workspace(ws_b, dark)

    a, _ = sf.read(str(ws_a / "song" / "producer.wav"), dtype="float32")
    b, _ = sf.read(str(ws_b / "song" / "producer.wav"), dtype="float32")
    n = min(len(a), len(b))
    assert not np.allclose(a[:n], b[:n], atol=1e-3), "reference choice had no effect"


def test_render_dry_run_shows_the_plan(workspace: Path, fallback_ref: Path) -> None:
    _put_reference(workspace / "ballad")
    result = CliRunner().invoke(
        cli,
        ["render", "--workspace", str(workspace), "--reference", str(fallback_ref), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "per-track" in result.output and "fallback" in result.output
    assert not (workspace / "ballad" / "producer.wav").exists()


def test_render_command_without_any_reference_fails_clearly(workspace: Path) -> None:
    result = CliRunner().invoke(cli, ["render", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert "No reference for" in result.output
    assert "ballad" in result.output and "rap-track" in result.output


def test_render_command_with_per_track_references_only(workspace: Path) -> None:
    _put_reference(workspace / "ballad")
    _put_reference(workspace / "rap-track", amp=0.5)

    result = CliRunner().invoke(cli, ["render", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert result.output.count("per-track") == 2


def test_reference_never_reaches_the_blind_test(workspace: Path, fallback_ref: Path) -> None:
    """If a reference leaked into the listening set, friends would be scoring
    an entirely different song and the whole test would be void."""
    from producer.blindtest import build_blind_test

    song = workspace / "ballad"
    _put_reference(song)
    render_workspace(workspace, fallback_ref)

    result = build_blind_test(discover_versions(song), workspace / "blind", seed=1)
    assert set(result["mapping"].values()) == {"original", "producer"}
    assert len(list((workspace / "blind" / "listen").glob("*.wav"))) == 2
