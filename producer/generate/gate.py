"""The Phase A gate, as arithmetic.

The thresholds were written into ROADMAP.md before any listening result
existed. They live here as constants so the decision is computed from the
tallies rather than argued from them afterwards.

On 5 real vocals, with at least 3 listeners each, a vocal passes when the
median "would you release this" score for the full pipeline is 4 or more out
of 5 and more listeners rank the pipeline above the vendor's raw output than
the other way round. Three or more passing vocals pass the gate; fewer than
two fail it; exactly two earns one more cycle.
"""

from __future__ import annotations

from statistics import median

from producer.generate.pipeline import VERSION_PIPELINE, VERSION_RAW

REQUIRED_VOCALS = 5
MIN_LISTENERS = 3
RELEASE_SCORE_MIN = 4.0
PASS_AT = 3          # vocals passing, of REQUIRED_VOCALS
FAIL_BELOW = 2

PASS, FAIL, RETRY, INCOMPLETE = "pass", "fail", "retry", "incomplete"


def judge_vocal(name: str, tally_result: dict) -> dict:
    """Apply the per-vocal rule to one un-blinded tally (from `blindtest.tally`)."""
    rows = tally_result["rows"]
    by_listener: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_listener.setdefault(row["listener"], {})[row["source"]] = row

    scores = [r["score"] for r in rows if r["source"] == VERSION_PIPELINE and r["score"] is not None]
    prefer_pipeline = prefer_raw = 0
    for answers in by_listener.values():
        ours, raw = answers.get(VERSION_PIPELINE), answers.get(VERSION_RAW)
        if not ours or not raw or ours["rank"] is None or raw["rank"] is None:
            continue
        if ours["rank"] < raw["rank"]:
            prefer_pipeline += 1
        elif raw["rank"] < ours["rank"]:
            prefer_raw += 1

    listeners = len(by_listener)
    median_score = float(median(scores)) if scores else None
    enough = listeners >= MIN_LISTENERS and len(scores) >= MIN_LISTENERS
    score_ok = median_score is not None and median_score >= RELEASE_SCORE_MIN
    preferred = prefer_pipeline > prefer_raw

    problems: list[str] = []
    if not enough:
        problems.append(f"{listeners} listener(s); needs {MIN_LISTENERS}")
    if not score_ok:
        problems.append(
            "no release scores" if median_score is None
            else f"median release score {median_score:g}; needs {RELEASE_SCORE_MIN:g}"
        )
    if not preferred:
        problems.append(f"pipeline preferred by {prefer_pipeline}, raw output by {prefer_raw}")

    return {
        "vocal": name,
        "listeners": listeners,
        "median_release_score": median_score,
        "prefer_pipeline": prefer_pipeline,
        "prefer_raw": prefer_raw,
        "enough_listeners": enough,
        "passed": enough and score_ok and preferred,
        "problems": problems,
    }


def phase_a_gate(vocals: list[dict]) -> dict:
    """The decision across all vocals. `incomplete` until the evidence is all in."""
    passed = sum(1 for v in vocals if v["passed"])
    short = [v["vocal"] for v in vocals if not v["enough_listeners"]]

    if len(vocals) < REQUIRED_VOCALS or short:
        missing = []
        if len(vocals) < REQUIRED_VOCALS:
            missing.append(f"{len(vocals)} of {REQUIRED_VOCALS} vocals tested")
        if short:
            missing.append(f"under {MIN_LISTENERS} listeners on: {', '.join(short)}")
        decision, why = INCOMPLETE, "Not decidable yet: " + "; ".join(missing) + "."
    elif passed >= PASS_AT:
        decision, why = PASS, f"{passed} of {len(vocals)} vocals passed; {PASS_AT} needed."
    elif passed < FAIL_BELOW:
        decision, why = FAIL, (
            f"{passed} of {len(vocals)} vocals passed. Do not build Phases B-E as written; "
            "reframe to mix/master with a supplied beat and revisit generation in 3 months."
        )
    else:
        decision, why = RETRY, (
            f"{passed} of {len(vocals)} vocals passed. One more cycle on vendor choice or "
            "conditioning, then re-test. There is no third cycle."
        )
    return {"decision": decision, "why": why, "passed": passed, "vocals": vocals}


def build_gate_report(result: dict) -> str:
    lines = [
        "# Phase A gate",
        "",
        f"**{result['decision'].upper()}** — {result['why']}",
        "",
        "| Vocal | Listeners | Median release (1–5) | Prefer pipeline | Prefer raw | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for v in result["vocals"]:
        score = "—" if v["median_release_score"] is None else f"{v['median_release_score']:g}"
        verdict = "pass" if v["passed"] else "no: " + "; ".join(v["problems"])
        lines.append(f"| {v['vocal']} | {v['listeners']} | {score} | {v['prefer_pipeline']} "
                     f"| {v['prefer_raw']} | {verdict} |")
    lines += [
        "",
        f"Rule: a vocal passes with at least {MIN_LISTENERS} listeners, a median release "
        f"score of {RELEASE_SCORE_MIN:g} or more for the full pipeline, and more listeners "
        f"ranking it above the vendor's raw output than below. {PASS_AT} of "
        f"{REQUIRED_VOCALS} passes the gate, fewer than {FAIL_BELOW} fails it.",
    ]
    return "\n".join(lines) + "\n"
