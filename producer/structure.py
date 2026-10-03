"""Song structure: what sections a track has, and where it is thin.

Turning a loop into a song is an arrangement problem, not a processing one. No
amount of mastering adds a bridge. This reads the arrangement that is already
there -- where sections start, which ones repeat, where the energy moves -- so
the gaps are visible.

Everything here is description, not judgement. It can say a track is two
minutes of one idea; it cannot say whether that idea is good.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np

from producer.audio import Track

# Roughly one section boundary per this many seconds, before merging.
SECONDS_PER_SECTION = 11.0
MIN_SECTIONS, MAX_SECTIONS = 4, 14

# Sections shorter than this are fragments, not sections.
MIN_SECTION_S = 4.0

# How many distinct section types to label (verse / chorus / bridge-ish).
MAX_LABELS = 5

# An energy jump of this much at a boundary reads as a drop or a lift.
DROP_DB = 3.0

# A beat drop in the producer's sense is not an energy dip -- it is the low end
# being pulled out and brought back. This is how far below the track's own
# typical low-end level a section has to sit to count.
LOW_END_DROP_DB = 4.0

# The intro and outro are meant to be quiet; flatness matters in the body.
BODY_FLAT_DB = 4.0

# Typical single length. Used only to say how far from it a track sits.
SINGLE_MIN_S, SINGLE_MAX_S = 150.0, 240.0


@dataclass
class Section:
    """One stretch of the song."""

    index: int
    label: str
    start_s: float
    end_s: float
    energy_db: float
    onset_rate: float
    brightness_hz: float
    low_energy_db: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        return {**asdict(self), "duration_s": round(self.duration_s, 2)}


@dataclass
class Structure:
    duration_s: float
    tempo_bpm: float
    key: str
    sections: list[Section]
    transitions: list[dict] = field(default_factory=list)
    repetition: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "duration_s": round(self.duration_s, 2),
            "tempo_bpm": round(self.tempo_bpm, 1),
            "key": self.key,
            "sections": [s.to_dict() for s in self.sections],
            "transitions": self.transitions,
            "repetition": self.repetition,
            "notes": self.notes,
        }


def _features(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Beat-synchronous timbre and harmony, which is what sections differ in."""
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    if len(beats) < MIN_SECTIONS * 4:
        beats = np.arange(0, len(y) // 512, 8)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    sync = np.vstack([
        librosa.util.sync(chroma, beats, aggregate=np.median),
        librosa.util.sync(mfcc, beats, aggregate=np.mean),
    ])
    return np.atleast_1d(tempo)[0], beats, librosa.util.normalize(sync, axis=0)


def _label_sections(features: np.ndarray, bounds: list[int], count: int) -> list[str]:
    """Group sections that sound alike, so repeats share a letter."""
    profiles = np.stack([
        features[:, a:b].mean(axis=1) for a, b in zip(bounds[:-1], bounds[1:])
    ])
    n_labels = min(count, len(profiles))
    if n_labels <= 1:
        return ["A"] * len(profiles)

    from sklearn.cluster import AgglomerativeClustering

    clustering = AgglomerativeClustering(n_clusters=n_labels).fit(profiles)
    # Name clusters in order of first appearance, so the intro is always A.
    order: dict[int, str] = {}
    for cluster in clustering.labels_:
        if cluster not in order:
            order[cluster] = chr(ord("A") + len(order))
    return [order[c] for c in clustering.labels_]


