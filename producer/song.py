"""One song as the studio sees it: its components, its settings, and a rebuild.

The CLI commands each do one stage and leave it to the operator to remember
which settings produced which file. The studio web app lets someone else turn a
knob, so that memory has to live somewhere: `settings.json`, beside the audio.

Nothing here invents processing. `rebuild` only re-runs the stage functions the
CLI already uses -- `mix_vocal`, `combine`, the body of `deliver`, `arrange` --
in order, from the first stage a change touches.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from producer.arrange import (
    DEFAULT_BRIDGE_BARS,
    BarGrid,
    plan_extension,
    render_arrangement,
)
from producer.combine import DEFAULT_DUCK_DB, DEFAULT_VOCAL_OVER_BEAT_DB, combine
from producer.library import classify_track
from producer.loudness import LoudnessResult, normalize_to_target
from producer.master_chain import DEFAULT_MASTER_PARAMS, master_full_mix
from producer.mastering import master_track
from producer.mix import DEFAULT_PARAMS, ChainParams, mix_vocal
from producer.standards import delivery_ceiling, load_standards
from producer.structure import analyze_structure
from producer.workspace import ORIGINAL, find_track_reference

SETTINGS_FILE = "settings.json"
LEGACY_CHAIN_FILE = "chain_params.json"

VOCAL_MIXED = "vocal_mixed.wav"
BEAT = "beat.wav"
WITH_BEAT = "with_beat.wav"
MASTER = "MASTER.wav"
EXTENDED = "extended.wav"
MASTER_EXTENDED = "MASTER_extended.wav"

VOCAL_ONLY = "vocal-only"

# Signal order. A change to one group re-renders it and everything after it.
GROUPS = ("generate", "vocal", "balance", "master", "arrangement")

GROUP_COPY = {
    "generate": ("Backing track", "The generated backing the vocal sits on."),
    "vocal": ("Vocal chain", "Cleans and shapes the raw vocal before anything is added."),
    "balance": ("Vocal against the beat", "How the vocal sits over the instrumental."),
    "master": ("Master", "The loudness the finished master is delivered at."),
    "arrangement": ("Arrangement", "The extended cut: how long, and how long the breakdown runs."),
}


@dataclass(frozen=True)
class Field:
    """One knob: where it lives, what it may be set to, and what it does."""

    key: str
    group: str
    label: str
    unit: str
    minimum: float
    maximum: float
    step: float
    default: float
    help: str
    kind: str = "number"    # number | toggle

    def to_dict(self) -> dict:
        return {
            "key": self.key, "group": self.group, "label": self.label, "unit": self.unit,
            "min": self.minimum, "max": self.maximum, "step": self.step,
            "default": self.default, "help": self.help, "kind": self.kind,
        }


_CHAIN = DEFAULT_PARAMS

# The ranges are guard rails against a typo, not taste. They are wide enough to
# make a bad-sounding choice and narrow enough to stop a nonsensical one.
FIELDS: tuple[Field, ...] = (
    Field("highpass_hz", "vocal", "Low cut", "Hz", 40, 200, 5, _CHAIN.highpass_hz,
          "Removes rumble below the voice. Higher thins the vocal."),
    Field("gate_threshold_db", "vocal", "Gate threshold", "dB", -70, -20, 1, _CHAIN.gate_threshold_db,
          "Silences the gaps between phrases. Higher cuts more, and can clip word endings."),
    Field("comp_threshold_db", "vocal", "Compressor threshold", "dB", -40, 0, 1, _CHAIN.comp_threshold_db,
          "Where levelling starts. Lower evens the performance out more."),
    Field("comp_ratio", "vocal", "Compressor ratio", ":1", 1, 10, 0.1, _CHAIN.comp_ratio,
          "How hard loud words are held back."),
    Field("deess_hz", "vocal", "De-ess frequency", "Hz", 3000, 10000, 100, _CHAIN.deess_hz,
          "Where the harsh S sounds sit for this voice."),
    Field("deess_gain_db", "vocal", "De-ess amount", "dB", -12, 0, 0.5, _CHAIN.deess_gain_db,
          "How much the S band is cut. More negative is duller."),
    Field("deess_q", "vocal", "De-ess width", "Q", 0.5, 6, 0.1, _CHAIN.deess_q,
          "Higher is a narrower cut."),
    Field("reverb_room", "vocal", "Reverb room", "", 0, 1, 0.01, _CHAIN.reverb_room,
          "Size of the space around the voice."),
    Field("reverb_wet", "vocal", "Reverb amount", "", 0, 0.5, 0.005, _CHAIN.reverb_wet,
          "How much of that space is heard."),
    Field("vocal_over_beat_db", "balance", "Vocal above the beat", "LU", -3, 12, 0.5,
          DEFAULT_VOCAL_OVER_BEAT_DB, "Higher puts the vocal further in front of the beat."),
    Field("duck_db", "balance", "Beat ducking", "dB", 0, 9, 0.5, DEFAULT_DUCK_DB,
          "How far the beat steps back while the vocal is present. 0 turns it off."),
    Field("offset_s", "balance", "Vocal start offset", "s", -30, 30, 0.01, 0.0,
          "Seconds the vocal starts after the beat. Negative starts it earlier."),
    Field("target_lufs", "master", "Loudness", "LUFS", -24, -8, 0.5, -16.0,
          "Quieter keeps more dynamics; streaming platforms lift it to their own level."),
    Field("use_reference", "master", "Match the reference track", "", 0, 1, 1, 0.0,
          "Shape the tone toward the reference track, if the song has one.", kind="toggle"),
    Field("minutes", "arrangement", "Target length", "min", 1.5, 6, 0.1, 2.8,
          "Roughly how long the extended cut should run."),
    Field("bridge_bars", "arrangement", "Breakdown length", "bars", 4, 16, 1, DEFAULT_BRIDGE_BARS,
          "How many bars the low end drops out for."),
)

FIELD_BY_KEY = {f.key: f for f in FIELDS}


@dataclass(frozen=True)
class TextField:
    """A setting that is words, not a number.

    `Field` is a range with a step, which is the wrong shape for a style
    description, so these are kept apart: stored under `"text"` in
    settings.json, validated by `validate_text`, and never handed to the
    numeric knobs or the code that reads them.
    """

    key: str
    group: str
    label: str
    help: str
    max_length: int = 400

    def to_dict(self) -> dict:
        return {"key": self.key, "group": self.group, "label": self.label,
                "help": self.help, "max_length": self.max_length, "kind": "text"}


TEXT_FIELDS: tuple[TextField, ...] = (
    TextField("style", "generate", "Style",
              "What the backing should sound like, in words. Changing it generates a new "
              "backing, which costs money."),
    TextField("backend", "generate", "Generator",
              "Which generation backend makes the backing.", max_length=40),
)

TEXT_FIELD_BY_KEY = {f.key: f for f in TEXT_FIELDS}


class SettingsError(ValueError):
    """A requested change that must not be applied."""


class RebuildError(RuntimeError):
    """A stage could not run. Nothing in the song folder was changed."""


# ── settings ────────────────────────────────────────────────────────────────


def load_recorded(song_dir: str | Path) -> dict:
    """Only the values someone actually chose, as written on disk.

    A legacy `chain_params.json` counts: it is the record of the vocal chain
    that produced `vocal_mixed.wav` before settings.json existed.
    """
    song_dir = Path(song_dir)
    recorded: dict = {}
    legacy = song_dir / LEGACY_CHAIN_FILE
    if legacy.exists():
        recorded.update({
            k: v for k, v in json.loads(legacy.read_text()).items()
            if k in FIELD_BY_KEY and FIELD_BY_KEY[k].group == "vocal"
        })
    path = song_dir / SETTINGS_FILE
    if path.exists():
        data = json.loads(path.read_text())
        recorded.update({k: v for k, v in data.get("values", {}).items() if k in FIELD_BY_KEY})
    return recorded


def load_title(song_dir: str | Path) -> str | None:
    path = Path(song_dir) / SETTINGS_FILE
    if path.exists():
        return json.loads(path.read_text()).get("title")
    return None


def effective(song_dir: str | Path) -> dict:
    """Every field's value: the recorded one, or the default when none was."""
    recorded = load_recorded(song_dir)
    return {f.key: recorded.get(f.key, f.default) for f in FIELDS}


