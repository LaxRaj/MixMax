"""One generation run: budget check, the call, the ledger, and the record of it."""

from __future__ import annotations

import json
from pathlib import Path

from producer.generate.base import Generator, GenRequest, GenResult
from producer.generate.ledger import Ledger, check_budget

GENERATION_FILE = "generation.json"


def generate_candidates(
    generator: Generator,
    req: GenRequest,
    out_dir: str | Path,
    ledger: Ledger | None = None,
) -> dict:
    """Ask `generator` for candidates and write everything about the run down.

    The budget is checked against the ledger before the backend is called, so a
    refusal costs nothing. Returns the contents of `generation.json`, with the
    live `GenResult` objects under `"candidates"`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    req.out_dir = out_dir
    ledger = ledger or Ledger.in_dir(out_dir)

    estimate = float(generator.estimate_cost(req))
    budget = check_budget(ledger.spent(), estimate)

    results: list[GenResult] = generator.generate(req)
    for result in results:
        ledger.record(result, req)

    record = {
        "backend": generator.name,
        "request": req.to_dict(),
        "results": [r.to_dict() for r in results],
        "estimated_cost_usd": round(estimate, 4),
        "total_cost_usd": round(sum(r.cost_usd for r in results), 4),
        "ledger": str(ledger.path),
        "ledger_spent_usd": round(ledger.spent(), 4),
        "budget_usd": budget,
    }
    (out_dir / GENERATION_FILE).write_text(json.dumps(record, indent=2, default=str) + "\n")
    return {**record, "candidates": results}
