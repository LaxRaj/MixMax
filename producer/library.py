"""A reference library built from real releases.

Two numbers in this pipeline were invented rather than measured: the QA
loudness window, and which reference track to master against. Both are
guesses dressed as defaults. This module replaces them with the measured
distribution of actual finished records.

What it learns is what commercial masters *measure* like — not what sounds
good. That is still a much stronger footing than a threshold someone typed
from memory, because it is grounded in records that shipped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np

from producer.audio import AUDIO_SUFFIXES, Track
from producer.benchmark import measure

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler key profiles: correlate a track's averaged chroma
# against all 24 rotations and take the best fit.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Only a kick and bass put real energy below 60 Hz. The `low` band (60-250 Hz)
# is where a *vocal's own fundamental* lives -- 85-255 Hz covers most singers --
# so judging on it marks every bare vocal as a full mix. Measured on fixtures:
# a bare vocal sits near -117 dB in `sub`, a full track near -4 dB.
#
# This matters because mastering a lone vocal toward a full-mix reference asks
# matchering to invent bass that was never recorded.
FULL_MIX = "full-mix"
VOCAL_ONLY = "vocal-only"
SUB_FULL_MIX_DB = -30.0

# Only analyse this much of each track. Enough to characterise it, and it keeps
# ingesting a few hundred songs to minutes rather than an afternoon.
ANALYSIS_SECONDS = 60.0


@dataclass
class LibraryEntry:
    """One reference track, as the library understands it."""

    path: str
    title: str
    genre: str
    kind: str
    tempo_bpm: float
    key: str
    duration_s: float
    lufs: float
    lra: float | None
    true_peak_dbtp: float
    crest_factor_db: float
    spectral_centroid_hz: float
    band_balance_db: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_key(y: np.ndarray, sr: int) -> str:
    """Best-fitting key, as a string like 'F# minor'."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    if chroma.size == 0:
        return "unknown"
    profile = chroma.mean(axis=1)
    if not np.any(profile):
        return "unknown"

    best, best_score = "unknown", -np.inf
    for index in range(12):
        for name, template in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            rotated = np.roll(template, index)
            score = float(np.corrcoef(profile, rotated)[0, 1])
            if np.isfinite(score) and score > best_score:
                best, best_score = f"{PITCH_CLASSES[index]} {name}", score
    return best


def classify_kind(band_balance: dict[str, float]) -> str:
    """Guess whether a track is a full mix or a bare vocal, from its sub energy."""
    sub = band_balance.get("sub", -120.0)
    return FULL_MIX if sub >= SUB_FULL_MIX_DB else VOCAL_ONLY


def analyze_reference(
    path: str | Path, genre: str = "unspecified", kind: str | None = None
) -> LibraryEntry:
    """Measure one reference track and describe it musically."""
    path = Path(path)
    metrics = measure(path)

    track = Track.load(path)
    mono = np.ascontiguousarray(track.mono(), dtype=np.float32)
    window = mono[: int(ANALYSIS_SECONDS * track.sample_rate)]

    tempo, _ = librosa.beat.beat_track(y=window, sr=track.sample_rate)
    tempo_bpm = float(np.atleast_1d(tempo)[0])

    return LibraryEntry(
        path=str(path.resolve()),
        title=path.stem,
        genre=genre,
        kind=kind or classify_kind(metrics["band_balance_db"]),
        tempo_bpm=round(tempo_bpm if np.isfinite(tempo_bpm) else 0.0, 1),
        key=estimate_key(window, track.sample_rate),
        duration_s=metrics["duration_s"],
        lufs=metrics["lufs"],
        lra=metrics["lra"],
        true_peak_dbtp=metrics["true_peak_dbtp"],
        crest_factor_db=metrics["crest_factor_db"],
        spectral_centroid_hz=metrics["spectral_centroid_hz"],
        band_balance_db=metrics["band_balance_db"],
    )


