"""The types every generator speaks, and nothing vendor-specific."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class NotSupported(RuntimeError):
    """The backend cannot do what was asked, e.g. condition on an uploaded vocal."""


class GenerationError(RuntimeError):
    """A backend failed. The vendor's own message is carried verbatim.

    `partial` holds any candidates that were generated, and paid for, before
    the failure, so the caller can still put them in the ledger.
    """

    def __init__(self, message: str, partial: "list[GenResult] | None" = None) -> None:
        super().__init__(message)
        self.partial = partial or []


@dataclass
class GenRequest:
    """One ask: a backing for this vocal, in this style."""

    vocal: Path
    style: str
    spec: dict | None = None      # a VocalSpec as a dict, when one was measured
    seed: int | None = None
    n_candidates: int = 3
    lyrics: str | None = None
    out_dir: Path | None = None   # where candidates are written; set by the caller

    def to_dict(self) -> dict:
        return {
            "vocal": str(self.vocal),
            "style": self.style,
            "spec": self.spec,
            "seed": self.seed,
            "n_candidates": self.n_candidates,
            "lyrics": self.lyrics,
        }


@dataclass
class GenResult:
    """One candidate backing, and what it cost to get."""

    path: Path
    stems: dict[str, Path] = field(default_factory=dict)
    vendor: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_s: float = 0.0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "stems": {name: str(path) for name, path in self.stems.items()},
            "vendor": self.vendor,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "latency_s": self.latency_s,
            "meta": self.meta,
        }


@runtime_checkable
class Generator(Protocol):
    """A backend that turns a request into candidate backings on disk."""

    name: str

    def estimate_cost(self, req: GenRequest) -> float:
        """What `generate(req)` is expected to cost, so a budget can refuse first."""
        ...

    def generate(self, req: GenRequest) -> list[GenResult]:
        ...
