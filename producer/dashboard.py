"""Collect the whole pipeline's state into one JSON document.

The page that renders this is read-only. The CLI stays the engine, so there is
no second implementation of any number here -- everything is read back from the
modules that produced it.

The organising idea is the *evidence ledger*: every tunable number carries
where it came from. Three times in this project a figure that looked
authoritative turned out to be someone's guess, so provenance is the thing
worth putting on screen.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from producer.benchmark import measure
from producer.library import Library, summarize
from producer.mix import DEFAULT_PARAMS, ChainParams
from producer.qa import DEFAULT_PROFILE, QAProfile
from producer.standards import load_standards, summarize_conformance, evaluate_all
from producer.workspace import ORIGINAL, OURS, find_track_reference, song_dirs

# Where a number came from, worst to best.
GUESSED = "guessed"      # a default nobody has checked against anything
REPORTED = "reported"    # widely reported, but not confirmed at a primary source
FITTED = "fitted"        # producer tune, against a measured target
MEASURED = "measured"    # derived from the reference corpus
PUBLISHED = "published"  # a platform or standards body's own document

# Anything but a bare guess counts as standing on something.
GROUNDED = (PUBLISHED, MEASURED, FITTED, REPORTED)


@dataclass
class Evidence:
    """One tunable number, and what backs it."""

    name: str
    value: str
    provenance: str
    detail: str
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _qa_evidence(profile: QAProfile, profile_path: Path | None) -> list[Evidence]:
    is_default = (
        profile.lufs_min == DEFAULT_PROFILE.lufs_min
        and profile.lufs_max == DEFAULT_PROFILE.lufs_max
    )
    if is_default and profile_path is None:
        return [Evidence(
            name="QA loudness window",
            value=f"{profile.lufs_min:.1f} … {profile.lufs_max:.1f} LUFS",
            provenance=GUESSED,
            detail=(
                "Hand-picked, never checked against anything. A corpus of real "
                "releases put the comparable window near -22 … -13, so this one "
                "fails records that actually shipped."
            ),
            source="producer/qa.py defaults",
        )]
    return [Evidence(
        name="QA loudness window",
        value=f"{profile.lufs_min:.1f} … {profile.lufs_max:.1f} LUFS",
        provenance=PUBLISHED if "standard:" in profile.source else MEASURED,
        detail=f"From {profile.source}.",
        source=str(profile_path) if profile_path else profile.source,
    )]


def _chain_evidence(params: ChainParams, params_path: Path | None) -> list[Evidence]:
    fitted = params_path is not None
    rows: list[Evidence] = []
    labels = {
        "highpass_hz": ("Highpass cutoff", "Hz"),
        "gate_threshold_db": ("Noise gate", "dB"),
        "comp_threshold_db": ("Compressor threshold", "dB"),
        "comp_ratio": ("Compressor ratio", ":1"),
        "deess_hz": ("De-ess frequency", "Hz"),
        "deess_gain_db": ("De-ess cut", "dB"),
        "reverb_wet": ("Reverb wet", ""),
    }
    for field, (label, unit) in labels.items():
        value = getattr(params, field)
        default = getattr(DEFAULT_PARAMS, field)
        changed = abs(value - default) > 1e-6
        rows.append(Evidence(
            name=label,
            value=f"{value:g}{unit}",
            provenance=FITTED if (fitted and changed) else GUESSED,
            detail=(
                f"Fitted by `producer tune` (default was {default:g})."
                if fitted and changed
                else "A conservative starting value, chosen by hand and never tested by ear."
            ),
            source=str(params_path) if fitted else "producer/mix.py defaults",
        ))
    return rows


def _standards_evidence() -> list[Evidence]:
    rows = []
    for s in load_standards().values():
        rows.append(Evidence(
            name=s.name,
            value=f"{s.target_lufs:.0f} LUFS / {s.max_true_peak_dbtp:.0f} dBTP",
            provenance=PUBLISHED if s.confidence == "verified" else REPORTED,
            detail=(
                s.notes or "Published playback target."
                if s.confidence == "verified"
                else (s.notes or "") + " Widely reported, not confirmed at a primary source."
            ),
            source=f"{s.source} ({s.as_of})",
        ))
    return rows


def _library_evidence(library: Library) -> list[Evidence]:
    if not len(library):
        return [Evidence(
            name="Reference corpus",
            value="empty",
            provenance=GUESSED,
            detail=(
                "No real releases ingested, so every threshold below falls back "
                "to a hand-picked default. `producer library add` fixes this."
            ),
            source="",
        )]
    stats = summarize(library.entries)
    return [Evidence(
        name="Reference corpus",
        value=f"{len(library)} tracks",
        provenance=MEASURED,
        detail=(
            f"Median {stats.lufs['median']:.1f} LUFS, "
            f"p5–p95 {stats.lufs['p5']:.1f} … {stats.lufs['p95']:.1f}. "
            f"Genres: {', '.join(library.genres)}."
        ),
        source="reference_library.json",
    )]


def _track_state(song_dir: Path, standards_table: dict) -> dict:
    """What exists for one track, and what the platforms would do to our master."""
    versions = sorted(
        p.stem for p in song_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".flac"}
        and p.stem.lower() != "reference"
    )
    ours = song_dir / OURS
    entry: dict = {
        "slug": song_dir.name,
        "versions": versions,
        "has_ours": ours.exists(),
        "has_reference": find_track_reference(song_dir) is not None,
        "competitors": [v for v in versions if v not in {"original", "producer"}],
    }

    if ours.exists():
        metrics = measure(ours)
        results = evaluate_all(metrics, standards_table)
        entry["measurement"] = {
            k: metrics[k] for k in
            ("lufs", "lra", "true_peak_dbtp", "crest_factor_db", "spectral_centroid_hz")
        }
        entry["band_balance_db"] = metrics["band_balance_db"]
        entry["conformance"] = [r.to_dict() for r in results]
        entry["consequences"] = summarize_conformance(metrics, results, standards_table)
    return entry


def build_dashboard(
    workspace: str | Path | None = None,
    library_path: str | Path | None = None,
    qa_profile_path: str | Path | None = None,
    chain_params_path: str | Path | None = None,
    results_path: str | Path | None = None,
) -> dict:
    """Gather every stage's state into one document."""
    standards_table = load_standards()

    library = Library.load(library_path) if library_path else Library()
    profile = QAProfile.load(qa_profile_path) if qa_profile_path else DEFAULT_PROFILE
    params = ChainParams.load(chain_params_path) if chain_params_path else DEFAULT_PARAMS

    tracks: list[dict] = []
    if workspace and Path(workspace).exists():
        tracks = [_track_state(d, standards_table) for d in song_dirs(workspace)]

    listening = None
    if results_path and Path(results_path).exists():
        listening = json.loads(Path(results_path).read_text())

    rendered = [t for t in tracks if t["has_ours"]]
    with_competitor = [t for t in tracks if t["competitors"]]

    ledger = (
        _library_evidence(library)
        + _qa_evidence(profile, Path(qa_profile_path) if qa_profile_path else None)
        + _chain_evidence(params, Path(chain_params_path) if chain_params_path else None)
        + _standards_evidence()
    )

    return {
        "stages": [
            {"key": "intake", "label": "Intake",
             "done": len(tracks), "total": len(tracks),
             "blurb": "Raw vocals validated and standardised."},
            {"key": "render", "label": "Our master",
             "done": len(rendered), "total": len(tracks),
             "blurb": "Mix chain, then reference-based mastering."},
            {"key": "benchmark", "label": "Benchmarked",
             "done": len(with_competitor), "total": len(tracks),
             "blurb": "A commercial master to compare against."},
            {"key": "listening", "label": "Blind listening",
             "done": (listening or {}).get("listeners", 0) and len(listening["listeners"]) or 0,
             "total": 3,
             "blurb": "The only thing that answers whether it sounds good."},
        ],
        "tracks": tracks,
        "evidence": [e.to_dict() for e in ledger],
        "evidence_summary": _summarize_evidence(ledger),
        "library": summarize(library.entries).to_dict() if len(library) else None,
        "listening": listening,
    }


def _summarize_evidence(ledger: list[Evidence]) -> dict:
    counts: dict[str, int] = {}
    for row in ledger:
        counts[row.provenance] = counts.get(row.provenance, 0) + 1
    total = max(len(ledger), 1)
    return {
        "counts": counts,
        "total": len(ledger),
        "grounded_pct": round(100 * sum(counts.get(k, 0) for k in GROUNDED) / total),
    }
