"""G0 — the generator interface, the fake backend, the ledger and its budget."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from click.testing import CliRunner

from producer.cli import cli
from producer.generate import Generator, GenRequest, get_generator, registered
from producer.generate.fake import FakeGenerator
from producer.generate.ledger import (
    BUDGET_ENV,
    DEFAULT_BUDGET_USD,
    BudgetExceeded,
    Ledger,
    check_budget,
)
from producer.generate.run import generate_candidates


def _request(vocal: Path, n: int = 3, seed: int | None = 7) -> GenRequest:
    return GenRequest(vocal=vocal, style="lofi", seed=seed, n_candidates=n)


def test_fake_backend_returns_n_audible_candidates(tmp_path: Path, target_wav: Path) -> None:
    generator = get_generator("fake")
    assert isinstance(generator, Generator)

    run = generate_candidates(generator, _request(target_wav), tmp_path)

    assert len(run["candidates"]) == 3
    for i, result in enumerate(run["candidates"], start=1):
        assert result.path == tmp_path / f"cand_{i}.wav"
        samples, _ = sf.read(str(result.path))
        assert np.sqrt(np.mean(np.square(samples))) > 1e-3   # not silent
        assert result.cost_usd == 0.0
        assert result.meta["generated"] is False


def test_fake_backend_is_deterministic_by_seed(tmp_path: Path, target_wav: Path) -> None:
    def sources(seed: int, where: str) -> list[str]:
        run = generate_candidates(FakeGenerator(), _request(target_wav, seed=seed), tmp_path / where)
        return [r.meta["source"] for r in run["candidates"]]

    assert sources(3, "a") == sources(3, "b")
    assert any(sources(s, f"s{s}") != sources(3, "a") for s in range(4, 12))


def test_ledger_has_one_line_per_candidate(tmp_path: Path, target_wav: Path) -> None:
    generate_candidates(FakeGenerator(), _request(target_wav, n=4), tmp_path)

    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert len(lines) == 4
    entry = json.loads(lines[0])
    assert entry["vendor"] == "fake"
    assert {"date", "model", "prompt_hash", "cost_usd", "latency_s", "terms_url"} <= set(entry)

    record = json.loads((tmp_path / "generation.json").read_text())
    assert record["request"]["style"] == "lofi"
    assert len(record["results"]) == 4
    assert record["total_cost_usd"] == 0.0


def test_check_budget_uses_the_env_and_its_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BUDGET_ENV, raising=False)
    assert check_budget(0.0, DEFAULT_BUDGET_USD) == DEFAULT_BUDGET_USD
    with pytest.raises(BudgetExceeded):
        check_budget(DEFAULT_BUDGET_USD, 0.01)

    monkeypatch.setenv(BUDGET_ENV, "1.5")
    check_budget(1.0, 0.5)
    with pytest.raises(BudgetExceeded, match="Nothing was generated"):
        check_budget(1.0, 0.51)


def test_budget_guard_refuses_before_the_backend_is_called(
    tmp_path: Path, target_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BUDGET_ENV, "1.00")
    generator = FakeGenerator(cost_per_candidate=0.40)

    # 2 x $0.40 fits in $1.00 ...
    generate_candidates(generator, _request(target_wav, n=2), tmp_path)
    assert Ledger.in_dir(tmp_path).spent() == pytest.approx(0.80)

    # ... a third candidate would not, and must cost nothing to find out.
    with pytest.raises(BudgetExceeded):
        generate_candidates(generator, _request(target_wav, n=1), tmp_path)
    assert generator.calls == 1
    assert len(Ledger.in_dir(tmp_path).entries()) == 2


def test_unknown_backend_names_the_registered_ones() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_generator("nope")
    message = excinfo.value.args[0]
    assert "nope" in message
    for name in registered():
        assert name in message


def test_generate_cli_end_to_end(tmp_path: Path, target_wav: Path) -> None:
    out = tmp_path / "g0"
    result = CliRunner().invoke(cli, [
        "generate", "--vocal", str(target_wav), "--style", "lofi",
        "--out-dir", str(out), "--backend", "fake", "--n", "2", "--seed", "1",
    ])

    assert result.exit_code == 0, result.output
    assert (out / "cand_1.wav").stat().st_size > 0
    assert (out / "cand_2.wav").exists() and not (out / "cand_3.wav").exists()
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 2
    assert result.output.count("cand_") >= 2
    assert "Nothing here was generated" in result.output


def test_generate_cli_rejects_an_unknown_backend(tmp_path: Path, target_wav: Path) -> None:
    result = CliRunner().invoke(cli, [
        "generate", "--vocal", str(target_wav), "--style", "x",
        "--out-dir", str(tmp_path / "o"), "--backend", "nope",
    ])
    assert result.exit_code != 0
    assert "fake" in result.output
