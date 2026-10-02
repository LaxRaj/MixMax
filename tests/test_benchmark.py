"""M6 — objective benchmarking against other services."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from producer.benchmark import BANDS, build_benchmark_report, compare, discover_versions, measure
from producer.cli import cli


def test_measure_shape(versions_dir: Path) -> None:
    m = measure(versions_dir / "producer.wav")
    assert set(m) == {
        "source", "lufs", "lra", "true_peak_dbtp", "sample_peak_dbfs",
        "crest_factor_db", "stereo_correlation", "spectral_centroid_hz",
        "band_balance_db", "duration_s", "sample_rate", "channels",
    }
    assert set(m["band_balance_db"]) == set(BANDS)
    assert m["source"] == "producer"


def test_true_peak_is_at_or_above_sample_peak(versions_dir: Path) -> None:
    """Oversampling can only reveal peaks, never hide them."""
    m = measure(versions_dir / "producer.wav")
    assert m["true_peak_dbtp"] >= m["sample_peak_dbfs"] - 0.01, m


def test_discover_versions_keys_by_stem(versions_dir: Path) -> None:
    versions = discover_versions(versions_dir)
    assert set(versions) == {"producer", "rival", "original"}


def test_louder_version_measures_louder(versions_dir: Path) -> None:
    producer = measure(versions_dir / "producer.wav")
    rival = measure(versions_dir / "rival.wav")
    assert producer["lufs"] > rival["lufs"]


def test_brighter_version_has_more_high_energy(versions_dir: Path) -> None:
    """The fixture gives `producer` far more 7 kHz content than `rival`.

    7 kHz sits in the `high` band (6-20 kHz), so that is where the difference
    must show up -- and it must be large, not noise-floor jitter.
    """
    comparison = compare(discover_versions(versions_dir), baseline="producer")
    delta = comparison["deltas_vs_baseline"]["rival"]["band_balance_db"]["high"]
    assert delta > 10.0, comparison


def test_interpretation_names_the_offending_band(versions_dir: Path) -> None:
    """A big `high` excess should produce advice pointing at the top end."""
    from producer.benchmark import interpret

    notes = "\n".join(interpret(compare(discover_versions(versions_dir), baseline="producer")))
    assert "high:" in notes, notes


def test_deltas_are_relative_to_baseline(versions_dir: Path) -> None:
    comparison = compare(discover_versions(versions_dir), baseline="producer")
    assert comparison["baseline"] == "producer"
    assert "producer" not in comparison["deltas_vs_baseline"]
    assert set(comparison["deltas_vs_baseline"]) == {"rival", "original"}


def test_report_renders_every_source(versions_dir: Path) -> None:
    report = build_benchmark_report(compare(discover_versions(versions_dir)))
    for source in ("producer", "rival", "original"):
        assert source in report
    assert "What to change" in report


def test_benchmark_command(tmp_path: Path, versions_dir: Path) -> None:
    out = tmp_path / "bench.md"
    result = CliRunner().invoke(
        cli, ["benchmark", "--versions-dir", str(versions_dir), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("# Benchmark")


def test_benchmark_rejects_unknown_baseline(versions_dir: Path) -> None:
    result = CliRunner().invoke(
        cli, ["benchmark", "--versions-dir", str(versions_dir), "--baseline", "nope"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_benchmark_needs_two_versions(tmp_path: Path, versions_dir: Path) -> None:
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    (lonely / "producer.wav").write_bytes((versions_dir / "producer.wav").read_bytes())
    result = CliRunner().invoke(cli, ["benchmark", "--versions-dir", str(lonely)])
    assert result.exit_code != 0
    assert "at least two" in result.output


def test_empty_bands_are_not_reported_as_differences(versions_dir: Path) -> None:
    """Noise floor vs noise floor is not a finding.

    The sine fixtures have no content in `sub` or `low_mid`, so despite large
    raw deltas there, the advice must stay silent about them.
    """
    from producer.benchmark import interpret

    notes = "\n".join(interpret(compare(discover_versions(versions_dir), baseline="producer")))
    assert "sub:" not in notes, notes
    assert "low_mid:" not in notes, notes
    assert "high_mid:" not in notes, notes
    # The band that genuinely differs must still survive the filter.
    assert "high:" in notes, notes


def test_band_relevance_floor() -> None:
    from producer.benchmark import band_is_audible

    assert band_is_audible(-3.0, -90.0) is True
    assert band_is_audible(-20.0, -25.0) is True   # normal music territory
    assert band_is_audible(-100.0, -120.0) is False
    assert band_is_audible(-56.0, -105.0) is False  # clipping harmonics only
    assert band_is_audible(None, None) is False


def test_inter_sample_overs_are_flagged(tmp_path: Path) -> None:
    """A master above -1 dBTP distorts after lossy encoding; say so."""
    import numpy as np
    import soundfile as sf
    from producer.benchmark import interpret

    sr = 44100
    t = np.linspace(0, 2.0, sr * 2, endpoint=False)
    hot = np.clip(np.sin(2 * np.pi * 997 * t) * 1.4, -1.0, 1.0).astype(np.float32)
    quiet = (np.sin(2 * np.pi * 997 * t) * 0.2).astype(np.float32)

    d = tmp_path / "v"
    d.mkdir()
    sf.write(str(d / "producer.wav"), hot, sr)
    sf.write(str(d / "landr.wav"), quiet, sr)

    notes = "\n".join(interpret(compare(discover_versions(d), baseline="producer")))
    assert "dBTP" in notes, notes