class Library:
    """A JSON-backed catalogue of reference tracks.

    Only measurements are stored. The audio stays wherever it already lives --
    nothing is copied into the repo.
    """

    def __init__(self, entries: list[LibraryEntry] | None = None) -> None:
        self.entries: list[LibraryEntry] = entries or []

    @classmethod
    def load(cls, path: str | Path) -> "Library":
        path = Path(path)
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text())
        return cls([LibraryEntry(**e) for e in payload.get("entries", [])])

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"entries": [e.to_dict() for e in self.entries]}, indent=2) + "\n"
        )
        return path

    def add(self, entry: LibraryEntry) -> None:
        """Add or replace by path, so re-ingesting a folder is idempotent."""
        self.entries = [e for e in self.entries if e.path != entry.path]
        self.entries.append(entry)

    def filter(self, genre: str | None = None, kind: str | None = None) -> list[LibraryEntry]:
        found = self.entries
        if genre:
            found = [e for e in found if e.genre.lower() == genre.lower()]
        if kind:
            found = [e for e in found if e.kind == kind]
        return found

    @property
    def genres(self) -> list[str]:
        return sorted({e.genre for e in self.entries})

    def __len__(self) -> int:
        return len(self.entries)


# Thresholds come from percentiles of the corpus, not round numbers. A record
# at the 5th percentile of commercial loudness is still a record.
LOUDNESS_PERCENTILES = (5, 95)
MIN_CORPUS_FOR_THRESHOLDS = 8

# Weights for picking a reference. Tempo and tonal character dominate: a
# reference far from the source makes matchering do violent things.
MATCH_WEIGHTS = {"tempo": 1.0, "centroid": 1.2, "band": 1.0, "crest": 0.6}


@dataclass
class Stats:
    """What finished records in this corpus actually measure like."""

    count: int
    genre: str | None
    lufs: dict
    lra: dict
    crest_factor_db: dict
    true_peak_dbtp: dict
    tempo_bpm: dict
    band_balance_db: dict[str, float]
    kinds: dict[str, int]
    keys: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


def _spread(values: list[float]) -> dict:
    clean = [float(v) for v in values if v is not None and np.isfinite(v)]
    if not clean:
        return {"n": 0}
    return {
        "n": len(clean),
        "min": round(float(np.min(clean)), 2),
        "p5": round(float(np.percentile(clean, 5)), 2),
        "median": round(float(np.median(clean)), 2),
        "p95": round(float(np.percentile(clean, 95)), 2),
        "max": round(float(np.max(clean)), 2),
    }


def summarize(entries: list[LibraryEntry], genre: str | None = None) -> Stats:
    """Aggregate a corpus into the profile of a finished record."""
    if not entries:
        raise ValueError("no reference tracks to summarise")

    bands: dict[str, float] = {}
    for band in entries[0].band_balance_db:
        values = [e.band_balance_db.get(band) for e in entries]
        clean = [v for v in values if v is not None and np.isfinite(v)]
        bands[band] = round(float(np.median(clean)), 2) if clean else float("-inf")

    keys: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for entry in entries:
        keys[entry.key] = keys.get(entry.key, 0) + 1
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1

    return Stats(
        count=len(entries),
        genre=genre,
        lufs=_spread([e.lufs for e in entries]),
        lra=_spread([e.lra for e in entries]),
        crest_factor_db=_spread([e.crest_factor_db for e in entries]),
        true_peak_dbtp=_spread([e.true_peak_dbtp for e in entries]),
        tempo_bpm=_spread([e.tempo_bpm for e in entries]),
        band_balance_db=bands,
        kinds=dict(sorted(kinds.items())),
        keys=dict(sorted(keys.items(), key=lambda kv: -kv[1])),
    )


