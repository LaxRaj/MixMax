"""From a vocal to a finished song: spec, generate, fit, mix, master, QA.

Nothing here processes audio itself. It runs the stages that already exist, in
order, around the one new thing: a backing that was generated for this vocal.

A candidate that does not fit is dropped with its reasons, and a run where none
fits fails. Mixing a vocal over a backing in the wrong key and mastering the
result would produce a clean, loud, wrong song, and nothing downstream of here
would notice.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from producer.combine import combine
from producer.generate.base import Generator, GenRequest
from producer.generate.fit import fit_candidate
from producer.generate.registry import get_generator
from producer.generate.run import generate_candidates
from producer.generate.spec import analyze_spec
from producer.intake import BLOCKED, intake_one, slugify
from producer.mix import mix_vocal
from producer.qa import DEFAULT_PROFILE, QAProfile, run_qa
from producer.workspace import ORIGINAL

GENERATED_DIR = "generated"
SUMMARY_FILE = "summary.json"
SPEC_FILE = "spec.json"
FIT_FILE = "fit.json"
QA_FILE = "qa.json"
RAW_WITH_VOCAL = "vendor_raw_with_vocal.wav"

# File names shared with producer.song; repeated here because song imports this
# module to regenerate, and a song folder's layout is a contract either way.
VOCAL_MIXED = "vocal_mixed.wav"
BEAT = "beat.wav"
WITH_BEAT = "with_beat.wav"
MASTER = "MASTER.wav"

# The two versions the Phase A blind test compares.
VERSION_RAW = "vendor_raw_with_vocal"
VERSION_PIPELINE = "pipeline"

# A delivered master sits on its target, so QA's window has to reach it.
QA_BELOW_TARGET_LU = 1.0

Log = Callable[[str], None]


class IntakeBlocked(RuntimeError):
    """The vocal failed the intake gate. Nothing was generated or spent."""


class NoSurvivors(RuntimeError):
    """Every candidate was rejected by `fit`. Nothing was mixed or mastered."""

    def __init__(self, message: str, built: dict | None = None) -> None:
        super().__init__(message)
        self.built = built or {}


def candidate_dir(root: Path, index: int) -> Path:
    return Path(root) / f"cand_{index}"


def style_prompt(style: str, spec: dict) -> str:
    """The user's words first, then only what was measured."""
    from producer.generate.spec import VocalSpec

    hints = VocalSpec(**spec).to_prompt_hints()
    return f"{style.strip()}. {hints}" if hints else style.strip()


def build_candidates(
    vocal: Path,
    work_dir: Path,
    generator: Generator,
    style: str,
    spec: dict,
    *,
    n_candidates: int = 3,
    seed: int | None = None,
    lyrics: str | None = None,
    generated_dir: Path | None = None,
    log: Log = lambda _msg: None,
) -> dict:
    """Generate candidates and fit each into `work_dir/cand_<i>`.

    The raw candidates, `generation.json` and the ledger go in `generated_dir`
    (default `work_dir/generated`). Returns the generation record plus one row
    per candidate: its fit report, and whether it survived. Raises
    `NoSurvivors` if none did.
    """
    work_dir = Path(work_dir)
    generated_dir = Path(generated_dir) if generated_dir else work_dir / GENERATED_DIR
    req = GenRequest(
        vocal=vocal, style=style_prompt(style, spec), spec=spec, seed=seed,
        n_candidates=n_candidates, lyrics=lyrics,
    )
    log(f"generate {n_candidates} candidate(s) with {generator.name}")
    run = generate_candidates(generator, req, generated_dir)

    rows: list[dict] = []
    for index, result in enumerate(run["candidates"], start=1):
        cand = candidate_dir(work_dir, index)
        shutil.rmtree(cand, ignore_errors=True)
        cand.mkdir(parents=True)
        log(f"fit candidate {index}")
        fit = fit_candidate(vocal, result.path, spec, cand / BEAT)
        (cand / FIT_FILE).write_text(json.dumps(fit.to_dict(), indent=2) + "\n")
        rows.append({
            "index": index,
            "raw": str(result.path),
            "vendor": result.vendor,
            "model": result.model,
            "cost_usd": result.cost_usd,
            "generated": result.meta.get("generated", True),
            "passed_fit": fit.passed,
            "reasons": fit.reasons,
            "notes": fit.notes,
            "fit": fit.to_dict(),
        })

    record = {k: v for k, v in run.items() if k != "candidates"}
    built = {"generation": record, "candidates": rows}
    if not any(row["passed_fit"] for row in rows):
        raise NoSurvivors(_no_survivors_message(rows), built)
    return built


def _no_survivors_message(rows: list[dict]) -> str:
    lines = [f"None of the {len(rows)} candidate(s) fits this vocal:"]
    for row in rows:
        for reason in row["reasons"]:
            lines.append(f"  candidate {row['index']}: {reason}")
    return "\n".join(lines)