def analyze_structure(path: str | Path) -> Structure:
    """Segment a track and describe its arrangement."""
    track = Track.load(path)
    y = np.ascontiguousarray(track.mono(), dtype=np.float32)
    sr = track.sample_rate
    duration = track.duration_s

    tempo, beats, sync = _features(y, sr)

    target = int(np.clip(round(duration / SECONDS_PER_SECTION), MIN_SECTIONS, MAX_SECTIONS))
    target = min(target, sync.shape[1] - 1)
    bounds = list(librosa.segment.agglomerative(sync, target))
    bounds = sorted(set([0, *bounds, sync.shape[1]]))

    beat_times = librosa.frames_to_time(beats, sr=sr)

    def beat_to_seconds(index: int) -> float:
        if index >= len(beat_times):
            return duration
        return float(beat_times[min(index, len(beat_times) - 1)])

    # Merge fragments into their predecessor.
    times = [beat_to_seconds(b) for b in bounds]
    times[0], times[-1] = 0.0, duration
    merged = [times[0]]
    for t in times[1:-1]:
        if t - merged[-1] >= MIN_SECTION_S:
            merged.append(t)
    merged.append(duration)

    frame_bounds = [int(np.searchsorted(beat_times, t)) for t in merged]
    frame_bounds[0], frame_bounds[-1] = 0, sync.shape[1]
    frame_bounds = sorted(set(frame_bounds))
    labels = _label_sections(sync, frame_bounds, MAX_LABELS)

    from producer.library import estimate_key

    sections: list[Section] = []
    for i, (start, end) in enumerate(zip(merged[:-1], merged[1:])):
        seg = y[int(start * sr):int(end * sr)]
        if seg.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
        onsets = librosa.onset.onset_detect(y=seg, sr=sr, units="time")
        spectrum = np.abs(np.fft.rfft(seg))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
        power = np.square(spectrum, dtype=np.float64)
        low = power[freqs < 150].sum() / max(power.sum(), 1e-20)

        sections.append(Section(
            index=i,
            label=labels[i] if i < len(labels) else "?",
            start_s=round(start, 2),
            end_s=round(end, 2),
            energy_db=round(float(20 * np.log10(max(rms, 1e-9))), 2),
            onset_rate=round(len(onsets) / max(end - start, 1e-6), 2),
            brightness_hz=round(float(np.mean(
                librosa.feature.spectral_centroid(y=seg, sr=sr))), 0),
            low_energy_db=round(float(10 * np.log10(max(low, 1e-20))), 2),
        ))

    transitions = []
    for before, after in zip(sections[:-1], sections[1:]):
        delta = after.energy_db - before.energy_db
        if abs(delta) >= DROP_DB:
            transitions.append({
                "at_s": after.start_s,
                "from_label": before.label,
                "to_label": after.label,
                "energy_change_db": round(delta, 2),
                "kind": "lift" if delta > 0 else "drop",
            })

    repetition: dict[str, int] = {}
    for s in sections:
        repetition[s.label] = repetition.get(s.label, 0) + 1

    return Structure(
        duration_s=duration,
        tempo_bpm=float(tempo),
        key=estimate_key(y[: int(60 * sr)], sr),
        sections=sections,
        transitions=transitions,
        repetition=dict(sorted(repetition.items())),
        notes=_arrangement_notes(duration, sections, transitions, repetition),
    )


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _arrangement_notes(
    duration: float, sections: list[Section], transitions: list[dict], repetition: dict[str, int]
) -> list[str]:
    """Where the arrangement is thin, in terms that suggest what to add."""
    notes: list[str] = []

    if duration < SINGLE_MIN_S:
        short_by = SINGLE_MIN_S - duration
        notes.append(
            f"At {duration / 60:.1f} min this is about {short_by:.0f}s short of a "
            f"typical single ({SINGLE_MIN_S / 60:.1f}-{SINGLE_MAX_S / 60:.1f} min). "
            "Another section, or a repeat of the strongest one, closes the gap."
        )

    distinct = len(repetition)
    if distinct <= 2:
        notes.append(
            f"Only {distinct} distinct section type(s). A contrasting part — a bridge, "
            "a stripped verse, a beat switch — is what stops a track feeling like a loop."
        )

    once_only = [label for label, n in repetition.items() if n == 1]
    if len(once_only) == distinct and distinct > 2:
        notes.append(
            "No section repeats. Repetition is what makes a hook memorable; "
            "consider bringing the strongest part back."
        )

    if not transitions:
        notes.append(
            f"No energy change of {DROP_DB:.0f} dB or more between sections, so there is "
            "no drop or lift anywhere. Dropping the low end for a bar before the chorus "
            "makes its return land harder."
        )
    else:
        lifts = [t for t in transitions if t["kind"] == "lift"]
        if not lifts:
            notes.append("Energy only ever falls between sections — nothing builds.")

    # Judge flatness on the body: an intro and outro are supposed to be quiet,
    # and including them hides a middle that never moves.
    body = sections[1:-1] if len(sections) > 3 else sections
    if body:
        spread = max(s.energy_db for s in body) - min(s.energy_db for s in body)
        if spread < BODY_FLAT_DB:
            notes.append(
                f"After the intro the energy never moves more than {spread:.1f} dB — "
                f"every section from {_clock(body[0].start_s)} to {_clock(body[-1].end_s)} sits "
                "at the same level. However good it sounds, that reads as one long idea."
            )

    # The thing a listener calls a drop: the low end leaving, then returning.
    lows = [s.low_energy_db for s in sections]
    typical_low = float(np.median(lows))
    stripped = [s for s in sections if typical_low - s.low_energy_db >= LOW_END_DROP_DB]
    if not stripped:
        hook = max(repetition, key=lambda k: repetition[k])
        first_hook = next((s for s in sections if s.label == hook), None)
        where = _clock(first_hook.start_s) if first_hook else "the chorus"
        notes.append(
            f"No section pulls the low end out: every part sits within "
            f"{max(lows) - min(lows):.1f} dB of the same bass level, so nothing ever drops. "
            f"Filtering the bass and kick for a bar or two before **{hook}** returns "
            f"(first at {where}) is what makes its return land."
        )

    longest = max(sections, key=lambda s: s.duration_s, default=None)
    if longest is not None and longest.duration_s > duration * 0.4:
        notes.append(
            f"Section {longest.label} runs {longest.duration_s:.0f}s — "
            f"{longest.duration_s / duration * 100:.0f}% of the track. Breaking it up "
            "gives the ear somewhere to go."
        )

    return notes