def save_settings(song_dir: str | Path, values: dict, title: str | None = None) -> Path:
    """Record chosen values. Defaults nobody chose are left out on purpose."""
    song_dir = Path(song_dir)
    path = song_dir / SETTINGS_FILE
    existing = json.loads(path.read_text()) if path.exists() else {}
    payload = {
        "title": title or existing.get("title"),
        "values": {k: values[k] for k in sorted(values) if k in FIELD_BY_KEY},
    }
    if payload["title"] is None:
        del payload["title"]
    if existing.get("text"):
        payload["text"] = existing["text"]
    song_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def load_text(song_dir: str | Path) -> dict:
    """The text settings recorded for a song. Empty if it was never generated."""
    path = Path(song_dir) / SETTINGS_FILE
    if not path.exists():
        return {}
    text = json.loads(path.read_text()).get("text", {})
    return {k: v for k, v in text.items() if k in TEXT_FIELD_BY_KEY}


def save_text(song_dir: str | Path, text: dict) -> Path:
    """Record text settings, leaving the numeric ones and the title as they are."""
    song_dir = Path(song_dir)
    path = song_dir / SETTINGS_FILE
    payload = json.loads(path.read_text()) if path.exists() else {"values": {}}
    merged = {**payload.get("text", {}), **{k: v for k, v in text.items() if k in TEXT_FIELD_BY_KEY}}
    payload["text"] = {k: merged[k] for k in sorted(merged)}
    song_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def validate_text(changes: dict) -> dict:
    """Check requested text settings. Raises on any problem, like `validate_changes`."""
    if not isinstance(changes, dict) or not changes:
        raise SettingsError("The request changes nothing.")
    problems: list[str] = []
    clean: dict = {}
    for key, value in changes.items():
        field = TEXT_FIELD_BY_KEY.get(key)
        if field is None:
            problems.append(f"`{key}` is not a text setting.")
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field.label} must be some text.")
            continue
        value = value.strip()
        if len(value) > field.max_length:
            problems.append(f"{field.label} must be {field.max_length} characters or fewer; "
                            f"got {len(value)}.")
            continue
        clean[key] = value
    if "backend" in clean:
        from producer.generate.registry import registered

        if clean["backend"] not in registered():
            problems.append(f"Generator must be one of: {', '.join(registered())}.")
    if problems:
        raise SettingsError(" ".join(problems))
    return clean


