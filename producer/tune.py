"""Fit the vocal chain to a target master, by measurement.

This optimises *measurable* similarity to a target you already trust — a LANDR
master of the same vocal, or a commercial track. It does not, and cannot, learn
what sounds good: nothing here listens. Closing the measured gap is a lead, and
the blind listening test remains the only thing that settles the question.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from producer.benchmark import BANDS, band_is_audible, measure
from producer.mastering import master_track
from producer.mix import ChainParams, DEFAULT_PARAMS, mix_vocal

# How much each measurement contributes to the distance. Spectral balance
# dominates because it is what listeners describe as "harsh" or "muddy";
# crest factor matters because over-compression is the usual giveaway.
WEIGHTS = {
    "band": 1.0,
    "crest": 0.8,
    "lra": 0.5,
    "centroid": 0.6,
}

# Search space: (low, high) per knob.
SPACE: dict[str, tuple[float, float]] = {
    "highpass_hz": (40.0, 160.0),
    "gate_threshold_db": (-60.0, -25.0),
    "comp_threshold_db": (-30.0, -8.0),
    "comp_ratio": (1.5, 6.0),
    "deess_hz": (5000.0, 9000.0),
    "deess_gain_db": (-10.0, 0.0),
    "deess_q": (1.0, 4.0),
    "reverb_room": (0.05, 0.40),
    "reverb_wet": (0.0, 0.20),
}

# A centroid ratio of this much counts as a full unit of error.
CENTROID_TOLERANCE = 0.3


@dataclass
class TuneResult:
    params: ChainParams
    loss: float
    baseline_loss: float
    evaluations: int
    history: list[float]

    @property
    def improvement(self) -> float:
        if self.baseline_loss <= 0:
            return 0.0
        return (self.baseline_loss - self.loss) / self.baseline_loss

    def to_dict(self) -> dict:
        return {
            "params": self.params.to_dict(),
            "loss": round(self.loss, 4),
            "baseline_loss": round(self.baseline_loss, 4),
            "improvement_pct": round(self.improvement * 100, 1),
            "evaluations": self.evaluations,
        }


def distance(ours: dict, target: dict) -> float:
    """How far our master sits from the target, in weighted measurement units.

    Loudness is deliberately excluded: matchering sets it from the reference,
    so including it would just measure the reference choice.
    """
    terms: list[float] = []

    audible = [
        band for band in BANDS
        if band_is_audible(ours["band_balance_db"][band], target["band_balance_db"][band])
    ]
    if audible:
        band_error = float(
            np.mean([abs(ours["band_balance_db"][b] - target["band_balance_db"][b]) for b in audible])
        )
        terms.append(WEIGHTS["band"] * band_error)

    terms.append(WEIGHTS["crest"] * abs(ours["crest_factor_db"] - target["crest_factor_db"]))

    if ours["lra"] is not None and target["lra"] is not None:
        terms.append(WEIGHTS["lra"] * abs(ours["lra"] - target["lra"]))

    ours_c, target_c = ours["spectral_centroid_hz"], target["spectral_centroid_hz"]
    if ours_c > 0 and target_c > 0:
        ratio = abs(np.log2(ours_c / target_c))
        terms.append(WEIGHTS["centroid"] * float(ratio) / CENTROID_TOLERANCE)

    return float(sum(terms))


def _render(vocal: Path, reference: Path, params: ChainParams, workdir: Path) -> dict:
    """Mix and master `vocal` with `params`, and measure the result."""
    mixed = workdir / "mixed.wav"
    mastered = workdir / "mastered.wav"
    mix_vocal(vocal, mixed, params)
    master_track(mixed, reference, mastered)
    return measure(mastered)


def _sample(rng: random.Random) -> ChainParams:
    return ChainParams(**{name: rng.uniform(lo, hi) for name, (lo, hi) in SPACE.items()})


def _neighbour(params: ChainParams, rng: random.Random, scale: float) -> ChainParams:
    """A nearby point: nudge one knob within its range."""
    name = rng.choice(list(SPACE))
    lo, hi = SPACE[name]
    current = getattr(params, name)
    step = (hi - lo) * scale * rng.uniform(-1.0, 1.0)
    return replace(params, **{name: float(np.clip(current + step, lo, hi))})


def tune_chain(
    vocal: str | Path,
    reference: str | Path,
    target: str | Path,
    budget: int = 60,
    seed: int | None = 0,
    on_step: "callable | None" = None,
) -> TuneResult:
    """Search chain settings that bring our master closest to `target`.

    Random exploration first, then local refinement around the best point --
    the objective is cheap but noisy-looking, and a full optimiser would spend
    the budget on gradients that do not exist here.
    """
    vocal, reference, target = Path(vocal), Path(reference), Path(target)
    target_measurement = measure(target)
    rng = random.Random(seed)

    explore = max(1, budget // 2)
    history: list[float] = []

    with TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        def evaluate(params: ChainParams) -> float:
            value = distance(_render(vocal, reference, params, workdir), target_measurement)
            history.append(value)
            if on_step is not None:
                on_step(len(history), value, min(history))
            return value

        baseline = evaluate(DEFAULT_PARAMS)
        best_params, best_loss = DEFAULT_PARAMS, baseline

        for _ in range(explore):
            candidate = _sample(rng)
            loss = evaluate(candidate)
            if loss < best_loss:
                best_params, best_loss = candidate, loss

        # Refine: shrink the step as the budget runs down.
        remaining = budget - explore
        for i in range(remaining):
            scale = 0.25 * (1.0 - i / max(remaining, 1)) + 0.02
            candidate = _neighbour(best_params, rng, scale)
            loss = evaluate(candidate)
            if loss < best_loss:
                best_params, best_loss = candidate, loss

    return TuneResult(
        params=best_params,
        loss=best_loss,
        baseline_loss=baseline,
        evaluations=len(history),
        history=history,
    )
