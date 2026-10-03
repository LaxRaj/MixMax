"""The reference library: learning measured facts from real releases."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.library import (
    FULL_MIX,
    MIN_CORPUS_FOR_THRESHOLDS,
    VOCAL_ONLY,
    Library,
    analyze_reference,
    classify_kind,
    derive_thresholds,
    discover_references,
    estimate_key,
    match_reference,
    summarize,
)
from producer.qa import DEFAULT_PROFILE, QAProfile, run_qa

SR = 44100


def _song(seconds=12.0, bpm=120.0, root=220.0, bass=0.5, bright=0.08, amp=0.4) -> np.ndarray:
    """A crude song: a bassline, a tone, a click at a known tempo."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    out = amp * (np.sin(2 * np.pi * root * t) + 0.3 * np.sin(2 * np.pi * root * 2 * t))
    out = out + bass * amp * np.sin(2 * np.pi * (root / 4) * t)
    out = out + bright * amp * np.sin(2 * np.pi * 7000 * t)
    step = int(SR * 60.0 / bpm)
    click = np.zeros_like(t)
    burst = np.exp(-np.linspace(0, 9, int(0.01 * SR)))
    for start in range(0, len(t) - len(burst), step):
        click[start:start + len(burst)] += burst
    return ((out + 0.35 * click) / 2.2).astype(np.float32)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """Ten 'releases' across two genres, with deliberately different loudness."""
    d = tmp_path / "refs"
    d.mkdir()
    for i in range(6):
        sf.write(str(d / f"soul_{i}.wav"), _song(bpm=92 + i, amp=0.30 + i * 0.03), SR)
    for i in range(4):
        sf.write(str(d / f"trap_{i}.wav"), _song(bpm=140 + i, bass=0.9, amp=0.45 + i * 0.02), SR)
    return d


def test_discover_is_recursive(corpus: Path) -> None:
    assert len(discover_references(corpus)) == 10


def test_analyze_captures_musical_and_measured_features(corpus: Path) -> None:
    entry = analyze_reference(corpus / "soul_0.wav", genre="soul")
    assert entry.genre == "soul"
    assert 80 <= entry.tempo_bpm <= 105, entry.tempo_bpm
    assert entry.key != "unknown"
    assert entry.band_balance_db
    assert np.isfinite(entry.lufs)


def test_key_estimation_returns_a_real_key() -> None:
    t = np.linspace(0, 6, SR * 6, endpoint=False)
    chord = sum(np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0))
    key = estimate_key((chord / 3).astype(np.float32), SR)
    assert key.split()[0] in {"C", "E", "G", "A"}, key
    assert key.split()[1] in {"major", "minor"}


def test_full_mix_and_vocal_only_are_distinguished() -> None:
    assert classify_kind({"sub": -30.0, "low": -4.0}) == FULL_MIX
    assert classify_kind({"sub": -70.0, "low": -40.0}) == VOCAL_ONLY


def test_library_roundtrip_and_idempotent_add(tmp_path: Path, corpus: Path) -> None:
    lib = Library()
    entry = analyze_reference(corpus / "soul_0.wav", genre="soul")
    lib.add(entry)
    lib.add(entry)  # re-ingesting the same folder must not duplicate
    assert len(lib) == 1

    path = lib.save(tmp_path / "lib.json")
    assert len(Library.load(path)) == 1
    assert Library.load(tmp_path / "missing.json").entries == []


def test_filter_by_genre(tmp_path: Path, corpus: Path) -> None:
    lib = Library()
    for p in discover_references(corpus):
        lib.add(analyze_reference(p, genre="soul" if p.name.startswith("soul") else "trap"))
    assert len(lib.filter(genre="soul")) == 6
    assert len(lib.filter(genre="trap")) == 4
    assert lib.genres == ["soul", "trap"]


def _build(corpus: Path) -> Library:
    lib = Library()
    for p in discover_references(corpus):
        lib.add(analyze_reference(p, genre="soul" if p.name.startswith("soul") else "trap"))
    return lib


def test_summarize_reports_spread_not_just_averages(corpus: Path) -> None:
    stats = summarize(_build(corpus).entries)
    assert stats.count == 10
    assert stats.lufs["min"] < stats.lufs["median"] < stats.lufs["max"]
    assert set(stats.lufs) >= {"n", "min", "p5", "median", "p95", "max"}
    assert stats.band_balance_db