def validate_changes(changes: dict) -> dict:
    """Check a requested change against each field's range. Raises on any problem.

    Everything is checked before anything is applied, so a request is taken
    whole or not at all.
    """
    if not isinstance(changes, dict) or not changes:
        raise SettingsError("The request changes nothing.")
    problems: list[str] = []
    clean: dict = {}
    for key, value in changes.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            problems.append(f"`{key}` is not a setting.")
            continue
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        if not isinstance(value, (int, float)) or value != value:   # NaN never equals itself
            problems.append(f"{field.label} must be a number.")
            continue
        if not field.minimum <= value <= field.maximum:
            problems.append(
                f"{field.label} must be between {field.minimum:g} and {field.maximum:g}"
                f"{' ' + field.unit if field.unit else ''}; got {value:g}."
            )
            continue
        clean[key] = float(value)
    if problems:
        raise SettingsError(" ".join(problems))
    return clean


def groups_touched(changes: dict) -> list[str]:
    """Which stages a change affects, in signal order."""
    touched = {FIELD_BY_KEY[k].group for k in changes if k in FIELD_BY_KEY}
    touched |= {TEXT_FIELD_BY_KEY[k].group for k in changes if k in TEXT_FIELD_BY_KEY}
    return [g for g in GROUPS if g in touched]


def apply_text(
    song_dir: str | Path, changes: dict, log: Callable[[str], None] = lambda _msg: None
) -> list[str]:
    """Change text settings and re-render from the stage they touch.

    The settings are only recorded once the rebuild has succeeded, so a failed
    regeneration leaves the song describing the backing it still has.
    """
    clean = validate_text(changes)
    groups = groups_touched(clean)
    for group in groups:
        applies, why_not = group_applies(song_dir, group)
        if not applies:
            raise SettingsError(why_not)
    rendered = rebuild(song_dir, groups[0], text=clean, log=log)
    save_text(song_dir, clean)
    return rendered


# ── what a song is made of ──────────────────────────────────────────────────


def song_kind(song_dir: str | Path) -> str:
    return classify_track(Path(song_dir) / ORIGINAL)[0]


def is_generated(song_dir: str | Path) -> bool:
    """Whether this song's beat came from `song-from-vocal` rather than an upload."""
    return bool(load_text(song_dir).get("style")) and (Path(song_dir) / "generated").is_dir()


