"""Objective benchmarking — measure our master against commercial ones.

The point of this module is not pass/fail (that's `qa.py`). It's *diff*: given
the same source rendered by producer, LANDR, or anyone else, say exactly where
ours differs, in terms that map back to a stage in the mix chain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as pyln

from producer.audio import AUDIO_SUFFIXES, Track

# True-peak needs oversampling: inter-sample peaks hide between samples and are
# what actually clips a consumer DAC. 4x is the ITU-BS.1770 minimum.
TRUE_PEAK_OVERSAMPLE = 4

# EBU R128 loudness range.
LRA_WINDOW_S = 3.0
LRA_HOP_S = 1.0
LRA_ABSOLUTE_GATE = -70.0
LRA_RELATIVE_GATE = -20.0

# Spectral balance bands. Chosen so each maps to something you can act on:
# "sub" -> highpass cutoff, "high" -> de-ess / air, etc.
BANDS: dict[str, tuple[float, float]] = {
    "sub": (20.0, 60.0),
    "low": (60.0, 250.0),
    "low_mid": (250.0, 800.0),
    "mid": (800.0, 2500.0),
    "high_mid": (2500.0, 6000.0),
    "high": (6000.0, 20000.0),
}

SILENCE_FLOOR = 1e-12


@dataclass
class Measurement:
    """Everything we can say about one rendered master, without ears."""

    source: str
    lufs: float
    lra: float | None
    true_peak_dbtp: float
    sample_peak_dbfs: float
    crest_factor_db: float
    stereo_correlation: float | None
    spectral_centroid_hz: float
    band_balance_db: dict[str, float]
    duration_s: float
    sample_rate: int
    channels: int

    def to_dict(self) -> dict:
        return asdict(self)


def _db(value: float) -> float:
    return float(20.0 * np.log10(max(float(value), SILENCE_FLOOR)))


def _true_peak_dbtp(track: Track) -> float:
    """Peak after 4x oversampling, which catches inter-sample overs."""
    mono = np.ascontiguousarray(track.mono(), dtype=np.float32)
    if mono.size == 0:
        return float("-inf")
    upsampled = librosa.resample(
        mono,
        orig_sr=track.sample_rate,
        target_sr=track.sample_rate * TRUE_PEAK_OVERSAMPLE,
        res_type="soxr_hq",
    )
    return round(_db(np.max(np.abs(upsampled))), 2)


def _loudness_range(track: Track) -> float | None:
    """EBU R128 loudness range: the P95-P10 spread of gated short-term loudness.

    This is the "did mastering crush the dynamics" number. Returns None for
    audio too short to hold a single 3-second window.
    """
    meter = pyln.Meter(track.sample_rate)
    window = int(LRA_WINDOW_S * track.sample_rate)
    hop = int(LRA_HOP_S * track.sample_rate)
    if len(track.samples) < window:
        return None

    short_term: list[float] = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for start in range(0, len(track.samples) - window + 1, hop):
            value = meter.integrated_loudness(track.samples[start:start + window])
            if np.isfinite(value):
                short_term.append(float(value))

    gated = [v for v in short_term if v > LRA_ABSOLUTE_GATE]
    if len(gated) < 2:
        return None

    # Relative gate, referenced to the mean loudness of the absolutely-gated set.
    mean_loudness = 10.0 * np.log10(np.mean([10 ** (v / 10.0) for v in gated]))
    gated = [v for v in gated if v > mean_loudness + LRA_RELATIVE_GATE]
    if len(gated) < 2:
        return None

    return round(float(np.percentile(gated, 95) - np.percentile(gated, 10)), 2)


def _crest_factor_db(samples: np.ndarray) -> float:
    """Peak-to-RMS. Falls as a limiter squashes the signal."""
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if rms <= 0:
        return 0.0
    return round(_db(peak) - _db(rms), 2)


def _stereo_correlation(track: Track) -> float | None:
    if track.channels < 2:
        return None
    left = track.samples[:, 0].astype(np.float64)
    right = track.samples[:, 1].astype(np.float64)
    if left.std() == 0 or right.std() == 0:
        return None
    correlation = float(np.corrcoef(left, right)[0, 1])
    return round(correlation, 3) if np.isfinite(correlation) else None


def _band_balance_db(track: Track) -> dict[str, float]:
    """Energy per band, in dB relative to total — the tonal fingerprint.

    Relative rather than absolute, so two masters at different loudness are
    still directly comparable.
    """
    mono = track.mono()
    if mono.size == 0:
        return {name: float("-inf") for name in BANDS}

    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / track.sample_rate)
    power = np.square(spectrum, dtype=np.float64)
    total = power.sum()
    if total <= 0:
        return {name: float("-inf") for name in BANDS}

    balance: dict[str, float] = {}
    for name, (low, high) in BANDS.items():
        mask = (freqs >= low) & (freqs < high)
        fraction = power[mask].sum() / total
        balance[name] = round(float(10.0 * np.log10(max(fraction, SILENCE_FLOOR))), 2)
    return balance


def measure(path: str | Path, source: str | None = None) -> dict:
    """Measure one rendered master."""
    path = Path(path)
    track = Track.load(path)
    meter = pyln.Meter(track.sample_rate)

    if track.duration_s >= meter.block_size:
        with np.errstate(divide="ignore", invalid="ignore"):
            lufs = float(meter.integrated_loudness(track.samples))
    else:
        lufs = float("-inf")

    mono = track.mono()
    measurement = Measurement(
        source=source or path.stem,
        lufs=round(lufs, 2) if np.isfinite(lufs) else lufs,
        lra=_loudness_range(track),
        true_peak_dbtp=_true_peak_dbtp(track),
        sample_peak_dbfs=round(_db(np.max(np.abs(track.samples))) if track.samples.size else float("-inf"), 2),
        crest_factor_db=_crest_factor_db(mono),
        stereo_correlation=_stereo_correlation(track),
        spectral_centroid_hz=round(
            float(np.mean(librosa.feature.spectral_centroid(y=np.ascontiguousarray(mono, dtype=np.float32),
                                                            sr=track.sample_rate))), 1
        ),
        band_balance_db=_band_balance_db(track),
        duration_s=round(track.duration_s, 2),
        sample_rate=track.sample_rate,
        channels=track.channels,
    )
    return measurement.to_dict()


def discover_versions(directory: str | Path) -> dict[str, Path]:
    """Every audio file in `directory`, keyed by filename stem as the source name.

    The convention is one folder per song, one file per service:
        comparisons/my_song/{producer,landr,original}.wav
    """
    directory = Path(directory)
    return {
        p.stem: p
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    }


def compare(versions: dict[str, Path], baseline: str = "producer") -> dict:
    """Measure every version and diff them against `baseline`."""
    measurements = [measure(path, source=name) for name, path in versions.items()]
    by_source = {m["source"]: m for m in measurements}

    deltas: dict[str, dict] = {}
    if baseline in by_source:
        base = by_source[baseline]
        for source, m in by_source.items():
            if source == baseline:
                continue
            deltas[source] = {
                "lufs": _delta(base["lufs"], m["lufs"]),
                "lra": _delta(base["lra"], m["lra"]),
                "true_peak_dbtp": _delta(base["true_peak_dbtp"], m["true_peak_dbtp"]),
                "crest_factor_db": _delta(base["crest_factor_db"], m["crest_factor_db"]),
                "spectral_centroid_hz": _delta(base["spectral_centroid_hz"], m["spectral_centroid_hz"]),
                "band_balance_db": {
                    band: _delta(base["band_balance_db"][band], m["band_balance_db"][band])
                    for band in BANDS
                },
            }

    return {"baseline": baseline, "measurements": measurements, "deltas_vs_baseline": deltas}


def _delta(base: float | None, other: float | None) -> float | None:
    """base - other, i.e. how far OUR value sits from theirs. None if unmeasurable."""
    if base is None or other is None:
        return None
    if not (np.isfinite(base) and np.isfinite(other)):
        return None
    return round(float(base - other), 2)


# A band difference below this is inaudible in practice; don't send anyone
# chasing it.
NOTABLE_DB = 1.5
NOTABLE_CREST_DB = 2.0

# A band holding less than this share of total energy is inaudible. Comparing
# two near-empty bands produces huge, meaningless deltas (noise floor vs noise
# floor), so anything below this is reported as "not present" instead.
#
# Heuristic, and deliberately conservative: in real full-range music every band
# sits roughly -3 to -25 dB relative to total, so this should never fire. It
# exists to stop clipping harmonics and synthetic test tones generating
# confident advice about bands nobody can hear. Revisit against real material.
BAND_RELEVANCE_FLOOR = -45.0

# Streaming encoders need headroom; above this, lossy codecs distort.
TRUE_PEAK_CEILING_DBTP = -1.0

# Which stage of the chain to reach for when a band is off.
BAND_ADVICE = {
    "sub": ("HighpassFilter cutoff", "raise toward 100 Hz", "lower toward 60 Hz"),
    "low": ("low-shelf / proximity", "cut some body", "add some body"),
    "low_mid": ("low-mid buildup", "cut — this is where 'muddy' lives", "add warmth"),
    "mid": ("midrange presence", "cut — can sound boxy/honky", "add presence"),
    "high_mid": ("de-ess PeakFilter at 7 kHz", "deepen the cut — likely harsh/sibilant", "ease the cut"),
    "high": ("air / top end", "cut — likely brittle", "add air; consider a high shelf"),
}


def _fmt(value: float | None, spec: str = ".2f", suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    return f"{value:{spec}}{suffix}"


def _signed(value: float | None, suffix: str = " dB") -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    return f"{value:+.2f}{suffix}"


def band_is_audible(base_db: float | None, other_db: float | None) -> bool:
    """True when at least one side actually has content in this band."""
    values = [v for v in (base_db, other_db) if v is not None and np.isfinite(v)]
    return bool(values) and max(values) >= BAND_RELEVANCE_FLOOR


def interpret(comparison: dict) -> list[str]:
    """Turn the deltas into plain-language, actionable notes."""
    notes: list[str] = []
    baseline = comparison["baseline"]
    by_source = {m["source"]: m for m in comparison["measurements"]}
    base_bands = by_source.get(baseline, {}).get("band_balance_db", {})

    base_peak = by_source.get(baseline, {}).get("true_peak_dbtp")
    peak_note: list[str] = []
    if base_peak is not None and np.isfinite(base_peak) and base_peak > TRUE_PEAK_CEILING_DBTP:
        peak_note.append(
            f"True peak is {base_peak:+.2f} dBTP, above the {TRUE_PEAK_CEILING_DBTP:.0f} dBTP "
            "ceiling. Inter-sample overs survive the WAV but distort once Spotify or "
            "YouTube re-encode it. Pull the master down or add a true-peak limiter."
        )

    for source, delta in comparison["deltas_vs_baseline"].items():
        lines: list[str] = list(peak_note)
        other_bands = by_source.get(source, {}).get("band_balance_db", {})

        crest = delta.get("crest_factor_db")
        if crest is not None and abs(crest) >= NOTABLE_CREST_DB:
            if crest < 0:
                lines.append(
                    f"Crest factor {_signed(crest)} vs {source} — ours is more squashed. "
                    "Ease the Compressor ratio or raise its threshold."
                )
            else:
                lines.append(
                    f"Crest factor {_signed(crest)} vs {source} — ours is more dynamic. "
                    "That may be good, or may read as 'unfinished' next to a commercial master."
                )

        lra = delta.get("lra")
        if lra is not None and abs(lra) >= NOTABLE_CREST_DB:
            direction = "narrower" if lra < 0 else "wider"
            lines.append(f"Loudness range {_signed(lra, ' LU')} vs {source} — ours is {direction}.")

        for band, value in (delta.get("band_balance_db") or {}).items():
            if value is None or abs(value) < NOTABLE_DB:
                continue
            if not band_is_audible(base_bands.get(band), other_bands.get(band)):
                continue  # both sides are effectively silent here
            label, too_much, too_little = BAND_ADVICE[band]
            action = too_much if value > 0 else too_little
            lines.append(
                f"{band}: {_signed(value)} vs {source} — "
                f"{'more' if value > 0 else 'less'} energy than theirs. {label}: {action}."
            )

        if not lines:
            lines.append(f"No notable differences from {source} above the {NOTABLE_DB} dB threshold.")
        lines.append("`n/a` bands hold no audible content in either version.")

        notes.append(f"### {baseline} vs {source}\n\n" + "\n".join(f"- {l}" for l in lines))

    return notes


def build_benchmark_report(comparison: dict, title: str = "Benchmark") -> str:
    """Markdown: the measurement table, the deltas, and what to do about them."""
    measurements = comparison["measurements"]
    baseline = comparison["baseline"]

    lines = [
        f"# {title}",
        "",
        "Objective comparison of the same source rendered by each service. "
        "Loudness differences are expected and are *not* a quality signal on their own — "
        "see the blind listening test for that.",
        "",
        "## Measurements",
        "",
        "| Source | LUFS | LRA | True peak | Crest | Centroid | Stereo corr |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in measurements:
        marker = " ⬅" if m["source"] == baseline else ""
        lines.append(
            "| {src}{mark} | {lufs} | {lra} | {tp} | {crest} | {cent} | {corr} |".format(
                src=m["source"], mark=marker,
                lufs=_fmt(m["lufs"], ".1f"),
                lra=_fmt(m["lra"], ".1f", " LU"),
                tp=_fmt(m["true_peak_dbtp"], ".2f", " dBTP"),
                crest=_fmt(m["crest_factor_db"], ".1f", " dB"),
                cent=_fmt(m["spectral_centroid_hz"], ".0f", " Hz"),
                corr=_fmt(m["stereo_correlation"], ".2f"),
            )
        )

    lines += ["", "## Spectral balance (dB relative to total energy)", "",
              "| Source | " + " | ".join(BANDS) + " |",
              "| --- |" + " --- |" * len(BANDS)]
    for m in measurements:
        cells = " | ".join(_fmt(m["band_balance_db"][b], ".1f") for b in BANDS)
        lines.append(f"| {m['source']} | {cells} |")

    if comparison["deltas_vs_baseline"]:
        lines += ["", f"## Deltas (`{baseline}` minus theirs)", "",
                  "| Source | " + " | ".join(BANDS) + " | Crest |",
                  "| --- |" + " --- |" * (len(BANDS) + 1)]
        by_source = {m["source"]: m for m in measurements}
        base_bands = by_source[baseline]["band_balance_db"]
        for source, delta in comparison["deltas_vs_baseline"].items():
            other_bands = by_source[source]["band_balance_db"]
            cells = " | ".join(
                _signed(delta["band_balance_db"][b], "")
                if band_is_audible(base_bands.get(b), other_bands.get(b)) else "n/a"
                for b in BANDS
            )
            lines.append(f"| {source} | {cells} | {_signed(delta['crest_factor_db'], '')} |")

        lines += ["", "## What to change", ""] + interpret(comparison)

    lines += [
        "",
        "## Caveat",
        "",
        "These numbers describe the signal, not the experience. A master can measure",
        "close to LANDR and still sound worse. The blind listening test is the",
        "decider; this report only tells you *where* to look.",
    ]
    return "\n".join(lines) + "\n"