def _qa_profile(target_lufs: float) -> QAProfile:
    return QAProfile(
        lufs_min=min(DEFAULT_PROFILE.lufs_min, target_lufs - QA_BELOW_TARGET_LU),
        lufs_max=DEFAULT_PROFILE.lufs_max,
        source=f"delivery target {target_lufs:g} LUFS",
    )


def render_candidate(
    row: dict, song_dir: Path, work_dir: Path, target_lufs: float, log: Log
) -> None:
    """Mix, master and QA one surviving candidate; also the untouched comparison."""
    from producer.song import deliver_master

    cand = candidate_dir(work_dir, row["index"])
    log(f"mix and master candidate {row['index']}")
    # The offset comes from fit, which only ever reports one it trusts.
    combine(song_dir / VOCAL_MIXED, cand / BEAT, cand / WITH_BEAT,
            offset_s=row["fit"]["offset_s"])
    deliver_master(cand / WITH_BEAT, cand / MASTER, target_lufs, scratch=cand)
    row["qa"] = run_qa(cand / MASTER, _qa_profile(target_lufs))
    (cand / QA_FILE).write_text(json.dumps(row["qa"], indent=2) + "\n")
    row["master"] = str(cand / MASTER)

    # What the blind test compares against: the vocal as recorded over the
    # backing as generated. Same loudness gap, so the comparison is about what
    # fit, the vocal chain, ducking and mastering add, not about balance.
    combine(song_dir / ORIGINAL, row["raw"], cand / RAW_WITH_VOCAL, offset_s=0.0, duck_db=0.0)
    row["raw_with_vocal"] = str(cand / RAW_WITH_VOCAL)


def choose(rows: list[dict]) -> dict:
    """The first candidate that fits and passes QA; failing that, the first that fits."""
    survivors = [row for row in rows if row["passed_fit"]]
    return next((row for row in survivors if row.get("qa", {}).get("pass")), survivors[0])


def phase_a_instructions(labels: list[str], target: float) -> str:
    joined = ", ".join(labels)
    return f"""# Listening test

Thanks — this takes about five minutes.

In `listen/` there are {len(labels)} versions of the same song: **{joined}**.
Same vocal, same backing track, finished differently. They are volume-matched
to {target:.1f} LUFS so you are judging the sound, not which one is loudest,
and the names are deliberately meaningless.

## Two questions

1. **Would you release this?** For each version, a score from 1 (no) to 5
   (yes, as it is). Judge the whole song — the backing track as much as the
   voice. Goes in `release_ready_1_5`.
2. **Which sounds more finished?** Put 1 against that one in `rank`, 2 against
   the other. No ties.

Use headphones or real speakers if you can. Fill in `listener` with your name
on every row, and use `notes` for anything you noticed — does the backing fit
the singing, is it in time, does anything clash. Send the CSV back.

## Please don't

Don't try to guess which is which, and don't compare notes with anyone before
sending it back.
"""


def song_from_vocal(
    vocal: str | Path,
    style: str,
    workspace: str | Path,
    *,
    backend: str = "fake",
    generator: Generator | None = None,
    n_candidates: int = 3,
    seed: int | None = None,
    lyrics: str | None = None,
    language: str | None = None,
    slug: str | None = None,
    target_lufs: float = -16.0,
    blind_dir: str | Path | None = None,
    log: Log = lambda _msg: None,
) -> dict:
    """Run the whole chain on one vocal. Returns the contents of `summary.json`.

    Raises `IntakeBlocked` before anything is spent, and `NoSurvivors` (after
    writing the summary) when no candidate fits.
    """
    from producer.blindtest import build_blind_test
    from producer.song import save_settings, save_text

    vocal, workspace = Path(vocal), Path(workspace)
    generator = generator or get_generator(backend)

    intake = intake_one(vocal, workspace, slug or slugify(vocal.stem))
    if intake.verdict == BLOCKED:
        raise IntakeBlocked(f"{vocal.name} cannot be used: " + " ".join(intake.blockers))
    song_dir = workspace / intake.slug
    original = song_dir / ORIGINAL

    log("measure the vocal")
    spec = analyze_spec(original, lyrics, language).to_dict()
    (song_dir / SPEC_FILE).write_text(json.dumps(spec, indent=2) + "\n")

    summary: dict = {
        "slug": intake.slug,
        "vocal": str(vocal),
        "style": style,
        "backend": generator.name,
        "target_lufs": target_lufs,
        "intake": {"verdict": intake.verdict, "warnings": intake.warnings},
        "spec": spec,
    }

    def write_summary() -> None:
        (song_dir / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, default=str) + "\n")

    try:
        built = build_candidates(
            original, song_dir, generator, style, spec,
            n_candidates=n_candidates, seed=seed, lyrics=lyrics, log=log,
        )
    except NoSurvivors as exc:
        built = exc.built
        summary.update(
            cost_usd=built["generation"]["total_cost_usd"], generation=built["generation"],
            candidates=built["candidates"], chosen=None, survivors=0,
            rejected=[{"index": r["index"], "reasons": r["reasons"]} for r in built["candidates"]],
        )
        write_summary()
        raise

    rows = built["candidates"]
    log("vocal chain")
    mix_vocal(original, song_dir / VOCAL_MIXED)
    for row in rows:
        if row["passed_fit"]:
            render_candidate(row, song_dir, song_dir, target_lufs, log)

    chosen = choose(rows)
    cand = candidate_dir(song_dir, chosen["index"])
    for name in (BEAT, WITH_BEAT, MASTER):
        shutil.copyfile(cand / name, song_dir / name)

    # Record what produced this song, so the studio's rebuild reproduces it.
    values = {"target_lufs": target_lufs}
    if chosen["fit"]["offset_s"]:
        values["offset_s"] = chosen["fit"]["offset_s"]
    save_settings(song_dir, values)
    save_text(song_dir, {"style": style, "backend": generator.name})

    summary.update(
        cost_usd=built["generation"]["total_cost_usd"],
        generation=built["generation"],
        candidates=rows,
        survivors=sum(1 for r in rows if r["passed_fit"]),
        rejected=[{"index": r["index"], "reasons": r["reasons"]}
                  for r in rows if not r["passed_fit"]],
        chosen=chosen["index"],
        chosen_qa_pass=bool(chosen["qa"]["pass"]),
        master=str(song_dir / MASTER),
    )

    if blind_dir is not None:
        log("blind test")
        blind = build_blind_test(
            {VERSION_RAW: Path(chosen["raw_with_vocal"]), VERSION_PIPELINE: song_dir / MASTER},
            Path(blind_dir) / intake.slug, seed=seed, instructions=phase_a_instructions,
        )
        summary["blind_test"] = {
            "share": str(blind["out_dir"]),
            "key": str(blind["key_path"]),
            "target_lufs": blind["target_lufs"],
        }

    write_summary()
    return summary


