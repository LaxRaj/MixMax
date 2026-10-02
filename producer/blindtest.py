"""Blind, loudness-matched listening tests.

Two biases destroy an informal mastering shootout:

1. **Loudness bias** — the louder of two otherwise identical masters wins,
   every time. Our masters run hot (~-7 LUFS) and LANDR targets ~-14, so an
   unmatched comparison measures gain staging, not quality.
2. **Brand bias** — a listener told "this one is LANDR" rates it higher.

So: every version is gain-matched to a common LUFS target, renamed to an
opaque label, and shuffled. The mapping lives in a key file written *outside*
the folder you share.
"""

from __future__ import annotations

import csv
import json
import random
import string
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm as pyln

from producer.audio import Track

# Headroom left after gain-matching, so no version clips on the way down.
TRUE_PEAK_CEILING_DBFS = -1.0


@dataclass
class BlindVersion:
    label: str
    source: str
    original_lufs: float
    applied_gain_db: float


def _integrated_lufs(track: Track) -> float:
    meter = pyln.Meter(track.sample_rate)
    if track.duration_s < meter.block_size:
        return float("-inf")
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(meter.integrated_loudness(track.samples))


def choose_target_lufs(loudness: dict[str, float]) -> float:
    """Match downward, never upward.

    Boosting a quiet master to meet a loud one risks clipping and changes its
    character; attenuating is transparent. So the target is the quietest
    version in the set.
    """
    measurable = [v for v in loudness.values() if np.isfinite(v)]
    if not measurable:
        raise ValueError("no version was long or loud enough to measure")
    return float(min(measurable))


def _apply_gain(track: Track, gain_db: float) -> np.ndarray:
    scaled = track.samples.astype(np.float32) * float(10.0 ** (gain_db / 20.0))
    peak = float(np.max(np.abs(scaled))) if scaled.size else 0.0
    ceiling = 10.0 ** (TRUE_PEAK_CEILING_DBFS / 20.0)
    if peak > ceiling:
        # Should be rare, since we only ever attenuate — but never ship a clip.
        scaled = scaled * (ceiling / peak)
    return scaled.astype(np.float32)


def _labels(n: int) -> list[str]:
    return list(string.ascii_uppercase[:n])


def build_blind_test(
    versions: dict[str, Path],
    out_dir: str | Path,
    key_path: str | Path | None = None,
    target_lufs: float | None = None,
    seed: int | None = None,
) -> dict:
    """Render a loudness-matched, anonymized, shuffled listening set."""
    if len(versions) < 2:
        raise ValueError("a blind test needs at least two versions to compare")

    out_dir = Path(out_dir)
    listen_dir = out_dir / "listen"
    listen_dir.mkdir(parents=True, exist_ok=True)
    key_path = Path(key_path) if key_path else out_dir.parent / f"{out_dir.name}.key.json"

    tracks = {name: Track.load(path) for name, path in versions.items()}
    loudness = {name: _integrated_lufs(t) for name, t in tracks.items()}
    target = float(target_lufs) if target_lufs is not None else choose_target_lufs(loudness)

    sources = list(versions)
    random.Random(seed).shuffle(sources)

    assigned: list[BlindVersion] = []
    for label, source in zip(_labels(len(sources)), sources):
        track = tracks[source]
        measured = loudness[source]
        gain_db = float(target - measured) if np.isfinite(measured) else 0.0
        Track(
            path=track.path,
            samples=_apply_gain(track, gain_db),
            sample_rate=track.sample_rate,
        ).write(listen_dir / f"{label}.wav")
        assigned.append(
            BlindVersion(
                label=label,
                source=source,
                original_lufs=round(measured, 2) if np.isfinite(measured) else measured,
                applied_gain_db=round(gain_db, 2),
            )
        )

    key = {
        "target_lufs": round(target, 2),
        "seed": seed,
        "mapping": {v.label: v.source for v in assigned},
        "versions": [vars(v) for v in assigned],
    }
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(key, indent=2) + "\n")

    _write_scoresheet(out_dir / "SCORESHEET.csv", [v.label for v in assigned])
    (out_dir / "INSTRUCTIONS.md").write_text(_instructions([v.label for v in assigned], target))

    return {"key_path": key_path, "listen_dir": listen_dir, "out_dir": out_dir, **key}


def _write_scoresheet(path: Path, labels: list[str]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["listener", "label", "release_ready_1_5", "rank", "notes"])
        for label in labels:
            writer.writerow(["", label, "", "", ""])


