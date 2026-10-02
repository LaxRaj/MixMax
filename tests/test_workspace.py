"""M7 — rendering our version into the workspace."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.intake import run_intake
from producer.workspace import render_workspace, song_dirs

SR = 44100


def _voice(seconds: float = 12.0, amp: float = 0.4) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (220.0 + 4.0 * np.sin(2 * np.pi * 5.0 * t)) / SR)
    return (amp * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3).astype(np.float32)


def _workspace(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "song one.wav"), _voice(), SR)
    sf.write(str(raw / "song two.wav"), _voice(amp=0.3), SR)
    workspace = tmp_path / "ws"
    run_intake(raw, workspace)
    return workspace


def test_song_dirs_finds_scaffolded_tracks(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert [d.name for d in song_dirs(workspace)] == ["song-one", "song-two"]


def test_render_writes_our_version_for_every_song(tmp_path: Path, reference_wav: Path) -> None:
    workspace = _workspace(tmp_path)
    results = render_workspace(workspace, reference_wav)

    assert len(results) == 2
    for result in results:
        out = Path(result["output"])
        assert out.name == "producer.wav"
        assert out.stat().st_size > 0
        assert "pass" in result["qa"]


def test_rendered_workspace_is_ready_to_benchmark(tmp_path: Path, reference_wav: Path) -> None:
    """After render, each folder holds the two versions benchmark needs."""
    from producer.benchmark import discover_versions

    workspace = _workspace(tmp_path)
    render_workspace(workspace, reference_wav)

    versions = discover_versions(workspace / "song-one")
    assert set(versions) == {"original", "producer"}


def test_render_command(tmp_path: Path, reference_wav: Path) -> None:
    workspace = _workspace(tmp_path)
    result = CliRunner().invoke(
        cli, ["render", "--workspace", str(workspace), "--reference", str(reference_wav)]
    )
    assert result.exit_code == 0, result.output
    assert "song-one" in result.output


def test_render_fails_on_an_unscaffolded_workspace(tmp_path: Path, reference_wav: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(
        cli, ["render", "--workspace", str(empty), "--reference", str(reference_wav)]
    )
    assert result.exit_code != 0
    assert "intake" in result.output