def test_derive_thresholds_replaces_the_guessed_window(corpus: Path) -> None:
    profile = derive_thresholds(_build(corpus).entries)
    assert profile["lufs_min"] < profile["lufs_max"]
    assert profile["derived_from"] == 10
    # It must be grounded in the corpus, not echo the hand-picked default.
    assert (profile["lufs_min"], profile["lufs_max"]) != (
        DEFAULT_PROFILE.lufs_min, DEFAULT_PROFILE.lufs_max,
    )


def test_thresholds_refuse_a_tiny_corpus(corpus: Path) -> None:
    entries = _build(corpus).entries[: MIN_CORPUS_FOR_THRESHOLDS - 1]
    with pytest.raises(ValueError, match="at least"):
        derive_thresholds(entries)


def test_derived_profile_drives_the_qa_gate(tmp_path: Path, corpus: Path) -> None:
    profile = derive_thresholds(_build(corpus).entries)
    path = tmp_path / "qa_profile.json"
    path.write_text(json.dumps(profile))

    loaded = QAProfile.load(path)
    assert loaded.lufs_min == profile["lufs_min"]
    assert "reference" in loaded.source

    report = run_qa(corpus / "soul_0.wav", loaded)
    assert "pass" in report


def test_match_prefers_the_closest_reference(corpus: Path) -> None:
    from producer.benchmark import measure

    lib = _build(corpus)
    metrics = measure(corpus / "trap_0.wav")
    ranked = match_reference(lib, metrics, tempo_bpm=140.0, genre="trap")

    assert len(ranked) == 4
    assert ranked[0][1] <= ranked[-1][1]
    assert ranked[0][0].genre == "trap"


def test_match_tolerates_half_and_double_time(corpus: Path) -> None:
    """70 and 140 BPM are the same groove; that must not read as distant."""
    from producer.benchmark import measure

    lib = _build(corpus)
    metrics = measure(corpus / "trap_0.wav")
    at_140 = match_reference(lib, metrics, tempo_bpm=140.0, genre="trap")[0][1]
    at_70 = match_reference(lib, metrics, tempo_bpm=70.0, genre="trap")[0][1]
    assert abs(at_140 - at_70) < 0.25, (at_140, at_70)


def test_match_returns_nothing_for_an_unknown_genre(corpus: Path) -> None:
    from producer.benchmark import measure

    assert match_reference(_build(corpus), measure(corpus / "soul_0.wav"), genre="polka") == []


def test_library_cli_end_to_end(tmp_path: Path, corpus: Path) -> None:
    runner = CliRunner()
    lib_path = tmp_path / "lib.json"

    add = runner.invoke(cli, ["library", "add", "--input", str(corpus),
                              "--genre", "soul", "--library", str(lib_path)])
    assert add.exit_code == 0, add.output
    assert "10 reference(s) catalogued" in add.output

    listed = runner.invoke(cli, ["library", "list", "--library", str(lib_path)])
    assert listed.exit_code == 0 and "soul_0" in listed.output

    out = tmp_path / "qa_profile.json"
    thresholds = runner.invoke(cli, ["library", "thresholds", "--library", str(lib_path),
                                     "--out", str(out)])
    assert thresholds.exit_code == 0, thresholds.output
    assert "LUFS window" in thresholds.output
    assert "not what sounds good" in thresholds.output
    assert json.loads(out.read_text())["derived_from"] == 10

    match = runner.invoke(cli, ["library", "match", "--vocal", str(corpus / "soul_0.wav"),
                                "--library", str(lib_path)])
    assert match.exit_code == 0, match.output
    assert "Best:" in match.output


def test_library_cli_fails_cleanly_when_empty(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["library", "list", "--library", str(tmp_path / "none.json")])
    assert result.exit_code != 0
    assert "empty" in result.output.lower()


def test_thresholds_report_how_much_of_the_corpus_they_admit(corpus: Path) -> None:
    """A window most of its own corpus fails would be a broken window."""
    profile = derive_thresholds(_build(corpus).entries)
    assert profile["corpus_inside_window"] <= profile["derived_from"]
    assert profile["corpus_pass_rate"] >= 0.8, profile
