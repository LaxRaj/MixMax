"""Synthetic fixtures, so the test suite never depends on real audio."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

FIXTURES = Path(__file__).parent / "fixtures"
SR = 44100


def sine(freq: float, seconds: float, amplitude: float, sr: int = SR) -> np.ndarray:
    """A constant-amplitude sine, as float32 mono."""
    t = np.linspace(0.0, seconds, int(sr * seconds), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_if_missing(path: Path, samples: np.ndarray, sr: int = SR) -> Path:
    """Fixtures are committed, so only synthesize what isn't already on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        sf.write(str(path), samples, sr)
    return path


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures() -> None:
    """Build the base fixture pair before any test runs."""
    write_if_missing(FIXTURES / "target.wav", sine(440.0, 2.0, 0.30))
    write_if_missing(FIXTURES / "reference.wav", sine(220.0, 2.0, 0.50))


@pytest.fixture()
def target_wav() -> Path:
    return FIXTURES / "target.wav"


@pytest.fixture()
def reference_wav() -> Path:
    return FIXTURES / "reference.wav"