def build_structure_report(structure: Structure, title: str = "Arrangement") -> str:
    """Markdown: the timeline, the transitions, and what is missing."""
    lines = [
        f"# {title}",
        "",
        f"{structure.duration_s / 60:.2f} min · {structure.tempo_bpm:.0f} BPM · {structure.key}",
        "",
        "| # | Section | Start | Length | Energy | Onsets/s | Low end | Brightness |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in structure.sections:
        lines.append(
            f"| {s.index + 1} | **{s.label}** | {int(s.start_s // 60)}:{int(s.start_s % 60):02d} "
            f"| {s.duration_s:.0f}s | {s.energy_db:.1f} dB | {s.onset_rate:.1f} "
            f"| {s.low_energy_db:.1f} dB | {s.brightness_hz:.0f} Hz |"
        )

    lines += ["", "## Repetition", "",
              " · ".join(f"**{k}** ×{v}" for k, v in structure.repetition.items()) or "—"]

    lines += ["", "## Transitions", ""]
    if structure.transitions:
        for t in structure.transitions:
            arrow = "▲" if t["kind"] == "lift" else "▼"
            lines.append(
                f"- {arrow} **{t['kind']}** at {int(t['at_s'] // 60)}:{int(t['at_s'] % 60):02d} — "
                f"{t['from_label']} → {t['to_label']}, {t['energy_change_db']:+.1f} dB"
            )
    else:
        lines.append("_No section-to-section energy change above the threshold._")

    lines += ["", "## What the arrangement is missing", ""]
    lines += [f"- {n}" for n in structure.notes] or ["- Nothing obvious."]

    half, double = structure.tempo_bpm / 2, structure.tempo_bpm * 2
    lines += [
        "",
        "## Finding compatible material",
        "",
        f"Search beats and samples on **{structure.tempo_bpm:.0f} BPM** "
        f"(or {half:.0f} / {double:.0f} — the same groove counted differently) "
        f"in **{structure.key}**.",
        "",
        "Nothing here queries a streaming catalogue. To rank candidates by how close",
        "they actually sit to this track, add them with `producer library add` and run",
        "`producer library match`, which compares tempo, brightness, spectral balance",
        "and dynamics.",
    ]
    lines += [
        "",
        "This describes the arrangement; it cannot tell you whether the ideas in it",
        "are good. That is still a listening question.",
    ]
    return "\n".join(lines) + "\n"
