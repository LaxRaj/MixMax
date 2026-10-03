"""The full-mix mastering chain — the one that is not for a lone vocal."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from producer.audio import Track
from producer.benchmark import measure
from producer.master_chain import (
    DEFAULT_MASTER_PARAMS,
    MasterParams,
    apply_width,
    build_master_chain,
    is_effectively_mono,
    master_full_mix,
)

SR = 44100


def _mix(seconds: float = 12.0, stereo: bool = False, sub: float = 0.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(3)
    out = 0.35 * (np.sin(2 * np.pi * 220 * t) + 0.4 * np.sin(2 * np.pi * 110 * t))
    step = int(SR * 0.5)
    env = np.exp(-np.linspace(0, 9, int(0.2 * SR)))
    kick = np.sin(2 * np.pi * 60 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(kick), step):
        out[start:start + len(kick)] += kick * 0.8
    if sub:
        out = out + sub * np.sin(2 * np.pi * 8 * t)  # subsonic rumble
    out = (out / 2.2).astype(np.float32)
    if stereo:
        return np.stack([out, np.roll(out, 300) * 0.9], axis=1).astype(np.float32)
    return out


def test_chain_is_subsonic_cleanup_then_glue() -> None:
    """Two highpasses: pedalboard's is one pole, and 6 dB/oct is not enough."""
    assert [type(p).__name__ for p in build_master_chain()] == [
        "HighpassFilter", "HighpassFilter", "Compressor",
    ]


def test_defaults_are_gentle() -> None:
    """A master is the last place to make large moves, especially blind."""
    p = DEFAULT_MASTER_PARAMS
    assert p.subsonic_hz <= 30.0, "a higher cutoff starts thinning the kick"
    assert p.glue_ratio <= 2.0
    assert p.glue_attack_ms >= 20.0, "a fast attack would flatten the transients"


def _energy_below(path: Path, hz: float) -> float:
    """Share of total energy under `hz`, in dB.

    The benchmark's `sub` band starts at 20 Hz, so it cannot see the rumble
    this chain exists to remove — that has to be measured directly.
    """
    track = Track.load(path)
    mono = track.mono()
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / track.sample_rate)
    power = np.square(spectrum, dtype=np.float64)
    return float(10 * np.log10(max(power[freqs < hz].sum() / power.sum(), 1e-20)))


def test_subsonic_energy_is_removed(tmp_path: Path) -> None:
    src = tmp_path / "rumble.wav"
    sf.write(str(src), _mix(sub=0.3), SR)

    before = _energy_below(src, 20.0)
    master_full_mix(src, tmp_path / "out.wav")
    after = _energy_below(tmp_path / "out.wav", 20.0)
    assert after < before - 10.0, f"rumble barely moved: {before:.1f} -> {after:.1f} dB"


def test_the_kick_survives(tmp_path: Path) -> None:
    """The vocal chain's 80 Hz highpass would gut this; 25 Hz must not."""
    src = tmp_path / "mix.wav"
    sf.write(str(src), _mix(), SR)

    before = measure(src)["band_balance_db"]["low"]
    master_full_mix(src, tmp_path / "out.wav")
    after = measure(tmp_path / "out.wav")["band_balance_db"]["low"]
    assert abs(after - before) < 1.5, (before, after)


def test_glue_does_not_flatten_the_mix(tmp_path: Path) -> None:
    src = tmp_path / "mix.wav"
    sf.write(str(src), _mix(), SR)

    before = measure(src)["crest_factor_db"]
    master_full_mix(src, tmp_path / "out.wav")
    after = measure(tmp_path / "out.wav")["crest_factor_db"]
    assert after > before - 3.0, f"chain cost {before - after:.1f} dB of crest"


def test_mono_is_detected(tmp_path: Path) -> None:
    mono, stereo = tmp_path / "m.wav", tmp_path / "s.wav"
    sf.write(str(mono), _mix(), SR)
    sf.write(str(stereo), _mix(stereo=True), SR)

    assert is_effectively_mono(Track.load(mono)) is True
    assert is_effectively_mono(Track.load(stereo)) is False


def test_dual_mono_counts_as_mono(tmp_path: Path) -> None:
    """Two identical channels carry no stereo information."""
    path = tmp_path / "dual.wav"
    one = _mix()
    sf.write(str(path), np.stack([one, one], axis=1), SR)
    assert is_effectively_mono(Track.load(path)) is True


def test_width_leaves_mono_alone() -> None:
    """Widening a mono file means inventing the side signal."""
    mono = _mix()
    assert np.array_equal(apply_width(mono, 1.6), mono)


def test_width_changes_stereo() -> None:
    stereo = _mix(stereo=True)
    wider = apply_width(stereo, 1.6)
    side_before = np.std(stereo[:, 0] - stereo[:, 1])
    side_after = np.std(wider[:, 0] - wider[:, 1])
    assert side_after > side_before * 1.4


def test_width_of_one_is_a_no_op() -> None:
    stereo = _mix(stereo=True)
    assert np.allclose(apply_width(stereo, 1.0), stereo)


def test_master_reports_a_mono_source(tmp_path: Path) -> None:
    src = tmp_path / "m.wav"
    sf.write(str(src), _mix(), SR)
    result = master_full_mix(src, tmp_path / "out.wav", MasterParams(width=1.8))

    assert result["mono_source"] is True
    # Width must not be claimed on a file that has none to give.
    assert result["width_applied"] == 1.0


def test_chain_preserves_duration(tmp_path: Path) -> None:
    src = tmp_path / "m.wav"
    sf.write(str(src), _mix(), SR)
    master_full_mix(src, tmp_path / "out.wav")

    assert abs(sf.info(str(tmp_path / "out.wav")).duration - sf.info(str(src)).duration) < 0.05


def test_full_mix_gets_the_master_chain_not_the_vocal_chain(tmp_path: Path) -> None:
    """The distinction this module exists for."""
    from producer.shootout import build_shootout

    src = tmp_path / "mix.wav"
    sf.write(str(src), _mix(seconds=14.0), SR)
    result = build_shootout(source=src, out_dir=tmp_path / "out", targets=("spotify",))

    assert result["vocal_chain_applied"] is False
    assert result["master_chain_applied"] is True
