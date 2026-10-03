"""Loudness targeting, and the shootout that makes limiting audible."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.benchmark import measure
from producer.cli import cli
from producer.library import FULL_MIX, VOCAL_ONLY
from producer.loudness import TOLERANCE_LU, normalize_to_target
from producer.shootout import build_shootout, build_shootout_report, detect_kind, parse_target
from producer.standards import load_standards

SR = 44100


def _track(seconds: float = 14.0, bass: float = 0.8, amp: float = 0.4) -> np.ndarray:
    """A full track: bass, chords, a lead, and a kick on the beat."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(5)
    out = amp * (np.sin(2 * np.pi * 220 * t) + 0.4 * np.sin(2 * np.pi * 330 * t))
    out = out + bass * amp * np.sin(2 * np.pi * 55 * t)
    step = int(SR * 0.6)
    env = np.exp(-np.linspace(0, 9, int(0.25 * SR)))
    kick = np.sin(2 * np.pi * 60 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(kick), step):
        out[start:start + len(kick)] += kick * 0.9
    return ((out / 2.6) + rng.normal(0, 0.002, len(t))).astype(np.float32)


def _vocal(seconds: float = 14.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (220 + 4 * np.sin(2 * np.pi * 5 * t)) / SR)
    return (0.4 * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3).astype(np.float32)


@pytest.fixture()
def rig(tmp_path: Path) -> dict:
    source = tmp_path / "track.wav"
    reference = tmp_path / "ref.wav"
    sf.write(str(source), _track(), SR)
    # A moderate reference, so `as_is` lands mid-loudness and a loud target
    # genuinely needs limiting to reach.
    sf.write(str(reference), (_track() * 0.8).astype(np.float32), SR)
    return {"source": source, "reference": reference, "out_dir": tmp_path / "versions"}


# ── loudness targeting ───────────────────────────────────────────────────


@pytest.mark.parametrize("target", [-8.0, -14.0, -16.0, -23.0])
def test_hits_every_target(tmp_path: Path, target: float) -> None:
    src = tmp_path / "a.wav"
    sf.write(str(src), _track(), SR)
    result = normalize_to_target(src, tmp_path / "out.wav", target)
    assert abs(result.achieved_lufs - target) <= TOLERANCE_LU, result.to_dict()


@pytest.mark.parametrize("target", [-6.0, -10.0, -14.0, -20.0])
def test_true_peak_ceiling_is_never_breached(tmp_path: Path, target: float) -> None:
    """The ceiling is what stops lossy encoders distorting; it is not optional."""
    src = tmp_path / "a.wav"
    sf.write(str(src), _track(), SR)
    result = normalize_to_target(src, tmp_path / "out.wav", target, ceiling_dbtp=-1.0)
    assert result.true_peak_dbtp <= -1.0 + 0.05, result.to_dict()


def test_quiet_targets_are_reached_without_limiting(tmp_path: Path) -> None:
    """Gain alone is transparent; limiting would alter what the test judges."""
    src = tmp_path / "a.wav"
    sf.write(str(src), _track(), SR)
    result = normalize_to_target(src, tmp_path / "out.wav", -28.0)
    assert result.limited is False


def test_loud_targets_require_limiting_and_cost_dynamics(tmp_path: Path) -> None:
    src = tmp_path / "a.wav"
    sf.write(str(src), _track(), SR)

    loud = normalize_to_target(src, tmp_path / "loud.wav", -8.0)
    quiet = normalize_to_target(src, tmp_path / "quiet.wav", -23.0)

    assert loud.limited is True
    assert quiet.limited is False
    # The whole point: loudness is bought with crest factor.
    assert measure(tmp_path / "loud.wav")["crest_factor_db"] < \
        measure(tmp_path / "quiet.wav")["crest_factor_db"] - 2.0


def test_unmeasurable_input_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "tiny.wav"
    sf.write(str(src), np.zeros(int(SR * 0.1), dtype=np.float32), SR)
    with pytest.raises(ValueError, match="too short or quiet"):
        normalize_to_target(src, tmp_path / "out.wav", -14.0)


# ── shootout ─────────────────────────────────────────────────────────────


