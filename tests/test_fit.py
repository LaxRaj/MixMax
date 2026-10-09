"""G3 — a candidate is made to fit the vocal, or rejected with the reason."""

from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np
import pytest
from click.testing import CliRunner

from producer.cli import cli
from producer.generate.fit import (
    KEY_CLASH_MAX,
    MAX_STRETCH,
    fit_candidate,
    fold_ratio,
    harmony_profile,
    key_clash,
)
from producer.generate.spec import estimate_tempo
from tests.synth import A2, SR, backing, melody_vocal, write

# What `producer spec` reports for the synthetic vocal; pinned so each fit test
# exercises fit, not key detection.
VOCAL_SPEC = {"tempo_bpm": 100.0, "key": "A minor"}


@pytest.fixture(scope="module")
def audio(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("fit")
    return {
        "vocal": write(root / "vocal.wav", melody_vocal(100.0)),
        "matched": write(root / "matched.wav", backing(100.0)),
        "fast": write(root / "fast.wav", backing(106.0)),
        "far": write(root / "far.wav", backing(125.0)),
        "fsharp": write(root / "fsharp.wav", backing(100.0, tonic=A2 - 3)),
        "bflat": write(root / "bflat.wav", backing(100.0, tonic=A2 + 1)),
        "fifth": write(root / "fifth.wav", backing(100.0, tonic=A2 - 5)),
    }


def _tempo(path: Path) -> float | None:
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    return estimate_tempo(y, sr)[0]


def test_the_synthetic_vocal_is_what_the_pinned_spec_says(audio: dict[str, Path]) -> None:
    from producer.generate.spec import analyze_spec

    spec = analyze_spec(audio["vocal"])
    assert spec.tempo_bpm == pytest.approx(100.0, abs=1.0)
    assert spec.key == "A minor"


def test_matched_candidate_passes_untouched(audio: dict[str, Path], tmp_path: Path) -> None:
    result = fit_candidate(audio["vocal"], audio["matched"], VOCAL_SPEC, tmp_path / "out.wav")

    assert result.passed and not result.reasons
    assert result.stretch == 1.0 and result.pitch_shift_semitones == 0
    assert result.key_clash_score <= KEY_CLASH_MAX
    assert result.alignment is not None
    assert result.vocal_masking_db is not None and np.isfinite(result.vocal_masking_db)
    assert Path(result.output).exists()


def test_fast_candidate_is_stretched_onto_the_vocal_tempo(audio: dict[str, Path], tmp_path: Path) -> None:
    result = fit_candidate(audio["vocal"], audio["fast"], VOCAL_SPEC, tmp_path / "out.wav")

    assert result.passed
    assert result.candidate_tempo_bpm == pytest.approx(106.0, abs=1.0)
    assert result.stretch == pytest.approx(100.0 / 106.0, abs=0.01)
    assert any("stretched" in note for note in result.notes)
    # The proof is the rendered file, not the number in the report.
    assert _tempo(Path(result.output)) == pytest.approx(100.0, abs=1.0)


def test_candidate_too_far_off_tempo_is_rejected(audio: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    result = fit_candidate(audio["vocal"], audio["far"], VOCAL_SPEC, out)

    assert not result.passed
    assert abs(result.tempo_ratio - 1.0) > MAX_STRETCH
    assert any(reason.startswith("Tempo") for reason in result.reasons)
    assert result.output is None and not out.exists()


def test_wrong_key_is_shifted_and_says_so(audio: dict[str, Path], tmp_path: Path) -> None:
    # F# minor under an A minor vocal: two semitones down lands on E minor,
    # which differs from A minor by one note.
    result = fit_candidate(audio["vocal"], audio["fsharp"], VOCAL_SPEC, tmp_path / "out.wav")

    assert result.passed
    assert result.pitch_shift_semitones == -2
    assert result.key_clash_score <= KEY_CLASH_MAX
    note = next(n for n in result.notes if n.startswith("Key"))
    assert "F# minor" in note and "A minor" in note and "-2" in note

    # The rendered file really is in a key that sits with the vocal now.
    y, sr = librosa.load(result.output, sr=SR, mono=True)
    assert key_clash(harmony_profile(y, sr), "A minor") <= KEY_CLASH_MAX


def test_key_no_small_shift_can_fix_is_rejected(audio: dict[str, Path], tmp_path: Path, monkeypatch) -> None:
    # A semitone above the vocal's key is the worst clash there is, and one
    # semitone down fixes it. Forbid shifting to prove the rejection path.
    monkeypatch.setattr("producer.generate.fit.MAX_SHIFT_SEMITONES", 0)
    result = fit_candidate(audio["vocal"], audio["bflat"], VOCAL_SPEC, tmp_path / "out.wav")

    assert not result.passed
    reason = next(r for r in result.reasons if r.startswith("Key"))
    assert "A# minor" in reason and "A minor" in reason
    assert result.key_clash_score > KEY_CLASH_MAX


def test_every_rejection_carries_a_reason(audio: dict[str, Path], tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("producer.generate.fit.MAX_SHIFT_SEMITONES", 0)
    for name in ("far", "fsharp", "bflat"):
        result = fit_candidate(audio["vocal"], audio[name], VOCAL_SPEC, tmp_path / f"{name}.wav")
        assert not result.passed
        assert result.reasons and all(len(reason) > 20 for reason in result.reasons)


def test_a_key_a_fifth_away_is_not_a_clash(audio: dict[str, Path], tmp_path: Path) -> None:
    result = fit_candidate(audio["vocal"], audio["fifth"], VOCAL_SPEC, tmp_path / "out.wav")
    assert result.passed and result.pitch_shift_semitones == 0


def test_unmeasured_vocal_is_not_checked_and_the_report_says_so(audio: dict[str, Path], tmp_path: Path) -> None:
    # An a cappella rap has neither tempo nor key. Nothing can be verified, and
    # a pass must not read as though it had been.
    result = fit_candidate(audio["vocal"], audio["bflat"], {"tempo_bpm": None, "key": None},
                           tmp_path / "out.wav")

    assert result.passed
    assert result.tempo_ratio is None and result.key_clash_score is None
    assert any(note.startswith("Tempo not checked") for note in result.notes)
    assert any(note.startswith("Key not checked") for note in result.notes)


def test_fold_ratio_treats_double_time_as_the_same_groove() -> None:
    assert fold_ratio(2.0) == pytest.approx(1.0)
    assert fold_ratio(0.52) == pytest.approx(1.04)
    assert fold_ratio(1.06) == pytest.approx(1.06)


def test_fit_cli_passes_and_rejects_with_exit_code_zero(audio: dict[str, Path], tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(VOCAL_SPEC))

    out = tmp_path / "fitted.wav"
    result = CliRunner().invoke(cli, [
        "fit", "--vocal", str(audio["vocal"]), "--candidate", str(audio["matched"]),
        "--out", str(out), "--spec", str(spec),
    ])
    assert result.exit_code == 0, result.output
    assert "FIT" in result.output and out.exists()
    assert json.loads(out.with_suffix(".fit.json").read_text())["passed"] is True

    out = tmp_path / "rejected.wav"
    result = CliRunner().invoke(cli, [
        "fit", "--vocal", str(audio["vocal"]), "--candidate", str(audio["far"]),
        "--out", str(out), "--spec", str(spec),
    ])
    assert result.exit_code == 0, result.output
    assert "REJECT" in result.output and "Tempo" in result.output
    assert not out.exists()
    assert json.loads(out.with_suffix(".fit.json").read_text())["reasons"]