def group_applies(song_dir: str | Path, group: str, kind: str | None = None) -> tuple[bool, str]:
    """Whether a settings group means anything for this song, and why not."""
    song_dir = Path(song_dir)
    if group == "generate":
        # Regenerating replaces beat.wav, so it must never reach a beat someone
        # wrote, bought or uploaded.
        if not is_generated(song_dir):
            return False, ("This song's backing was not generated here, "
                           "so there is nothing to regenerate.")
        return True, ""
    kind = kind or song_kind(song_dir)
    if group == "vocal" and kind != VOCAL_ONLY:
        return False, "This arrived as a finished mix, so there is no separate vocal to process."
    if group == "balance":
        if kind != VOCAL_ONLY:
            return False, "This arrived as a finished mix; the vocal is already over the beat."
        if not (song_dir / BEAT).exists():
            return False, "There is no beat yet. Upload one and the balance can be set."
    return True, ""


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    label: str
    filename: str            # the file in the song folder; reference is resolved separately
    about: str
    replace_as: str = ""     # upload kind that replaces it; empty = produced by the pipeline


def component_specs(kind: str) -> list[ComponentSpec]:
    """The pieces of a song, in the order they are made."""
    raw = (
        ComponentSpec("raw", "Raw vocal", ORIGINAL, "The take as it was recorded.", "vocal")
        if kind == VOCAL_ONLY else
        ComponentSpec("raw", "Original mix", ORIGINAL, "The mix as it arrived.", "vocal")
    )
    return [
        raw,
        ComponentSpec("vocal_mixed", "Vocal after the chain", VOCAL_MIXED,
                      "The vocal cleaned, levelled and de-essed."),
        ComponentSpec("beat", "Beat", BEAT, "The instrumental under the vocal.", "beat"),
        ComponentSpec("with_beat", "Vocal over the beat", WITH_BEAT,
                      "The mix before mastering."),
        ComponentSpec("master", "Master", MASTER, "The finished master."),
        ComponentSpec("extended", "Extended cut, unmastered", EXTENDED,
                      "The arrangement with a breakdown, before mastering."),
        ComponentSpec("master_extended", "Extended master", MASTER_EXTENDED,
                      "The full-length song, mastered."),
        ComponentSpec("reference", "Reference track", "reference.*",
                      "A released song whose tone this one is aimed at.", "reference"),
    ]


def component_path(song_dir: str | Path, spec: ComponentSpec) -> Path | None:
    song_dir = Path(song_dir)
    if spec.key == "reference":
        return find_track_reference(song_dir)
    path = song_dir / spec.filename
    return path if path.exists() else None


def final_component(song_dir: str | Path) -> str:
    """The furthest-along version: what "the song" means right now."""
    song_dir = Path(song_dir)
    for key, name in (("master_extended", MASTER_EXTENDED), ("master", MASTER),
                      ("extended", EXTENDED), ("with_beat", WITH_BEAT)):
        if (song_dir / name).exists():
            return key
    return "raw"


# ── stages ──────────────────────────────────────────────────────────────────


def deliver_master(
    source: str | Path,
    out_path: str | Path,
    target_lufs: float = -16.0,
    standard_key: str = "spotify",
    reference: str | Path | None = None,
    chain: bool = True,
    scratch: str | Path | None = None,
) -> tuple[LoudnessResult, dict | None]:
    """Master a mix to a chosen loudness, leaving headroom for the platform to lift.

    The body of `producer deliver`, as a function, so the CLI and the studio
    rebuild run the same thing.
    """
    import tempfile

    table = load_standards()
    if standard_key not in table:
        raise ValueError(f"Unknown standard {standard_key!r}. Available: {', '.join(table)}.")
    ceiling = delivery_ceiling(target_lufs, table[standard_key])

    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        staged = Path(tmp) / "staged.wav"
        info = None
        if chain:
            info = master_full_mix(source, staged, DEFAULT_MASTER_PARAMS)
        else:
            shutil.copyfile(source, staged)

        if reference is not None:
            matched = Path(tmp) / "matched.wav"
            master_track(staged, reference, matched)
            staged = matched

        return normalize_to_target(staged, out_path, target_lufs, ceiling), info


def _arrange(source: Path, out_path: Path, minutes: float, bridge_bars: float) -> None:
    structure = analyze_structure(source)
    grid = BarGrid.from_audio(source)
    try:
        arrangement, _why = plan_extension(
            structure, grid, target_duration_s=minutes * 60.0, bridge_bars=bridge_bars
        )
    except ValueError as exc:
        raise RebuildError(f"Could not arrange: {exc}.") from exc
    render_arrangement(source, arrangement, out_path)