def test_parse_standard_and_explicit_targets() -> None:
    table = load_standards()
    assert parse_target("spotify", table) == ("spotify", -14.0, -1.0)

    name, lufs, ceiling = parse_target("loud:-8", table)
    assert (name, lufs, ceiling) == ("loud_8", -8.0, -1.0)

    with pytest.raises(ValueError, match="Unknown target"):
        parse_target("nope", table)


def test_full_mix_skips_the_vocal_chain(rig: dict) -> None:
    """An 80 Hz highpass and de-ess would thin a track that has a kick in it."""
    assert detect_kind(rig["source"]) == FULL_MIX
    result = build_shootout(**rig, targets=("spotify",))
    assert result["kind"] == FULL_MIX
    assert result["vocal_chain_applied"] is False


def test_bare_vocal_still_gets_the_vocal_chain(tmp_path: Path) -> None:
    source = tmp_path / "vocal.wav"
    reference = tmp_path / "ref.wav"
    sf.write(str(source), _vocal(), SR)
    sf.write(str(reference), _vocal() * 1.3, SR)

    result = build_shootout(source, reference, tmp_path / "out", targets=("spotify",))
    assert result["kind"] == VOCAL_ONLY
    assert result["vocal_chain_applied"] is True


def test_renders_one_version_per_target(rig: dict) -> None:
    result = build_shootout(**rig, targets=("loud:-8", "spotify", "ebu_r128"))
    names = [v["name"] for v in result["versions"]]
    assert {"as_is", "loud_8", "spotify", "ebu_r128", "original"} <= set(names)
    for v in result["versions"]:
        assert Path(v["path"]).stat().st_size > 0


def test_a_loud_target_makes_the_comparison_non_vacuous(rig: dict) -> None:
    """Without limiting, every version is one master at different levels."""
    with_loud = build_shootout(**rig, targets=("loud:-8", "ebu_r128"))
    assert with_loud["differs_in_processing"] is True
    assert "loud_8" in with_loud["limited_versions"]


def test_all_quiet_targets_are_flagged_as_vacuous(rig: dict) -> None:
    result = build_shootout(**rig, targets=("ebu_r128",))
    assert result["differs_in_processing"] is False
    assert "vacuous" in build_shootout_report(result).lower()


def test_versions_identical_after_matching_are_named(rig: dict) -> None:
    """Two unlimited renders are the same audio; shipping both wastes a slot."""
    result = build_shootout(**rig, targets=("apple_music", "ebu_r128"))
    assert set(result["identical_after_matching"]) == {"apple_music", "ebu_r128"}


def test_competitors_are_carried_through(tmp_path: Path, rig: dict) -> None:
    rival = tmp_path / "landr.wav"
    sf.write(str(rival), np.tanh(_track() * 1.8) * 0.75, SR)

    result = build_shootout(**rig, targets=("spotify",), competitors={"landr": rival})
    assert "landr" in [v["name"] for v in result["versions"]]
    assert (rig["out_dir"] / "landr.wav").exists()


def test_unknown_target_is_refused(rig: dict) -> None:
    with pytest.raises(ValueError, match="Unknown target"):
        build_shootout(**rig, targets=("nope",))


def test_shootout_command(tmp_path: Path, rig: dict) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "shootout",
            "--source", str(rig["source"]),
            "--reference", str(rig["reference"]),
            "--out-dir", str(rig["out_dir"]),
            "--target", "loud:-8",
            "--target", "ebu_r128",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Limiting engaged" in result.output
    assert (rig["out_dir"] / "shootout.md").exists()
    assert (rig["out_dir"] / "shootout.json").exists()


def test_shootout_output_feeds_blindtest(rig: dict) -> None:
    """The handoff that matters: the folder must be a valid versions-dir."""
    from producer.benchmark import discover_versions
    from producer.blindtest import build_blind_test

    build_shootout(**rig, targets=("loud:-8", "ebu_r128"))
    versions = discover_versions(rig["out_dir"])
    assert len(versions) >= 3

    blind = build_blind_test(versions, rig["out_dir"].parent / "blind", seed=1)
    assert len(blind["mapping"]) == len(versions)
