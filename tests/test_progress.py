"""How finished a song is — completion, not conformance."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.intake import run_intake
from producer.progress import build_comparison, track_progress

SR = 22050


def _mix(seconds: float = 30.0) -> np.ndarray:
    """A full track: bass, kick, music."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(4)
    out = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 55 * t)
    env = np.exp(-np.linspace(0, 9, int(0.2 * SR)))
    kick = np.sin(2 * np.pi * 60 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(kick), int(SR * 0.5)):
        out[start:start + len(kick)] += kick * 0.8
    return ((out / 2.3) + rng.normal(0, 0.002, len(t))).astype(np.float32)


def _acappella(seconds: float = 30.0) -> np.ndarray:
    """A lone voice: no sub, and it stops between phrases."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (200 + 30 * np.sin(2 * np.pi * 0.7 * t)) / SR)
    tone = 0.4 * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3
    gate = ((t % 5.0) < 3.2).astype(np.float32)
    return (tone * gate).astype(np.float32)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "full song.wav"), _mix(), SR)
    sf.write(str(raw / "just vocals.wav"), _acappella(), SR)
    ws = tmp_path / "ws"
    run_intake(raw, ws)
    return ws


def test_a_bare_vocal_is_blocked_on_its_backing_track(workspace: Path) -> None:
    """The point of the whole view: a mastered a cappella is not a song."""
    progress = track_progress(workspace / "just-vocals")
    assert progress.kind == "vocal-only"

    backing = next(s for s in progress.stages if s.key == "backing")
    assert backing.state == "blocked"
    assert "no instrumental" in backing.detail
    assert "cannot" in progress.next_step or "has to be" in progress.next_step


def test_a_finished_mix_clears_the_backing_stage(workspace: Path) -> None:
    progress = track_progress(workspace / "full-song")
    assert progress.kind == "full-mix"
    assert next(s for s in progress.stages if s.key == "backing").state == "done"


def test_a_vocal_track_gets_a_vocal_production_stage(workspace: Path) -> None:
    vocal = track_progress(workspace / "just-vocals")
    mix = track_progress(workspace / "full-song")
    assert any(s.key == "vocal" for s in vocal.stages)
    assert not any(s.key == "vocal" for s in mix.stages), "a finished mix needs no vocal chain"


def test_unmastered_tracks_say_so(workspace: Path) -> None:
    progress = track_progress(workspace / "full-song")
    master = next(s for s in progress.stages if s.key == "master")
    assert master.state == "todo"
    assert "deliver" in master.blocker


def test_mastering_moves_the_stage_and_the_percentage(workspace: Path) -> None:
    song = workspace / "full-song"
    before = track_progress(song)

    from producer.loudness import normalize_to_target

    normalize_to_target(song / "original.wav", song / "MASTER.wav", -16.0, -3.0)
    after = track_progress(song)

    assert next(s for s in after.stages if s.key == "master").state == "done"
    assert after.percent > before.percent


def test_nobody_listening_is_reported_as_such(workspace: Path) -> None:
    progress = track_progress(workspace / "full-song")
    judged = next(s for s in progress.stages if s.key == "judged")
    assert judged.state == "todo"
    assert "nobody" in judged.detail


def test_one_listener_is_an_anecdote_not_a_verdict(workspace: Path) -> None:
    song = workspace / "full-song"
    (song / "listening_results.md").write_text(
        "# Listening test results\n\n1 listener(s): lakshya\n\n"
        "| Source | Mean |\n| --- | --- |\n| mastered_16 | 4.00 |\n"
    )
    judged = next(s for s in track_progress(song).stages if s.key == "judged")
    assert judged.state == "partial"
    assert "anecdote" in judged.detail


def test_percentage_is_bounded_and_reflects_blockers(workspace: Path) -> None:
    vocal = track_progress(workspace / "just-vocals")
    assert 0 <= vocal.percent <= 100
    # A blocked stage must not count toward completion.
    assert vocal.percent < 100


def test_measurements_carry_raw_and_final(workspace: Path) -> None:
    progress = track_progress(workspace / "full-song")
    assert set(progress.measurements) == {"raw", "final", "final_file"}
    for key in ("lufs", "true_peak_dbtp", "crest_factor_db", "lra"):
        assert key in progress.measurements["raw"]


def test_arrangement_timeline_covers_the_track(workspace: Path) -> None:
    progress = track_progress(workspace / "full-song")
    timeline = progress.arrangement["timeline"]
    assert timeline
    total = sum(s["duration_s"] for s in timeline)
    assert total == pytest.approx(progress.duration_s, abs=1.0)


def test_comparison_covers_every_track(workspace: Path) -> None:
    data = build_comparison(workspace)
    assert {t["slug"] for t in data["tracks"]} == {"full-song", "just-vocals"}
    assert data["stage_order"][0] == "intake"


def test_compare_command(tmp_path: Path, workspace: Path) -> None:
    out = tmp_path / "compare.json"
    result = CliRunner().invoke(
        cli, ["compare", "--workspace", str(workspace), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "%" in result.output
    assert out.exists()


def test_compare_refuses_an_empty_workspace(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(cli, ["compare", "--workspace", str(empty)])
    assert result.exit_code != 0
    assert "No tracks" in result.output


def test_compare_does_not_shadow_the_benchmark_command(tmp_path: Path) -> None:
    """`compare` is also a function in producer.benchmark.

    Defining a CLI command of the same name rebound it, so `producer benchmark`
    called the Click object and died on a Context TypeError.
    """
    import numpy as np
    import soundfile as sf

    versions = tmp_path / "versions"
    versions.mkdir()
    t = np.linspace(0, 6, SR * 6, endpoint=False)
    sf.write(str(versions / "producer.wav"), (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)
    sf.write(str(versions / "landr.wav"), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)

    result = CliRunner().invoke(cli, ["benchmark", "--versions-dir", str(versions)])
    assert result.exit_code == 0, result.output
    assert "Measurements" in result.output