def _instructions(labels: list[str], target: float) -> str:
    joined = ", ".join(labels)
    return f"""# Listening test

Thanks — this takes about five minutes.

In `listen/` there are {len(labels)} versions of the same track: **{joined}**.
They are the same performance, processed differently. They have been
volume-matched to {target:.1f} LUFS so you're judging the *sound*, not which one
is loudest, and the names are deliberately meaningless so nothing biases you.

## What to do

1. Listen to all {len(labels)} once, start to finish, before scoring anything.
2. Use headphones or real speakers if you can — laptop speakers hide most of
   what we're testing.
3. Fill in `SCORESHEET.csv`:
   - `listener` — your name, same on every row.
   - `release_ready_1_5` — would you hear this on a release? 1 = no, 5 = yes.
   - `rank` — 1 for your favourite, 2 for next, and so on. No ties.
   - `notes` — anything you noticed: harsh, muddy, thin, boxy, over-compressed.
     Short is fine. This is the most useful column.
4. Send the CSV back.

## Please don't

Don't try to guess which is which, and don't discuss it with the others before
sending it back — that's the whole point of the labels.
"""


def tally(key_path: str | Path, responses: str | Path) -> dict:
    """Un-blind the responses and aggregate them by source."""
    key = json.loads(Path(key_path).read_text())
    mapping: dict[str, str] = key["mapping"]

    responses = Path(responses)
    files = sorted(responses.glob("*.csv")) if responses.is_dir() else [responses]

    rows: list[dict] = []
    for path in files:
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                label = (row.get("label") or "").strip().upper()
                if label not in mapping:
                    continue
                rows.append({
                    "listener": (row.get("listener") or path.stem).strip() or path.stem,
                    "source": mapping[label],
                    "label": label,
                    "score": _number(row.get("release_ready_1_5")),
                    "rank": _number(row.get("rank")),
                    "notes": (row.get("notes") or "").strip(),
                })

    by_source: dict[str, dict] = {}
    for source in mapping.values():
        theirs = [r for r in rows if r["source"] == source]
        scores = [r["score"] for r in theirs if r["score"] is not None]
        ranks = [r["rank"] for r in theirs if r["rank"] is not None]
        by_source[source] = {
            "n_responses": len(theirs),
            "mean_score": round(float(np.mean(scores)), 2) if scores else None,
            "mean_rank": round(float(np.mean(ranks)), 2) if ranks else None,
            "wins": sum(1 for r in ranks if r == 1),
            "notes": [r["notes"] for r in theirs if r["notes"]],
        }

    listeners = sorted({r["listener"] for r in rows})
    return {"key": key, "listeners": listeners, "rows": rows, "by_source": by_source}


def _number(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def build_tally_report(result: dict) -> str:
    """Markdown summary of the un-blinded listening results."""
    by_source = result["by_source"]
    listeners = result["listeners"]

    ranked = sorted(
        by_source.items(),
        key=lambda kv: (kv[1]["mean_rank"] is None, kv[1]["mean_rank"]),
    )

    lines = [
        "# Listening test results",
        "",
        f"{len(listeners)} listener(s): {', '.join(listeners) if listeners else '—'}",
        f"Loudness-matched to {result['key']['target_lufs']} LUFS.",
        "",
        "| Source | Mean release-ready (1–5) | Mean rank | 1st-place votes | Responses |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source, stats in ranked:
        lines.append(
            "| {src} | {score} | {rank} | {wins} | {n} |".format(
                src=source,
                score="—" if stats["mean_score"] is None else f"{stats['mean_score']:.2f}",
                rank="—" if stats["mean_rank"] is None else f"{stats['mean_rank']:.2f}",
                wins=stats["wins"],
                n=stats["n_responses"],
            )
        )

    lines += ["", "## Blind labels", "",
              "| Label | Source |", "| --- | --- |"]
    for label, source in result["key"]["mapping"].items():
        lines.append(f"| {label} | {source} |")

    lines += ["", "## What listeners said", ""]
    for source, stats in ranked:
        if not stats["notes"]:
            continue
        lines.append(f"### {source}")
        lines += [f"- {note}" for note in stats["notes"]]
        lines.append("")

    if len(listeners) < 3:
        lines += [
            "> **Caution:** fewer than three listeners. Treat this as an anecdote,",
            "> not a verdict.",
            "",
        ]

    return "\n".join(lines) + "\n"
