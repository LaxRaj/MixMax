"""M7 — the intake gate: catch bad recordings before anyone spends money."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.intake import (
    BLOCKED,
    NOISY_FLOOR_DBFS,
    CAUTION,
    READY,
    discover_raw,
    inspect,
    judge,
    run_intake,
    slugify,
    standardize,
)
from producer.audio import Track

SR = 44100


def _voice(seconds: float = 12.0, amp: float = 0.4, sr: int = SR) -> np.ndarray:
    """A crude stand-in for a vocal: a warbling tone with a few harmonics."""
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    vibrato = 220.0 + 4.0 * np.sin(2 * np.pi * 5.0 * t)
    phase = np.cumsum(2 * np.pi * vibrato / sr)
    tone = np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t)
    return (amp * tone / 1.45 * envelope).astype(np.float32)


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "raw"
    d.mkdir()
    sf.write(str(d / "good take.wav"), _voice(), SR)
    return d


def test_clean_take_is_ready(raw_dir: Path) -> None:
    metrics = inspect(Track.load(raw_dir / "good take.wav"))
    verdict, blockers, _ = judge(metrics)
    assert blockers == []
    assert verdict in (READY, CAUTION)


def test_clipped_take_is_blocked(tmp_path: Path) -> None:
    clipped = np.clip(_voice(amp=0.9) * 3.0, -1.0, 1.0)
    path = tmp_path / "clipped.wav"
    sf.write(str(path), clipped, SR)

    verdict, blockers, _ = judge(inspect(Track.load(path)))
    assert verdict == BLOCKED
    assert any("clipped" in b for b in blockers), blockers


def test_silent_take_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "silent.wav"
    sf.write(str(path), np.zeros(SR * 5, dtype=np.float32), SR)

    verdict, blockers, _ = judge(inspect(Track.load(path)))
    assert verdict == BLOCKED
    assert any("silent" in b for b in blockers), blockers


def test_too_short_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "short.wav"
    sf.write(str(path), _voice(seconds=1.0), SR)

    verdict, blockers, _ = judge(inspect(Track.load(path)))
    assert verdict == BLOCKED
    assert any("too short" in b.lower() for b in blockers), blockers


def test_quiet_and_noisy_take_warns_without_blocking(tmp_path: Path) -> None:
    noisy = _voice(amp=0.05) + np.random.default_rng(0).normal(0, 0.004, SR * 12).astype(np.float32)
    path = tmp_path / "noisy.wav"
    sf.write(str(path), noisy.astype(np.float32), SR)

    verdict, blockers, warnings = judge(inspect(Track.load(path)))
    assert blockers == []
    assert verdict == CAUTION
    assert any("Quiet" in w or "noise floor" in w.lower() for w in warnings), warnings


def test_long_edge_silence_is_flagged(tmp_path: Path) -> None:
    pad = np.zeros(SR * 4, dtype=np.float32)
    path = tmp_path / "padded.wav"
    sf.write(str(path), np.concatenate([pad, _voice(), pad]), SR)

    _, _, warnings = judge(inspect(Track.load(path)))
    assert any("silence at the start" in w for w in warnings), warnings


def test_dual_mono_detected(tmp_path: Path) -> None:
    mono = _voice()
    path = tmp_path / "dual.wav"
    sf.write(str(path), np.stack([mono, mono], axis=1), SR)

    metrics = inspect(Track.load(path))
    assert metrics["dual_mono"] is True
    assert metrics["channels"] == 2


def test_low_sample_rate_is_resampled_on_standardize(tmp_path: Path) -> None:
    low = tmp_path / "low.wav"
    sf.write(str(low), _voice(sr=22050), 22050)

    out = standardize(Track.load(low), tmp_path / "out" / "original.wav")
    info = sf.info(str(out))
    assert info.samplerate == 44100
    assert info.subtype == "PCM_24"


def test_standardize_preserves_a_good_rate(tmp_path: Path, raw_dir: Path) -> None:
    out = standardize(Track.load(raw_dir / "good take.wav"), tmp_path / "o" / "original.wav")
    assert sf.info(str(out)).samplerate == SR


def test_slugify() -> None:
    assert slugify("Good Take #2 (final).wav") == "good-take-2-final-wav"
    assert slugify("!!!") == "track"


def test_discover_raw_is_recursive_and_filtered(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    sf.write(str(tmp_path / "a.wav"), _voice(seconds=3), SR)
    sf.write(str(tmp_path / "sub" / "b.wav"), _voice(seconds=3), SR)
    (tmp_path / "notes.txt").write_text("not audio")

    found = discover_raw(tmp_path)
    assert [p.name for p in found] == ["a.wav", "b.wav"]


def test_run_intake_scaffolds_a_workspace(tmp_path: Path, raw_dir: Path) -> None:
    workspace = tmp_path / "ws"
    results = run_intake(raw_dir, workspace)

    assert len(results) == 1
    assert (workspace / "good-take" / "original.wav").exists()
    assert (workspace / "INTAKE_REPORT.md").exists()
    assert (workspace / "MANIFEST.md").exists()
    assert json.loads((workspace / "intake.json").read_text())[0]["slug"] == "good-take"
    assert not (workspace / ".scratch").exists(), "scratch dir should be cleaned up"


def test_blocked_files_get_no_workspace_slot(tmp_path: Path, raw_dir: Path) -> None:
    sf.write(str(raw_dir / "ruined.wav"), np.clip(_voice(amp=0.9) * 3.0, -1.0, 1.0), SR)
    workspace = tmp_path / "ws"
    run_intake(raw_dir, workspace)

    assert (workspace / "good-take" / "original.wav").exists()
    assert not (workspace / "ruined").exists(), "blocked input must not be staged"


def test_duplicate_names_do_not_collide(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "ana").mkdir(parents=True)
    (raw / "bo").mkdir()
    sf.write(str(raw / "ana" / "take.wav"), _voice(), SR)
    sf.write(str(raw / "bo" / "take.wav"), _voice(), SR)

    results = run_intake(raw, tmp_path / "ws")
    slugs = [r["slug"] for r in results]
    assert len(set(slugs)) == 2, slugs


def test_manifest_lists_each_service(tmp_path: Path, raw_dir: Path) -> None:
    workspace = tmp_path / "ws"
    run_intake(raw_dir, workspace, services=("landr", "emastered"))
    manifest = (workspace / "MANIFEST.md").read_text()

    assert "landr.wav" in manifest
    assert "emastered.wav" in manifest
    assert "original.wav" in manifest


def test_intake_command(tmp_path: Path, raw_dir: Path) -> None:
    result = CliRunner().invoke(
        cli, ["intake", "--input", str(raw_dir), "--workspace", str(tmp_path / "ws")]
    )
    assert result.exit_code == 0, result.output
    assert "usable" in result.output


def test_intake_command_fails_on_empty_input(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(
        cli, ["intake", "--input", str(empty), "--workspace", str(tmp_path / "ws")]
    )
    assert result.exit_code != 0
    assert "No audio files" in result.output


def test_intake_command_fails_when_everything_is_blocked(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    sf.write(str(raw / "ruined.wav"), np.clip(_voice(amp=0.9) * 3.0, -1.0, 1.0), SR)

    result = CliRunner().invoke(
        cli, ["intake", "--input", str(raw), "--workspace", str(tmp_path / "ws")]
    )
    assert result.exit_code != 0
    assert "Nothing passed intake" in result.output


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="afconvert is macOS-only")
def test_m4a_voice_memo_is_transcoded(tmp_path: Path) -> None:
    """iPhone Voice Memos are .m4a, which libsndfile cannot read."""
    raw = tmp_path / "raw"
    raw.mkdir()
    source = tmp_path / "src.wav"
    sf.write(str(source), _voice(), SR)
    subprocess.run(
        ["afconvert", "-f", "m4af", "-d", "aac", str(source), str(raw / "memo.m4a")],
        check=True, capture_output=True,
    )

    results = run_intake(raw, tmp_path / "ws")
    assert results[0]["verdict"] != BLOCKED, results
    assert (tmp_path / "ws" / "memo" / "original.wav").exists()


def test_continuous_take_reports_no_noise_floor(tmp_path: Path) -> None:
    """A take with no gaps has no measurable noise floor — don't invent one."""
    path = tmp_path / "continuous.wav"
    sf.write(str(path), _voice(), SR)

    metrics = inspect(Track.load(path))
    assert metrics["noise_floor_dbfs"] is None, metrics
    assert metrics["snr_db"] is None, metrics

    _, _, warnings = judge(metrics)
    assert not any("noise floor" in w.lower() for w in warnings), warnings
    assert not any("signal-to-noise" in w.lower() for w in warnings), warnings


