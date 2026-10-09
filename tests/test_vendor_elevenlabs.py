"""G2 — the ElevenLabs Music adapter, offline.

These responses are constructed from the documented shape of `POST /v1/music`
(an audio file on 200, a JSON body on an error). They are NOT recordings of the
real API: no key was available when this was written. The one `live` test at
the bottom is the real check, and it has not been run. When it has, replace
the constructed bodies with sanitized recordings under
tests/fixtures/vendors/elevenlabs/.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from producer.generate import GenerationError, GenRequest, get_generator
from producer.generate.ledger import BUDGET_ENV, BudgetExceeded, Ledger
from producer.generate.run import generate_candidates
from producer.generate.vendors.elevenlabs import (
    API_URL,
    KEY_ENV,
    MAX_LENGTH_MS,
    RATE_ENV,
    TERMS_URL,
    ElevenLabsMusic,
)

SR = 44100


def mp3_bytes(seconds: float = 3.0) -> bytes:
    t = np.arange(int(SR * seconds)) / SR
    stereo = np.stack([0.3 * np.sin(2 * np.pi * 220 * t)] * 2, axis=1).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, stereo, SR, format="MP3")
    return buffer.getvalue()


class StubTransport:
    """Hands back queued responses and remembers what it was asked."""

    def __init__(self, *responses: tuple[int, dict, bytes]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


OK = (200, {"song-id": "song_abc123", "Content-Type": "audio/mpeg"}, mp3_bytes())
RATE_LIMITED = (429, {}, b'{"detail": {"status": "too_many_concurrent_requests"}}')
BAD_PROMPT = (400, {}, b'{"detail": {"status": "bad_prompt", "message": "names an artist"}}')


def _backend(transport: StubTransport, monkeypatch: pytest.MonkeyPatch, rate: str = "0.50") -> ElevenLabsMusic:
    monkeypatch.setenv(RATE_ENV, rate)
    return ElevenLabsMusic(api_key="test-key", transport=transport, sleep=lambda _s: None)


def _request(tmp_path: Path, n: int = 1, duration_s: float | None = 90.0) -> GenRequest:
    spec = {"duration_s": duration_s} if duration_s else None
    return GenRequest(vocal=tmp_path / "vocal.wav", style="warm lofi, around 98 BPM, F# minor",
                      spec=spec, seed=7, n_candidates=n)


def test_a_missing_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(GenerationError, match=KEY_ENV):
        get_generator("elevenlabs")


def test_request_matches_the_documented_endpoint(tmp_path: Path, monkeypatch) -> None:
    transport = StubTransport(OK)
    generate_candidates(_backend(transport, monkeypatch), _request(tmp_path), tmp_path / "out")

    call = transport.calls[0]
    assert call["url"].startswith(API_URL) and "output_format=mp3_44100_192" in call["url"]
    assert call["headers"]["xi-api-key"] == "test-key"
    body = call["body"]
    assert body["music_length_ms"] == 90_000            # as long as the vocal
    assert body["force_instrumental"] is True           # the vocal is always the user's own
    assert body["prompt"].startswith("warm lofi, around 98 BPM, F# minor.")
    assert "no vocals" in body["prompt"]
    assert "seed" not in body and "composition_plan" not in body
    # The API has no audio input; nothing about the vocal file may be sent.
    assert "vocal" not in json.dumps({k: v for k, v in body.items() if k != "prompt"})


def test_length_is_clamped_to_what_the_api_allows(tmp_path: Path, monkeypatch) -> None:
    backend = _backend(StubTransport(OK), monkeypatch)
    assert backend.length_ms(_request(tmp_path, duration_s=1.0)) == 3_000
    assert backend.length_ms(_request(tmp_path, duration_s=5000.0)) == MAX_LENGTH_MS
    assert backend.length_ms(_request(tmp_path, duration_s=None)) == 120_000


def test_candidates_cost_and_provenance_are_recorded(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    transport = StubTransport(OK)
    run = generate_candidates(_backend(transport, monkeypatch), _request(tmp_path, n=2), out)

    assert len(transport.calls) == 2
    for i, result in enumerate(run["candidates"], start=1):
        assert result.path == out / f"cand_{i}.wav"
        samples, sr = sf.read(str(result.path))
        assert sr == SR and np.sqrt(np.mean(np.square(samples))) > 1e-3
        assert result.vendor == "elevenlabs" and result.model == "music_v2_5"
        assert result.cost_usd == pytest.approx(0.75)   # 90s at $0.50/min
        assert result.meta["cost_estimated"] is True
        assert result.meta["conditioned_on_vocal"] is False
        assert result.latency_s >= 0

        provenance = json.loads((out / f"cand_{i}.provenance.json").read_text())
        assert provenance["request_id"] == "song_abc123"
        assert provenance["terms_url"] == TERMS_URL and provenance["terms_read_on"]
        assert provenance["prompt"] == transport.calls[0]["body"]["prompt"]
        assert provenance["seed"] is None and provenance["seed_requested"] == 7
        assert provenance["generated_at"]

    entries = Ledger.in_dir(out).entries()
    assert len(entries) == 2
    assert entries[0]["cost_estimated"] is True and entries[0]["request_id"] == "song_abc123"
    assert entries[0]["terms_url"] == TERMS_URL
    assert run["total_cost_usd"] == pytest.approx(1.50)


def test_budget_refuses_before_any_request_is_sent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(BUDGET_ENV, "1.00")
    transport = StubTransport(OK)
    with pytest.raises(BudgetExceeded):
        generate_candidates(_backend(transport, monkeypatch), _request(tmp_path, n=2), tmp_path / "out")
    assert transport.calls == []


def test_the_default_rate_is_pessimistic_enough_to_trip_the_default_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(RATE_ENV, raising=False)
    monkeypatch.delenv(BUDGET_ENV, raising=False)
    backend = ElevenLabsMusic(api_key="k", transport=StubTransport(OK))
    # Three 2.5-minute candidates at the unverified default must not fit in $5.
    assert backend.estimate_cost(_request(tmp_path, n=3, duration_s=150.0)) > 5.0


def test_vendor_errors_are_surfaced_verbatim_and_not_retried(tmp_path: Path, monkeypatch) -> None:
    transport = StubTransport(BAD_PROMPT)
    with pytest.raises(GenerationError) as excinfo:
        generate_candidates(_backend(transport, monkeypatch), _request(tmp_path), tmp_path / "out")

    assert "HTTP 400" in str(excinfo.value) and "names an artist" in str(excinfo.value)
    assert len(transport.calls) == 1                     # it may have been billed; do not repeat
    assert Ledger.in_dir(tmp_path / "out").entries() == []


def test_rate_limiting_is_retried_with_backoff(tmp_path: Path, monkeypatch) -> None:
    waits: list[float] = []
    transport = StubTransport(RATE_LIMITED, RATE_LIMITED, OK)
    monkeypatch.setenv(RATE_ENV, "0.50")
    backend = ElevenLabsMusic(api_key="k", transport=transport, sleep=waits.append)

    run = generate_candidates(backend, _request(tmp_path), tmp_path / "out")

    assert len(run["candidates"]) == 1 and len(transport.calls) == 3
    assert waits == [5.0, 10.0]


def test_a_failure_part_way_still_ledgers_what_was_paid_for(tmp_path: Path, monkeypatch) -> None:
    transport = StubTransport(OK, BAD_PROMPT)
    with pytest.raises(GenerationError):
        generate_candidates(_backend(transport, monkeypatch), _request(tmp_path, n=2), tmp_path / "out")

    entries = Ledger.in_dir(tmp_path / "out").entries()
    assert len(entries) == 1 and entries[0]["cost_usd"] == pytest.approx(0.75)


def test_a_free_plan_key_is_refused_in_the_vendors_own_words(tmp_path: Path, monkeypatch) -> None:
    # The one response here that IS a recording: a real call with a free-plan key.
    recorded = json.loads((Path(__file__).parent / "fixtures" / "vendors" / "elevenlabs"
                           / "402_paid_plan_required.json").read_text())
    transport = StubTransport((recorded["status"], {}, json.dumps(recorded["body"]).encode()))

    with pytest.raises(GenerationError, match="HTTP 402.*upgrade to a paid plan"):
        generate_candidates(_backend(transport, monkeypatch), _request(tmp_path), tmp_path / "out")
    assert len(transport.calls) == 1
    assert Ledger.in_dir(tmp_path / "out").entries() == []     # refused, so nothing was spent


@pytest.mark.live
def test_live_single_short_generation(tmp_path: Path) -> None:
    """One real, short, paid call. Run with: pytest --live -k live_single"""
    if not os.environ.get(KEY_ENV):
        pytest.skip(f"{KEY_ENV} is not set")
    req = GenRequest(vocal=tmp_path / "unused.wav", style="slow lofi hip hop, around 85 BPM",
                     spec={"duration_s": 10.0}, n_candidates=1)
    run = generate_candidates(get_generator("elevenlabs"), req, tmp_path / "live")
    result = run["candidates"][0]
    samples, _ = sf.read(str(result.path))
    assert len(samples) > 0 and np.sqrt(np.mean(np.square(samples))) > 1e-4
