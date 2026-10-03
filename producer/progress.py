"""How finished is a song, and what is actually stopping it.

The pipeline can say a master is compliant. That is not the same as saying a
song is done. A compliant master of a bare vocal is still a bare vocal.

This walks each track through the stages a release has to pass and reports
which are cleared, which are blocked, and by what. It is deliberately willing
to say a track is 40% done.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from producer.benchmark import measure
from producer.library import classify_track
from producer.standards import STANDARDS, evaluate
from producer.structure import analyze_structure

# A single is usually at least this long.
SINGLE_MIN_S = 150.0

# Below this many listeners a verdict is an anecdote.
LISTENERS_FOR_A_VERDICT = 3


@dataclass
class Stage:
    key: str
    label: str
    state: str          # done | partial | blocked | todo
    detail: str
    blocker: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrackProgress:
    slug: str
    kind: str
    kind_reason: str
    duration_s: float
    stages: list[Stage]
    measurements: dict = field(default_factory=dict)
    arrangement: dict = field(default_factory=dict)
    listening: dict | None = None
    next_step: str = ""

    @property
    def percent(self) -> int:
        if not self.stages:
            return 0
        score = sum({"done": 1.0, "partial": 0.5}.get(s.state, 0.0) for s in self.stages)
        return round(100 * score / len(self.stages))

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "kind": self.kind,
            "kind_reason": self.kind_reason,
            "duration_s": round(self.duration_s, 1),
            "percent": self.percent,
            "stages": [s.to_dict() for s in self.stages],
            "measurements": self.measurements,
            "arrangement": self.arrangement,
            "listening": self.listening,
            "next_step": self.next_step,
        }


def _read_listening(song_dir: Path) -> dict | None:
    """Scores for this track, if anyone has actually listened."""
    results = song_dir / "listening_results.md"
    if not results.exists():
        return None
    text = results.read_text()
    listeners = 0
    for line in text.splitlines():
        if line.startswith("1 listener") or " listener(s):" in line:
            head = line.split("listener")[0].strip()
            listeners = int(head) if head.isdigit() else 0
            break
    winner = ""
    for line in text.splitlines():
        if line.startswith("| ") and "|" in line and "Source" not in line and "---" not in line:
            winner = line.split("|")[1].strip()
            break
    return {"listeners": listeners, "preferred": winner}


def track_progress(song_dir: str | Path) -> TrackProgress:
    """Walk one track through the stages a release has to clear."""
    song_dir = Path(song_dir)
    source = song_dir / "original.wav"
    kind, why = classify_track(source)
    raw = measure(source)

    master = next(
        (p for p in (song_dir / "MASTER_extended.wav", song_dir / "MASTER.wav") if p.exists()),
        None,
    )
    extended = song_dir / "extended.wav"
    mixed = song_dir / "vocal_mixed.wav"
    # A vocal placed over a beat is no longer blocked on one.
    combined = song_dir / "with_beat.wav"
    beat_file = next(
        (p for p in song_dir.glob("beat.*")
         if p.suffix.lower() in {".wav", ".mp3", ".flac", ".aiff", ".m4a"}),
        None,
    )
    final = master or (extended if extended.exists() else
                       combined if combined.exists() else source)
    final_m = measure(final)

    stages: list[Stage] = [
        Stage("intake", "Raw material", "done",
              f"{raw['duration_s']:.0f}s, {raw['sample_rate'] / 1000:.0f} kHz, "
              f"{'mono' if raw['channels'] == 1 else 'stereo'}")
    ]

    # Vocal production only applies to a lone voice.
    if kind == "vocal-only":
        if mixed.exists():
            m = measure(mixed)
            stages.append(Stage(
                "vocal", "Vocal production", "done",
                f"chain applied — loudness range {raw['lra']} → {m['lra']} LU",
            ))
        else:
            stages.append(Stage("vocal", "Vocal production", "todo",
                                "no vocal chain run yet", "producer mix --params ..."))
        if combined.exists():
            stages.append(Stage(
                "backing", "Backing track", "done",
                f"vocal mixed over {beat_file.name if beat_file else 'a beat'}",
            ))
        elif beat_file is not None:
            stages.append(Stage(
                "backing", "Backing track", "partial",
                f"{beat_file.name} is here but the vocal is not over it yet",
                "producer combine --vocal ... --beat ...",
            ))
        else:
            stages.append(Stage(
                "backing", "Backing track", "blocked",
                "there is no instrumental under this vocal",
                "A beat has to be written, bought or licensed. Nothing here can "
                "generate one, and a vocal without music is not a song.",
            ))
    else:
        stages.append(Stage("backing", "Backing track", "done",
                            "arrived as a finished mix, vocal already over the beat"))

    if master is not None:
        conformance = evaluate(final_m, STANDARDS["spotify"])
        stages.append(Stage(
            "master", "Master", "done",
            f"{final_m['lufs']:.1f} LUFS @ {final_m['true_peak_dbtp']:+.2f} dBTP → "
            f"Spotify delivers {conformance.delivered_lufs:.1f}",
        ))
    else:
        stages.append(Stage("master", "Master", "todo", "not mastered",
                            "producer deliver --lufs -16"))

    structure = analyze_structure(final)
    long_enough = structure.duration_s >= SINGLE_MIN_S
    has_drop = any(t["kind"] in {"breakdown", "bass returns"} for t in structure.transitions)
    if long_enough and has_drop:
        arrangement_state, arrangement_detail = "done", (
            f"{structure.duration_s / 60:.2f} min, "
            f"{len(structure.repetition)} section types, with a breakdown"
        )
    elif long_enough or has_drop:
        arrangement_state, arrangement_detail = "partial", (
            f"{structure.duration_s / 60:.2f} min"
            + (", has a breakdown" if has_drop else ", no breakdown")
        )
    else:
        arrangement_state, arrangement_detail = "todo", (
            f"{structure.duration_s / 60:.2f} min, no breakdown"
        )
    stages.append(Stage(
        "arrangement", "Arrangement", arrangement_state, arrangement_detail,
        "" if arrangement_state == "done" else "producer arrange",
    ))

    listening = _read_listening(song_dir)
    if listening and listening["listeners"] >= LISTENERS_FOR_A_VERDICT:
        stages.append(Stage("judged", "Judged by ear", "done",
                            f"{listening['listeners']} listeners"))
    elif listening:
        stages.append(Stage(
            "judged", "Judged by ear", "partial",
            f"{listening['listeners']} listener — an anecdote, not a verdict",
            f"needs {LISTENERS_FOR_A_VERDICT - listening['listeners']} more",
        ))
    else:
        stages.append(Stage("judged", "Judged by ear", "todo", "nobody has listened",
                            "producer blindtest"))

    blocked = next((s for s in stages if s.state == "blocked"), None)
    todo = next((s for s in stages if s.state in {"todo", "partial"}), None)
    next_step = (blocked.blocker if blocked else (todo.blocker or todo.detail) if todo
                 else "Nothing outstanding.")

    return TrackProgress(
        slug=song_dir.name,
        kind=kind,
        kind_reason=why,
        duration_s=structure.duration_s,
        stages=stages,
        measurements={
            "raw": {k: raw[k] for k in ("lufs", "true_peak_dbtp", "crest_factor_db", "lra")},
            "final": {k: final_m[k] for k in ("lufs", "true_peak_dbtp", "crest_factor_db", "lra")},
            "final_file": final.name,
        },
        arrangement={
            "sections": len(structure.sections),
            "types": len(structure.repetition),
            "transitions": structure.transitions,
            "notes": structure.notes,
            "timeline": [
                {"label": s.label, "start_s": s.start_s, "duration_s": round(s.duration_s, 1),
                 "energy_db": s.energy_db, "low_energy_db": s.low_energy_db}
                for s in structure.sections
            ],
        },
        listening=listening,
        next_step=next_step,
    )


def build_comparison(workspace: str | Path) -> dict:
    """Every track in the workspace, side by side."""
    workspace = Path(workspace)
    tracks = [
        track_progress(p.parent) for p in sorted(workspace.glob("*/original.wav"))
    ]
    return {
        "tracks": [t.to_dict() for t in tracks],
        "stage_order": ["intake", "vocal", "backing", "master", "arrangement", "judged"],
    }
