"""ElevenLabs Music: a backing written from words, never from the vocal.

`POST /v1/music` takes a prompt and a length and returns audio. No field
accepts uploaded audio, so this backend cannot hear the vocal: all it knows of
it is what `VocalSpec.to_prompt_hints()` put into the style text. That makes
it the text-only path. `fit` is what decides whether the result belongs under
the vocal.

Terms, limits and everything not verified are in docs/VENDORS.md. If the API
turns out to differ from what that file says, fix the file first.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from producer.audio import Track
from producer.generate.base import GenerationError, GenRequest, GenResult

API_URL = "https://api.elevenlabs.io/v1/music"
KEY_ENV = "ELEVENLABS_API_KEY"
MODEL_ENV = "ELEVENLABS_MUSIC_MODEL"
RATE_ENV = "ELEVENLABS_MUSIC_USD_PER_MIN"
FORMAT_ENV = "ELEVENLABS_MUSIC_FORMAT"

DEFAULT_MODEL = "music_v2_5"
DEFAULT_FORMAT = "mp3_44100_192"

# The response does not report what a call cost, and the per-minute price past
# a plan's allowance was not found in the vendor's pages. This default is
# deliberately pessimistic so the budget guard errs toward refusing; set the
# env var to your plan's real rate. Every cost from this backend is flagged
# as estimated.
DEFAULT_USD_PER_MIN = 1.00

TERMS_URL = "https://elevenlabs.io/eleven-music-model-specific-terms"
TERMS_READ_ON = "2026-10-08"

# The API's own bounds on a track.
MIN_LENGTH_MS, MAX_LENGTH_MS = 3_000, 600_000
DEFAULT_LENGTH_MS = 120_000

REQUEST_TIMEOUT_S = 600.0
# Only "too many requests" is retried: the vendor says outright that it did
# nothing. Any other failure may have generated, and been billed for, a track,
# so it is reported instead of repeated.
RETRY_STATUS = 429
MAX_RETRIES = 4
BACKOFF_S = 5.0

INSTRUMENTAL_ONLY = "Instrumental backing track only, no vocals, no singing, no spoken words."

Response = tuple[int, dict, bytes]
Transport = Callable[[str, dict, bytes, float], Response]


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class ElevenLabsMusic:
    name = "elevenlabs"
    conditions_on_vocal = False

    def __init__(
        self,
        api_key: str | None = None,
        transport: Transport = _urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.environ.get(KEY_ENV, "").strip()
        if not self.api_key:
            raise GenerationError(
                f"{KEY_ENV} is not set. The ElevenLabs Music API needs a key from a paid plan; "
                "put it in .env or the environment. See docs/VENDORS.md."
            )
        self.model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
        self.output_format = os.environ.get(FORMAT_ENV, "").strip() or DEFAULT_FORMAT
        raw_rate = os.environ.get(RATE_ENV, "").strip()
        try:
            self.usd_per_min = float(raw_rate) if raw_rate else DEFAULT_USD_PER_MIN
        except ValueError as exc:
            raise GenerationError(f"{RATE_ENV} must be a number of dollars; got {raw_rate!r}.") from exc
        self.transport = transport
        self.sleep = sleep

    # ── request ─────────────────────────────────────────────────────────────

    def length_ms(self, req: GenRequest) -> int:
        """As long as the vocal, within what the API allows."""
        duration_s = (req.spec or {}).get("duration_s")
        wanted = int(round(float(duration_s) * 1000)) if duration_s else DEFAULT_LENGTH_MS
        return max(MIN_LENGTH_MS, min(wanted, MAX_LENGTH_MS))

    def prompt(self, req: GenRequest) -> str:
        return f"{req.style.strip().rstrip('.')}. {INSTRUMENTAL_ONLY}"

    def estimate_cost(self, req: GenRequest) -> float:
        return round(self.length_ms(req) / 60_000.0 * self.usd_per_min * req.n_candidates, 4)

    def _call(self, body: dict) -> Response:
        url = f"{API_URL}?{urllib.parse.urlencode({'output_format': self.output_format})}"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = json.dumps(body).encode()
        for attempt in range(MAX_RETRIES + 1):
            status, response_headers, content = self.transport(url, headers, payload, REQUEST_TIMEOUT_S)
            if status != RETRY_STATUS or attempt == MAX_RETRIES:
                break
            self.sleep(BACKOFF_S * 2 ** attempt)
        if status != 200:
            # The vendor's own words, untouched: they are the only diagnosis there is.
            raise GenerationError(
                f"ElevenLabs Music returned HTTP {status}: {content.decode('utf-8', 'replace')}"
            )
        if not content:
            raise GenerationError("ElevenLabs Music returned HTTP 200 with no audio.")
        return status, response_headers, content

    # ── generate ────────────────────────────────────────────────────────────

    def generate(self, req: GenRequest) -> list[GenResult]:
        if req.out_dir is None:
            raise GenerationError("GenRequest.out_dir is not set; there is nowhere to write.")
        out_dir = Path(req.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        length_ms = self.length_ms(req)
        prompt = self.prompt(req)
        # `seed` cannot be combined with `prompt` on this endpoint, so it is not
        # sent; candidates differ because the model is not deterministic.
        body = {
            "prompt": prompt,
            "music_length_ms": length_ms,
            "model_id": self.model,
            "force_instrumental": True,
        }
        extension = self.output_format.split("_", 1)[0]
        if extension != "mp3":
            raise GenerationError(
                f"{FORMAT_ENV}={self.output_format!r} is not supported here; use an mp3_* format."
            )

        results: list[GenResult] = []
        for index in range(1, req.n_candidates + 1):
            started = time.monotonic()
            try:
                _, headers, content = self._call(body)
            except GenerationError as exc:
                raise GenerationError(str(exc), partial=results) from None
            latency = round(time.monotonic() - started, 2)

            downloaded = out_dir / f"cand_{index}.{extension}"
            downloaded.write_bytes(content)
            path = Track.load(downloaded).write(out_dir / f"cand_{index}.wav")

            lowered = {k.lower(): v for k, v in headers.items()}
            request_id = lowered.get("song-id") or lowered.get("request-id")
            cost = round(length_ms / 60_000.0 * self.usd_per_min, 4)
            provenance = {
                "vendor": self.name,
                "model": self.model,
                "request_id": request_id,
                "prompt": prompt,
                "seed": None,
                "seed_requested": req.seed,
                "music_length_ms": length_ms,
                "conditioned_on_vocal": False,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "terms_url": TERMS_URL,
                "terms_read_on": TERMS_READ_ON,
                "source_file": downloaded.name,
            }
            (out_dir / f"cand_{index}.provenance.json").write_text(
                json.dumps(provenance, indent=2) + "\n")

            results.append(GenResult(
                path=path, vendor=self.name, model=self.model,
                cost_usd=cost, latency_s=latency,
                meta={
                    "generated": True,
                    "request_id": request_id,
                    "cost_estimated": True,
                    "cost_basis": f"{length_ms / 1000:.0f}s at ${self.usd_per_min:.2f}/min ({RATE_ENV})",
                    "conditioned_on_vocal": False,
                    "terms_url": TERMS_URL,
                    "terms_read_on": TERMS_READ_ON,
                    "provenance": str(out_dir / f"cand_{index}.provenance.json"),
                },
            ))
        return results
