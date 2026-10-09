"""Synthetic fixtures, so the test suite never depends on real audio."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

FIXTURES = Path(__file__).parent / "fixtures"
SR = 44100


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", default=False,
                     help="Run tests marked `live`, which call a paid vendor API.")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls a real vendor API and costs money")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (calls a paid vendor API)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


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


def click_track(bpm: float, seconds: float, sr: int = SR) -> np.ndarray:
    """A click train at a known tempo, so tempo detection has a ground truth."""
    n = int(sr * seconds)
    out = np.zeros(n, dtype=np.float32)
    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0, 8, click_len)).astype(np.float32)
    burst = envelope * np.sin(2 * np.pi * 1000.0 * np.arange(click_len) / sr).astype(np.float32)
    step = int(sr * 60.0 / bpm)
    for start in range(0, n - click_len, step):
        out[start:start + click_len] += burst
    return np.clip(out * 0.8, -1.0, 1.0)


@pytest.fixture(scope="session")
def click_120_wav(ensure_fixtures: None) -> Path:
    """A 120 BPM click track — committed, used to pin tempo detection."""
    return write_if_missing(FIXTURES / "click_120bpm.wav", click_track(120.0, 8.0))


@pytest.fixture(scope="session")
def clipped_wav(ensure_fixtures: None) -> Path:
    """A fixture that deliberately clips, to prove the QA gate catches it."""
    samples = sine(440.0, 2.0, 1.0)
    samples[::100] = 1.0  # unambiguous full-scale hits
    return write_if_missing(FIXTURES / "clipped.wav", samples)


@pytest.fixture(scope="session")
def clean_wav(ensure_fixtures: None) -> Path:
    """A fixture normalized into the QA loudness window, so it should pass."""
    import pyloudnorm as pyln

    path = FIXTURES / "clean_-12lufs.wav"
    if path.exists():
        return path
    samples = sine(440.0, 3.0, 0.3)
    meter = pyln.Meter(SR)
    normalized = pyln.normalize.loudness(samples, meter.integrated_loudness(samples), -12.0)
    return write_if_missing(path, normalized.astype(np.float32))


@pytest.fixture(scope="session")
def out_of_phase_wav(ensure_fixtures: None) -> Path:
    """Stereo with L and R inverted — cancels completely when folded to mono."""
    left = sine(440.0, 2.0, 0.3)
    stereo = np.stack([left, -left], axis=1)
    return write_if_missing(FIXTURES / "out_of_phase.wav", stereo)


@pytest.fixture(scope="session")
def batch_dir(ensure_fixtures: None) -> Path:
    """A small folder of committed fixtures for the batch harness."""
    directory = FIXTURES / "batch"
    write_if_missing(directory / "take_a.wav", sine(330.0, 2.0, 0.25))
    write_if_missing(directory / "take_b.wav", sine(550.0, 2.0, 0.35))
    write_if_missing(directory / "take_c.wav", click_track(100.0, 3.0))
    return directory


@pytest.fixture()
def versions_dir(tmp_path: Path) -> Path:
    """Three 'services' rendering the same source, with known differences.

    `rival` is deliberately quieter, more dynamic and darker than `producer`,
    so the delta maths has a ground truth to hit.
    """
    directory = tmp_path / "versions"
    directory.mkdir()

    base = sine(440.0, 4.0, 0.30) + sine(7000.0, 4.0, 0.08)

    # producer: loud, bright, squashed.
    sf.write(str(directory / "producer.wav"), np.clip(base * 2.2, -1.0, 1.0), SR)
    # rival: quieter, darker (less 7 kHz), untouched dynamics.
    rival = sine(440.0, 4.0, 0.30) + sine(7000.0, 4.0, 0.01)
    sf.write(str(directory / "rival.wav"), rival * 0.5, SR)
    # original: the unmastered source.
    sf.write(str(directory / "original.wav"), base * 0.4, SR)
    return directory
