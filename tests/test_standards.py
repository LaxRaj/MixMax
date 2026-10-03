"""Published loudness standards, and what they do to a master."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.standards import (
    STANDARDS,
    Standard,
    evaluate,
    evaluate_all,
    load_standards,
    summarize_conformance,
    to_qa_profile,
)

SR = 44100


def _metrics(lufs: float, true_peak: float = -3.0, crest: float = 8.0) -> dict:
    return {
        "lufs": lufs,
        "true_peak_dbtp": true_peak,
        "crest_factor_db": crest,
        "band_balance_db": {},
    }


def test_every_builtin_is_well_formed() -> None:
    for key, s in STANDARDS.items():
        assert s.key == key
        assert -30.0 <= s.target_lufs <= -5.0, key
        assert -3.0 <= s.max_true_peak_dbtp <= 0.0, key
        assert s.tolerance_lu > 0, key
        # Every figure must say where it came from and when.
        assert s.source, f"{key} has no source"
        assert s.as_of, f"{key} has no as_of date"


def test_loud_master_is_attenuated_to_the_target() -> None:
    c = evaluate(_metrics(-7.0), STANDARDS["spotify"])
    assert c.normalization_gain_db == pytest.approx(-7.0)
    assert c.delivered_lufs == pytest.approx(-14.0)
    # The headline point: the loudness was discarded.
    assert any("buys nothing" in x for x in c.consequences)


def test_quiet_master_is_raised_where_the_platform_does_that() -> None:
    c = evaluate(_metrics(-20.0), STANDARDS["spotify"])
    assert c.normalization_gain_db == pytest.approx(6.0)
    assert c.delivered_lufs == pytest.approx(-14.0)


def test_youtube_does_not_raise_quiet_masters() -> None:
    """A platform that only attenuates leaves a quiet master sounding weak."""
    c = evaluate(_metrics(-20.0), STANDARDS["youtube"])
    assert c.normalization_gain_db == 0.0
    assert c.delivered_lufs == pytest.approx(-20.0)
    assert any("below everything" in x for x in c.consequences)


def test_raising_a_master_can_breach_true_peak() -> None:
    c = evaluate(_metrics(-20.0, true_peak=-2.0), STANDARDS["spotify"])
    assert c.true_peak_after_norm_dbtp == pytest.approx(4.0)
    assert not c.conforms
    assert any("after normalization" in i for i in c.issues)


def test_true_peak_over_ceiling_is_flagged() -> None:
    c = evaluate(_metrics(-14.0, true_peak=0.2), STANDARDS["spotify"])
    assert not c.conforms
    assert any("true peak" in i for i in c.issues)
    assert any("re-encodes" in x for x in c.consequences)


def test_on_target_master_conforms_cleanly() -> None:
    c = evaluate(_metrics(-14.0, true_peak=-1.5), STANDARDS["spotify"])
    assert c.conforms
    assert c.issues == []
    assert c.normalization_gain_db == pytest.approx(0.0)


def test_amazon_has_a_stricter_peak_ceiling() -> None:
    hot = _metrics(-14.0, true_peak=-1.5)
    assert evaluate(hot, STANDARDS["spotify"]).conforms
    assert not evaluate(hot, STANDARDS["amazon_music"]).conforms


def test_unmeasurable_loudness_does_not_crash() -> None:
    c = evaluate(_metrics(float("-inf")), STANDARDS["spotify"])
    assert not c.conforms
    assert "could not be measured" in c.issues[0]


def test_summary_states_the_point_once_not_per_platform() -> None:
    """Eight copies of one sentence buries the finding it is making."""
    metrics = _metrics(-7.0)
    notes = summarize_conformance(metrics, evaluate_all(metrics), STANDARDS)
    assert len(notes) <= 3, notes
    assert sum("buys nothing" in n for n in notes) == 1
    assert any("headroom" in n for n in notes)


def test_summary_is_empty_for_a_compliant_master() -> None:
    metrics = _metrics(-14.0, true_peak=-1.5)
    notes = summarize_conformance(metrics, [evaluate(metrics, STANDARDS["spotify"])], STANDARDS)
    assert notes == []


def test_qa_profile_brackets_the_target() -> None:
    profile = to_qa_profile(STANDARDS["spotify"])
    assert profile["lufs_min"] < -14.0 < profile["lufs_max"]
    assert profile["true_peak_max_dbtp"] == -1.0
    # The provenance and the warning must travel with the numbers.
    assert "Spotify" in profile["notes"]
    assert "Verify" in profile["notes"]


def test_standards_table_can_be_replaced_from_json(tmp_path: Path) -> None:
    """Targets drift; correcting one must not need a code change."""
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"standards": [
        {"key": "house", "name": "House rules", "target_lufs": -11.0,
         "max_true_peak_dbtp": -0.5, "source": "internal", "as_of": "2026"},
    ]}))

    table = load_standards(path)
    assert list(table) == ["house"]
    assert evaluate(_metrics(-11.0), table["house"]).conforms


def test_standards_list_command() -> None:
    result = CliRunner().invoke(cli, ["standards", "list"])
    assert result.exit_code == 0, result.output
    assert "spotify" in result.output
    assert "drift" in result.output  # the caveat must always ship


def test_standards_check_command(tmp_path: Path) -> None:
    path = tmp_path / "hot.wav"
    t = np.linspace(0, 6, SR * 6, endpoint=False)
    sf.write(str(path), (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)

    result = CliRunner().invoke(cli, ["standards", "check", "--file", str(path)])
    assert result.exit_code == 0, result.output
    assert "Spotify" in result.output
    assert "delivered" in result.output


def test_standards_check_rejects_unknown(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    t = np.linspace(0, 3, SR * 3, endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)

    result = CliRunner().invoke(
        cli, ["standards", "check", "--file", str(path), "--standard", "nope"]
    )
    assert result.exit_code != 0
    assert "Unknown standard" in result.output


def test_standards_profile_drives_qa(tmp_path: Path) -> None:
    out = tmp_path / "qa_profile.json"
    runner = CliRunner()
    made = runner.invoke(cli, ["standards", "profile", "--standard", "spotify", "--out", str(out)])
    assert made.exit_code == 0, made.output

    path = tmp_path / "a.wav"
    t = np.linspace(0, 6, SR * 6, endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)

    checked = runner.invoke(cli, ["qa", "--file", str(path), "--profile", str(out)])
    assert checked.exit_code == 0, checked.output
    assert "lufs" in checked.output


def test_qa_accepts_a_standard_directly(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    t = np.linspace(0, 6, SR * 6, endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)

    result = CliRunner().invoke(cli, ["qa", "--file", str(path), "--standard", "spotify"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["lufs"]


def test_qa_rejects_profile_and_standard_together(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    t = np.linspace(0, 3, SR * 3, endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"lufs_min": -16, "lufs_max": -9}))

    result = CliRunner().invoke(
        cli, ["qa", "--file", str(path), "--profile", str(profile), "--standard", "spotify"]
    )
    assert result.exit_code != 0
    assert "not both" in result.output