def rebuild(
    song_dir: str | Path,
    start: str,
    values: dict | None = None,
    force: tuple[str, ...] | list[str] = (),
    log: Callable[[str], None] = lambda _msg: None,
    text: dict | None = None,
) -> list[str]:
    """Re-render from `start` onwards. Returns the files that were rewritten.

    A stage runs when it is at or after `start` and either its output already
    exists or it is named in `force`. So changing the vocal chain refreshes the
    mix and the master that were built on it, but does not conjure an extended
    cut nobody had asked for.

    Everything is rendered into a scratch folder and only moved into place once
    every stage has succeeded. A failure leaves the song exactly as it was.

    Starting at `generate` asks the generator for a new backing, using the
    song's text settings overridden by `text`. That is the one stage that costs
    money, and its attempts are recorded under `generated/` whether or not the
    rebuild then succeeds.
    """
    song_dir = Path(song_dir)
    if start not in GROUPS:
        raise RebuildError(f"Unknown stage {start!r}.")
    source = song_dir / ORIGINAL
    if not source.exists():
        raise RebuildError(f"{song_dir.name} has no {ORIGINAL}.")

    values = {**effective(song_dir), **(values or {})}
    kind = song_kind(song_dir)
    first = GROUPS.index(start)

    def wanted(group: str, output: str) -> bool:
        return GROUPS.index(group) >= first and (group in force or (song_dir / output).exists())

    scratch = song_dir / ".render"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    fresh: dict[str, Path] = {}
    extras: dict[str, Path] = {}      # per-candidate records that follow a new beat
    stale: list[Path] = []
    new_offset: float | None = None

    def current(name: str) -> Path | None:
        """The newest version of a file: this run's, else what is already there."""
        if name in fresh:
            return fresh[name]
        path = song_dir / name
        return path if path.exists() else None

    try:
        if first == 0:
            if not is_generated(song_dir):
                raise RebuildError(group_applies(song_dir, "generate")[1])
            log("generate a new backing")
            from producer.generate.pipeline import regenerate

            extras, stale, new_offset = regenerate(
                song_dir, scratch, {**load_text(song_dir), **(text or {})}, log)
            fresh[BEAT] = extras.pop(BEAT)
            # The offset belongs to the beat it was measured against.
            values["offset_s"] = new_offset

        if kind == VOCAL_ONLY and wanted("vocal", VOCAL_MIXED):
            log("vocal chain")
            params = ChainParams.from_dict(values)
            fresh[VOCAL_MIXED] = mix_vocal(source, scratch / VOCAL_MIXED, params)

        voice = current(VOCAL_MIXED) or source
        beat = current(BEAT)
        if kind == VOCAL_ONLY and beat is not None and wanted("balance", WITH_BEAT):
            log("vocal over the beat")
            combine(
                voice, beat, scratch / WITH_BEAT,
                offset_s=values["offset_s"],
                vocal_over_beat_db=values["vocal_over_beat_db"],
                duck_db=values["duck_db"],
            )
            fresh[WITH_BEAT] = scratch / WITH_BEAT

        mix = current(WITH_BEAT) or voice
        reference = find_track_reference(song_dir) if values["use_reference"] >= 0.5 else None

        if wanted("master", MASTER):
            log("master")
            deliver_master(mix, scratch / MASTER, values["target_lufs"],
                           reference=reference, scratch=scratch)
            fresh[MASTER] = scratch / MASTER

        if wanted("arrangement", EXTENDED):
            log("arrangement")
            _arrange(mix, scratch / EXTENDED, values["minutes"], values["bridge_bars"])
            fresh[EXTENDED] = scratch / EXTENDED

        extended = current(EXTENDED)
        # The extended master follows the extended cut: if either the cut or
        # the mastering changed, it is out of date.
        if extended is not None and (EXTENDED in fresh or wanted("master", MASTER_EXTENDED)):
            log("master the extended cut")
            deliver_master(extended, scratch / MASTER_EXTENDED, values["target_lufs"],
                           reference=reference, scratch=scratch)
            fresh[MASTER_EXTENDED] = scratch / MASTER_EXTENDED

        for name, path in {**fresh, **extras}.items():
            (song_dir / name).parent.mkdir(parents=True, exist_ok=True)
            Path(path).replace(song_dir / name)
        for path in stale:
            path.unlink(missing_ok=True)
        if new_offset is not None:
            recorded = {**load_recorded(song_dir), "offset_s": new_offset}
            if not new_offset:
                recorded.pop("offset_s")
            save_settings(song_dir, recorded)
        return list(fresh)
    except RebuildError:
        raise
    except Exception as exc:  # a stage blew up; report it rather than half-apply
        raise RebuildError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