def test_hiss_between_phrases_is_detected(tmp_path: Path) -> None:
    """With real gaps, the estimator must still catch a genuinely noisy take."""
    rng = np.random.default_rng(1)
    phrase = _voice(seconds=2.0, amp=0.5)
    gap = np.zeros(int(SR * 1.5), dtype=np.float32)
    take = np.concatenate([phrase, gap, phrase, gap, phrase])
    noisy = (take + rng.normal(0, 0.02, len(take))).astype(np.float32)

    path = tmp_path / "hissy.wav"
    sf.write(str(path), noisy, SR)

    metrics = inspect(Track.load(path))
    assert metrics["noise_floor_dbfs"] is not None, metrics
    assert metrics["noise_floor_dbfs"] > NOISY_FLOOR_DBFS, metrics

    _, _, warnings = judge(metrics)
    assert any("noise floor" in w.lower() for w in warnings), warnings


def test_quiet_gaps_without_hiss_are_not_flagged(tmp_path: Path) -> None:
    """Gaps plus a clean floor should measure fine and raise nothing."""
    phrase = _voice(seconds=2.0, amp=0.5)
    gap = np.zeros(int(SR * 1.5), dtype=np.float32)
    path = tmp_path / "clean_gaps.wav"
    sf.write(str(path), np.concatenate([phrase, gap, phrase, gap, phrase]), SR)

    metrics = inspect(Track.load(path))
    _, blockers, warnings = judge(metrics)
    assert blockers == []
    assert not any("noise floor" in w.lower() for w in warnings), warnings
