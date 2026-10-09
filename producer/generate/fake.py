"""A generator that generates nothing: it hands back beats already on disk.

It exists so the pipeline around generation can be built and tested offline,
for free and deterministically. Nothing it returns was written for the vocal.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from producer.audio import AUDIO_SUFFIXES, Track
from producer.generate.base import GenerationError, GenRequest, GenResult

# The committed synthetic fixtures; present in a checkout, absent from a wheel.
DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# Point the fake at a real beat (a file, or a folder of them) without code.
SOURCE_ENV = "MIXMAX_FAKE_BEAT"


class FakeGenerator:
    name = "fake"
    model = "fake-copy"

    def __init__(self, source: str | Path | None = None, cost_per_candidate: float = 0.0) -> None:
        self.source = Path(source or os.environ.get(SOURCE_ENV) or DEFAULT_SOURCE)
        self.cost_per_candidate = float(cost_per_candidate)
        self.calls = 0

    def _sources(self) -> list[Path]:
        if self.source.is_file():
            return [self.source]
        found = sorted(
            p for p in self.source.glob("*") if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
        )
        if not found:
            raise GenerationError(
                f"The fake generator has no beat to copy: nothing readable at {self.source}. "
                f"Set {SOURCE_ENV} to a beat or a folder of beats."
            )
        return found

    def estimate_cost(self, req: GenRequest) -> float:
        return self.cost_per_candidate * req.n_candidates

    def generate(self, req: GenRequest) -> list[GenResult]:
        if req.out_dir is None:
            raise GenerationError("GenRequest.out_dir is not set; there is nowhere to write.")
        self.calls += 1
        sources = self._sources()
        # Same seed, same candidates in the same order.
        start = random.Random(req.seed or 0).randrange(len(sources))
        out_dir = Path(req.out_dir)

        results: list[GenResult] = []
        for i in range(req.n_candidates):
            source = sources[(start + i) % len(sources)]
            path = Track.load(source).write(out_dir / f"cand_{i + 1}.wav")
            results.append(GenResult(
                path=path, vendor=self.name, model=self.model,
                cost_usd=self.cost_per_candidate, latency_s=0.0,
                meta={"source": str(source), "seed": req.seed, "generated": False},
            ))
        return results