def derive_thresholds(entries: list[LibraryEntry], genre: str | None = None) -> dict:
    """Turn a corpus into a QA profile.

    The shipped -16..-9 LUFS window was a guess. This replaces it with the
    range real records in *this* corpus actually occupy, so a master is judged
    against the company it will keep rather than a remembered number.
    """
    if len(entries) < MIN_CORPUS_FOR_THRESHOLDS:
        raise ValueError(
            f"need at least {MIN_CORPUS_FOR_THRESHOLDS} references to derive thresholds; "
            f"got {len(entries)}. A handful of tracks would just encode their quirks."
        )

    stats = summarize(entries, genre)
    low, high = LOUDNESS_PERCENTILES
    loud = [e.lufs for e in entries if np.isfinite(e.lufs)]

    lufs_min = round(float(np.percentile(loud, low)), 2)
    lufs_max = round(float(np.percentile(loud, high)), 2)

    # Trimming at p5/p95 means a few of the corpus fall outside the window it
    # produced. Reporting that keeps the gate honest: if most of the records it
    # learned from would fail it, the window is wrong, not the records.
    inside = sum(1 for v in loud if lufs_min <= v <= lufs_max)

    return {
        "genre": genre or "all",
        "derived_from": len(entries),
        "lufs_min": lufs_min,
        "lufs_max": lufs_max,
        "true_peak_max_dbtp": -1.0,
        "corpus_inside_window": inside,
        "corpus_pass_rate": round(inside / len(loud), 3) if loud else 0.0,
        "notes": (
            f"Derived from {len(entries)} reference tracks "
            f"(p{low}-p{high} of their integrated loudness). "
            "Describes what these records measure like, not what sounds good."
        ),
        "stats": stats.to_dict(),
    }


def _band_distance(a: dict[str, float], b: dict[str, float]) -> float:
    from producer.benchmark import band_is_audible

    shared = [k for k in a if k in b and band_is_audible(a[k], b[k])]
    if not shared:
        return 0.0
    return float(np.mean([abs(a[k] - b[k]) for k in shared]))


def match_reference(
    library: Library,
    vocal_metrics: dict,
    tempo_bpm: float | None = None,
    genre: str | None = None,
    kind: str | None = None,
) -> list[tuple[LibraryEntry, float]]:
    """Rank references by how little work mastering would have to do.

    Closest-in-character inside the target genre, rather than closest overall:
    a reference from the style you want is the point, but one wildly unlike the
    source makes matchering overcorrect.
    """
    candidates = library.filter(genre=genre, kind=kind)
    if not candidates:
        return []

    scored: list[tuple[LibraryEntry, float]] = []
    for entry in candidates:
        terms: list[float] = []

        if tempo_bpm and entry.tempo_bpm > 0:
            # An octave error (half/double time) should not count as distant.
            ratio = max(tempo_bpm, entry.tempo_bpm) / min(tempo_bpm, entry.tempo_bpm)
            folded = min(abs(ratio - 1.0), abs(ratio / 2 - 1.0), abs(ratio * 2 - 1.0))
            terms.append(MATCH_WEIGHTS["tempo"] * folded)

        ours_c = vocal_metrics.get("spectral_centroid_hz", 0.0)
        if ours_c > 0 and entry.spectral_centroid_hz > 0:
            terms.append(
                MATCH_WEIGHTS["centroid"]
                * float(abs(np.log2(ours_c / entry.spectral_centroid_hz)))
            )

        terms.append(
            MATCH_WEIGHTS["band"]
            * _band_distance(vocal_metrics.get("band_balance_db", {}), entry.band_balance_db)
            / 10.0
        )
        terms.append(
            MATCH_WEIGHTS["crest"]
            * abs(vocal_metrics.get("crest_factor_db", 0.0) - entry.crest_factor_db)
            / 10.0
        )

        scored.append((entry, float(sum(terms))))

    return sorted(scored, key=lambda pair: pair[1])


def discover_references(path: str | Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in AUDIO_SUFFIXES else []
    return sorted(
        p for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and not p.name.startswith(".")
    )