def regenerate(
    song_dir: Path, scratch: Path, text: dict, log: Log = lambda _msg: None
) -> tuple[dict[str, Path], list[Path], float]:
    """Generate and fit a fresh backing for an existing song.

    Used by `song.rebuild` when a generation setting changes. Returns the files
    to move into the song folder (keyed by path relative to it), files there
    that the new backing makes stale, and the offset the new backing mixes at.

    `generated/` is written directly and always holds the latest attempt, a
    failed one included: money spent on a run that then fits nothing was still
    spent, and the ledger and the rejected candidates are the record of it.
    The song's own audio only changes if this returns.
    """
    song_dir, scratch = Path(song_dir), Path(scratch)
    spec_path = song_dir / SPEC_FILE
    spec = (json.loads(spec_path.read_text()) if spec_path.exists()
            else analyze_spec(song_dir / ORIGINAL).to_dict())

    previous = {}
    record = song_dir / GENERATED_DIR / "generation.json"
    if record.exists():
        previous = json.loads(record.read_text()).get("request", {})

    generator = get_generator(text["backend"])
    built = build_candidates(
        song_dir / ORIGINAL, scratch, generator, text["style"], spec,
        n_candidates=int(previous.get("n_candidates") or 3),
        seed=previous.get("seed"), lyrics=previous.get("lyrics"),
        generated_dir=song_dir / GENERATED_DIR, log=log,
    )
    rows = built["candidates"]
    chosen = choose(rows)

    fresh: dict[str, Path] = {}
    stale: list[Path] = []
    for row in rows:
        name = candidate_dir(Path(""), row["index"]).name
        for filename in (BEAT, FIT_FILE):
            if (scratch / name / filename).exists():
                fresh[f"{name}/{filename}"] = scratch / name / filename
        # These were rendered over the backing this one replaces.
        stale += [song_dir / name / f for f in (WITH_BEAT, MASTER, RAW_WITH_VOCAL, QA_FILE)]
        if not row["passed_fit"]:
            stale.append(song_dir / name / BEAT)

    shutil.copyfile(candidate_dir(scratch, chosen["index"]) / BEAT, scratch / BEAT)
    fresh[BEAT] = scratch / BEAT

    summary_path = song_dir / SUMMARY_FILE
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"slug": song_dir.name}
    summary.update(
        style=text["style"], backend=generator.name, spec=spec,
        cost_usd=built["generation"]["total_cost_usd"], generation=built["generation"],
        candidates=rows, survivors=sum(1 for r in rows if r["passed_fit"]),
        rejected=[{"index": r["index"], "reasons": r["reasons"]}
                  for r in rows if not r["passed_fit"]],
        chosen=chosen["index"], regenerated=True,
    )
    # A blind test built on the old backing no longer describes this song.
    for key in ("blind_test", "chosen_qa_pass"):
        summary.pop(key, None)
    (scratch / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, default=str) + "\n")
    fresh[SUMMARY_FILE] = scratch / SUMMARY_FILE
    return fresh, stale, float(chosen["fit"]["offset_s"])
