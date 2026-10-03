"""Published loudness standards, and what each one does to a master.

Every streaming platform normalises playback loudness. That single fact makes
most loudness-chasing pointless: a master louder than the target is simply
turned down, and the limiting that bought the loudness is discarded — the
dynamic range it cost is not given back. This module makes that concrete for a
specific file.

The figures below are published platform and broadcast targets, not opinions.
They do drift, so each carries `source` and `as_of`, and the whole table can be
replaced from JSON (`--standards`) without touching code. Verify against the
current spec before trusting a number for a release.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

# Below this, a difference in delivered loudness is not worth acting on.
AUDIBLE_LUFS_TOLERANCE = 0.5

# Giving up this much crest factor for loudness a platform discards is worth
# calling out explicitly.
WASTED_LIMITING_DB = 1.0


@dataclass(frozen=True)
class Standard:
    """One published target, plus how that platform actually behaves."""

    key: str
    name: str
    target_lufs: float
    max_true_peak_dbtp: float
    normalizes_down: bool = True
    normalizes_up: bool = False
    tolerance_lu: float = 1.0
    notes: str = ""
    source: str = ""
    as_of: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Built-in table. Streaming targets cluster at -14 LUFS; broadcast is far
# quieter at -23. True peak is -1 dBTP almost everywhere, because lossy
# encoding overshoots and a master at 0 dBFS distorts after transcoding.
BUILT_IN: tuple[Standard, ...] = (
    Standard(
        key="spotify", name="Spotify", target_lufs=-14.0, max_true_peak_dbtp=-1.0,
        normalizes_down=True, normalizes_up=True,
        notes="Turns quiet tracks up too, limiting if that would breach true peak. "
              "A master louder than -14 is simply attenuated on playback.",
        source="Spotify Loudness Normalization docs", as_of="2024",
    ),
    Standard(
        key="apple_music", name="Apple Music (Sound Check)", target_lufs=-16.0,
        max_true_peak_dbtp=-1.0, normalizes_down=True, normalizes_up=True,
        notes="Sound Check is on by default on most devices.",
        source="Apple Digital Masters guidance", as_of="2024",
    ),
    Standard(
        key="youtube", name="YouTube", target_lufs=-14.0, max_true_peak_dbtp=-1.0,
        normalizes_down=True, normalizes_up=False,
        notes="Only attenuates. A master quieter than the target stays quiet and "
              "will sound weak next to everything else.",
        source="YouTube playback loudness", as_of="2024",
    ),
    Standard(
        key="amazon_music", name="Amazon Music", target_lufs=-14.0, max_true_peak_dbtp=-2.0,
        normalizes_down=True, normalizes_up=True, source="Amazon Music mastering guidance",
        as_of="2024",
    ),
    Standard(
        key="tidal", name="Tidal", target_lufs=-14.0, max_true_peak_dbtp=-1.0,
        normalizes_down=True, normalizes_up=True, source="Tidal loudness normalization",
        as_of="2024",
    ),
    Standard(
        key="deezer", name="Deezer", target_lufs=-15.0, max_true_peak_dbtp=-1.0,
        normalizes_down=True, normalizes_up=True, source="Deezer loudness normalization",
        as_of="2024",
    ),
    Standard(
        key="aes_streaming", name="AES streaming recommendation", target_lufs=-16.0,
        max_true_peak_dbtp=-1.0, tolerance_lu=2.0,
        notes="AES TD1004 recommends -16 to -20 LUFS for streaming delivery.",
        source="AES TD1004", as_of="2021",
    ),
    Standard(
        key="ebu_r128", name="EBU R128 (broadcast)", target_lufs=-23.0,
        max_true_peak_dbtp=-1.0, tolerance_lu=0.5,
        notes="Broadcast, not streaming. -23 LUFS +/-0.5 LU.",
        source="EBU R128", as_of="2020",
    ),
)

STANDARDS: dict[str, Standard] = {s.key: s for s in BUILT_IN}


def load_standards(path: str | Path | None = None) -> dict[str, Standard]:
    """Built-in table, or a JSON file replacing it.

    Targets drift; this is the seam that lets you correct one without a code
    change. The file is a list of objects matching `Standard`'s fields.
    """
    if path is None:
        return dict(STANDARDS)
    payload = json.loads(Path(path).read_text())
    rows = payload["standards"] if isinstance(payload, dict) else payload
    fields = Standard.__dataclass_fields__
    return {
        row["key"]: Standard(**{k: v for k, v in row.items() if k in fields})
        for row in rows
    }


@dataclass
class Conformance:
    """What a given standard does to one master."""

    standard: str
    target_lufs: float
    measured_lufs: float
    normalization_gain_db: float
    delivered_lufs: float
    true_peak_dbtp: float
    true_peak_after_norm_dbtp: float
    conforms: bool
    issues: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(measurement: dict, standard: Standard) -> Conformance:
    """Work out what `standard` does to this master on playback."""
    lufs = float(measurement["lufs"])
    true_peak = float(measurement["true_peak_dbtp"])

    if not np.isfinite(lufs):
        return Conformance(
            standard=standard.key, target_lufs=standard.target_lufs, measured_lufs=lufs,
            normalization_gain_db=0.0, delivered_lufs=lufs, true_peak_dbtp=true_peak,
            true_peak_after_norm_dbtp=true_peak, conforms=False,
            issues=["loudness could not be measured"],
        )

    # Platforms differ in whether they only turn down, or also turn up.
    wanted = standard.target_lufs - lufs
    if wanted < 0:
        gain = wanted if standard.normalizes_down else 0.0
    else:
        gain = wanted if standard.normalizes_up else 0.0

    delivered = lufs + gain
    peak_after = true_peak + gain

    issues: list[str] = []
    consequences: list[str] = []

    if true_peak > standard.max_true_peak_dbtp:
        issues.append(
            f"true peak {true_peak:+.2f} dBTP exceeds {standard.max_true_peak_dbtp:.0f} dBTP"
        )
        consequences.append(
            "Inter-sample overs survive the WAV and distort once the platform "
            "re-encodes to a lossy format. Pull the master down or use a "
            "true-peak limiter."
        )

    if abs(delivered - standard.target_lufs) > standard.tolerance_lu:
        issues.append(
            f"delivered {delivered:.1f} LUFS vs target {standard.target_lufs:.0f} "
            f"(+/-{standard.tolerance_lu:.1f} LU)"
        )

    # The important one: loudness a platform discards.
    if gain < -WASTED_LIMITING_DB:
        crest = measurement.get("crest_factor_db")
        detail = f" Crest factor is {crest:.1f} dB." if crest is not None else ""
        consequences.append(
            f"{standard.name} attenuates this by {gain:.1f} dB on playback, so it "
            f"lands at {delivered:.1f} LUFS regardless. Any limiting done to reach "
            f"{lufs:.1f} LUFS buys nothing here — it only costs dynamic range.{detail}"
        )
    elif gain > WASTED_LIMITING_DB:
        consequences.append(
            f"{standard.name} raises this by {gain:+.1f} dB, taking true peak to "
            f"{peak_after:+.2f} dBTP."
        )
    elif wanted > standard.tolerance_lu and not standard.normalizes_up:
        consequences.append(
            f"{standard.name} does not turn quiet tracks up, so this plays "
            f"{wanted:.1f} LU below everything around it."
        )

    if peak_after > standard.max_true_peak_dbtp and gain > 0:
        issues.append(f"true peak reaches {peak_after:+.2f} dBTP after normalization")

    return Conformance(
        standard=standard.key,
        target_lufs=standard.target_lufs,
        measured_lufs=round(lufs, 2),
        normalization_gain_db=round(gain, 2),
        delivered_lufs=round(delivered, 2),
        true_peak_dbtp=round(true_peak, 2),
        true_peak_after_norm_dbtp=round(peak_after, 2),
        conforms=not issues,
        issues=issues,
        consequences=consequences,
    )


def evaluate_all(measurement: dict, standards: dict[str, Standard] | None = None) -> list[Conformance]:
    table = standards if standards is not None else STANDARDS
    return [evaluate(measurement, s) for s in table.values()]


def to_qa_profile(standard: Standard) -> dict:
    """A QA profile that accepts masters this standard will deliver cleanly."""
    return {
        "genre": f"standard:{standard.key}",
        "derived_from": 0,
        "lufs_min": round(standard.target_lufs - standard.tolerance_lu, 2),
        "lufs_max": round(standard.target_lufs + standard.tolerance_lu, 2),
        "true_peak_max_dbtp": standard.max_true_peak_dbtp,
        "notes": (
            f"{standard.name}: {standard.target_lufs:.0f} LUFS "
            f"+/-{standard.tolerance_lu:.1f} LU, true peak <= "
            f"{standard.max_true_peak_dbtp:.0f} dBTP. "
            f"Source: {standard.source or 'unspecified'} ({standard.as_of or 'undated'}). "
            "Verify against the current published spec before a release."
        ),
    }


def summarize_conformance(
    measurement: dict, results: list[Conformance], standards: dict[str, Standard]
) -> list[str]:
    """Collapse per-standard findings into what is true about the master.

    "This gets turned down" is one fact about the file, not one per platform.
    Repeating it for every target buries the point it is making.
    """
    if not results:
        return []

    notes: list[str] = []
    lufs = float(measurement["lufs"])
    crest = measurement.get("crest_factor_db")

    attenuated = [r for r in results if r.normalization_gain_db < -WASTED_LIMITING_DB]
    if attenuated:
        gains = [r.normalization_gain_db for r in attenuated]
        delivered = [r.delivered_lufs for r in attenuated]
        names = [standards[r.standard].name for r in attenuated]
        scope = (
            "Every target here"
            if len(attenuated) == len(results)
            else f"{len(attenuated)} of {len(results)} targets ({', '.join(names[:3])}"
            + ("…)" if len(names) > 3 else ")")
        )
        headroom = abs(max(gains))
        notes.append(
            f"{scope} attenuates this, by {abs(max(gains)):.1f} to {abs(min(gains)):.1f} dB. "
            f"It plays back at {max(delivered):.1f} to {min(delivered):.1f} LUFS whatever you "
            f"do, so the limiting that reached {lufs:.1f} LUFS buys nothing on playback."
        )
        if crest is not None:
            notes.append(
                f"Crest factor is {crest:.1f} dB. Mastering around "
                f"{max(r.target_lufs for r in attenuated):.0f} LUFS instead would give back "
                f"roughly {headroom:.1f} dB of headroom at no cost in delivered loudness."
            )

    raised = [r for r in results if r.normalization_gain_db > WASTED_LIMITING_DB]
    if raised:
        worst = max(raised, key=lambda r: r.true_peak_after_norm_dbtp)
        notes.append(
            f"{len(raised)} target(s) raise this on playback; the largest takes true peak to "
            f"{worst.true_peak_after_norm_dbtp:+.2f} dBTP."
        )

    stuck = [
        standards[r.standard].name
        for r in results
        if r.normalization_gain_db == 0.0
        and r.target_lufs - r.measured_lufs > standards[r.standard].tolerance_lu
    ]
    if stuck:
        notes.append(
            f"{', '.join(stuck)} do not turn quiet tracks up, so this plays noticeably "
            "below everything around it there."
        )

    peak_breaches = {standards[r.standard].name for r in results if any("true peak" in i for i in r.issues)}
    if peak_breaches:
        notes.append(
            f"True peak is over the ceiling for {', '.join(sorted(peak_breaches))}. "
            "Inter-sample overs survive the WAV and distort after lossy encoding."
        )

    return notes
