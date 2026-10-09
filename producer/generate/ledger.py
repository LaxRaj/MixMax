"""Every generation call, written down, and a budget that refuses before spending.

Generation is the only stage that costs money per run. The ledger is append-only
so what was spent can always be reconciled, and the guard runs before the call
rather than reporting an overrun after it.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from producer.generate.base import GenRequest, GenResult

LEDGER_FILE = "ledger.jsonl"
BUDGET_ENV = "MIXMAX_GEN_BUDGET_USD"
DEFAULT_BUDGET_USD = 5.0


class BudgetExceeded(RuntimeError):
    """The next call would take spending past the budget. Nothing was spent."""


def budget_usd() -> float:
    raw = os.environ.get(BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_BUDGET_USD
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{BUDGET_ENV} must be a number of dollars; got {raw!r}.") from exc


def check_budget(spent: float, next_estimate: float) -> float:
    """Raise if `next_estimate` on top of `spent` would exceed the budget."""
    budget = budget_usd()
    if spent + next_estimate > budget + 1e-9:
        raise BudgetExceeded(
            f"This call is estimated at ${next_estimate:.2f}; ${spent:.2f} is already spent "
            f"and the budget is ${budget:.2f} ({BUDGET_ENV}). Nothing was generated."
        )
    return budget


def prompt_hash(req: GenRequest) -> str:
    """Stable fingerprint of what was asked for."""
    payload = json.dumps(
        {"style": req.style, "spec": req.spec, "seed": req.seed, "lyrics": req.lyrics},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def in_dir(cls, out_dir: str | Path) -> "Ledger":
        return cls(Path(out_dir) / LEDGER_FILE)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def spent(self) -> float:
        return float(sum(e.get("cost_usd") or 0.0 for e in self.entries()))

    def record(self, result: GenResult, req: GenRequest) -> dict:
        """Append one line for one candidate."""
        entry = {
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "vendor": result.vendor,
            "model": result.model,
            "prompt_hash": prompt_hash(req),
            "cost_usd": result.cost_usd,
            "cost_estimated": bool(result.meta.get("cost_estimated", False)),
            "latency_s": result.latency_s,
            "request_id": result.meta.get("request_id"),
            "terms_url": result.meta.get("terms_url"),
            "path": str(result.path),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry
