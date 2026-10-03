"""Arrangement editing: build a longer song out of the parts already there.

The structure analyser can say a track has no bridge and no drop. This acts on
that, by cutting the source on bar lines and reassembling it -- repeating the
hook, filtering a section down into a breakdown, sweeping a riser into the
return.

It composes nothing. Every sample out is a sample in, moved, filtered or faded.
A bridge made from existing material is a real technique and it is honest about
what it is; it is not the same as writing a new part.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np
from pedalboard import HighpassFilter, LowpassFilter, Pedalboard, Reverb

from producer.audio import Track

BEATS_PER_BAR = 4

# Joins land on a bar line, so a short crossfade is enough to kill the click
# without smearing the downbeat.
JOIN_CROSSFADE_S = 0.012

# Filter sweeps are rendered as a blend between fixed-cutoff copies: pedalboard
# has no cutoff automation, and per-block filtering leaves seams.
SWEEP_STEPS = 24


@dataclass
class Segment:
    """One slice of the source, with what to do to it."""

    source_start_s: float
    source_end_s: float
    role: str = ""
    gain_db: float = 0.0
    # A fixed highpass, for a breakdown with the low end pulled out.
    highpass_hz: float | None = None
    # A sweep from `start` to `end` across the segment, for a riser.
    highpass_sweep: tuple[float, float] | None = None
    lowpass_sweep: tuple[float, float] | None = None
    reverb_wet: float = 0.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.source_end_s - self.source_start_s

    def to_dict(self) -> dict:
        return {**asdict(self), "duration_s": round(self.duration_s, 2)}


@dataclass
class Arrangement:
    segments: list[Segment] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments)

    def to_dict(self) -> dict:
        return {
            "duration_s": round(self.duration_s, 2),
            "segments": [s.to_dict() for s in self.segments],
        }


class BarGrid:
    """The track's bar lines, so edits land musically."""

    def __init__(self, tempo_bpm: float, first_beat_s: float, duration_s: float) -> None:
        self.tempo_bpm = tempo_bpm
        self.first_beat_s = first_beat_s
        self.duration_s = duration_s
        self.beat_s = 60.0 / tempo_bpm
        self.bar_s = self.beat_s * BEATS_PER_BAR

    @classmethod
    def from_audio(cls, path: str | Path) -> "BarGrid":
        track = Track.load(path)
        y = np.ascontiguousarray(track.mono(), dtype=np.float32)
        tempo, beats = librosa.beat.beat_track(y=y, sr=track.sample_rate, trim=False)
        times = librosa.frames_to_time(beats, sr=track.sample_rate)
        first = float(times[0]) if len(times) else 0.0
        return cls(float(np.atleast_1d(tempo)[0]), first, track.duration_s)

    def snap(self, seconds: float) -> float:
        """Nearest bar line, clamped inside the track."""
        bars = round((seconds - self.first_beat_s) / self.bar_s)
        snapped = self.first_beat_s + bars * self.bar_s
        return float(min(max(snapped, 0.0), self.duration_s))

    def bars(self, count: float) -> float:
        return count * self.bar_s


def _sweep(samples: np.ndarray, sample_rate: int, low: float, high: float, highpass: bool) -> np.ndarray:
    """Blend between fixed-cutoff copies to fake cutoff automation."""
    cutoffs = np.geomspace(max(low, 20.0), max(high, 21.0), SWEEP_STEPS)
    rendered = []
    for cutoff in cutoffs:
        stage = HighpassFilter(cutoff_frequency_hz=float(cutoff)) if highpass else \
            LowpassFilter(cutoff_frequency_hz=float(cutoff))
        rendered.append(np.asarray(
            Pedalboard([stage])(samples, sample_rate, reset=True), dtype=np.float32
        ))

    out = np.zeros_like(samples, dtype=np.float32)
    n = len(samples)
    position = np.linspace(0.0, SWEEP_STEPS - 1, n)
    index = np.floor(position).astype(int)
    frac = (position - index).astype(np.float32)
    index = np.clip(index, 0, SWEEP_STEPS - 2)

    if samples.ndim == 2:
        frac = frac[:, None]
    stacked = np.stack(rendered)
    lower = stacked[index, np.arange(n)]
    upper = stacked[index + 1, np.arange(n)]
    out = (lower * (1.0 - frac) + upper * frac).astype(np.float32)
    return out


def _shape(segment: Segment, samples: np.ndarray, sample_rate: int) -> np.ndarray:
    out = samples.astype(np.float32)

    stages = []
    if segment.highpass_hz is not None:
        # Two poles: one is 6 dB/octave and barely removes a bassline. They must
        # be separate instances -- pedalboard refuses the same object twice.
        stages += [
            HighpassFilter(cutoff_frequency_hz=segment.highpass_hz),
            HighpassFilter(cutoff_frequency_hz=segment.highpass_hz),
        ]
    if segment.reverb_wet > 0:
        stages.append(Reverb(room_size=0.35, wet_level=segment.reverb_wet,
                             dry_level=1.0 - segment.reverb_wet))
    if stages:
        out = np.asarray(Pedalboard(stages)(out, sample_rate, reset=True), dtype=np.float32)

    if segment.highpass_sweep:
        out = _sweep(out, sample_rate, *segment.highpass_sweep, highpass=True)
    if segment.lowpass_sweep:
        out = _sweep(out, sample_rate, *segment.lowpass_sweep, highpass=False)

    if segment.gain_db:
        out = out * float(10.0 ** (segment.gain_db / 20.0))

    for seconds, at_start in ((segment.fade_in_s, True), (segment.fade_out_s, False)):
        n = int(seconds * sample_rate)
        if n <= 0 or n > len(out):
            continue
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        ramp = ramp if at_start else ramp[::-1]
        if out.ndim == 2:
            ramp = ramp[:, None]
        if at_start:
            out[:n] *= ramp
        else:
            out[-n:] *= ramp

    return out.astype(np.float32)


def render_arrangement(
    source: str | Path, arrangement: Arrangement, out_path: str | Path
) -> dict:
    """Cut, process and join the segments into one file."""
    track = Track.load(source)
    sr = track.sample_rate
    fade = int(JOIN_CROSSFADE_S * sr)

    pieces: list[np.ndarray] = []
    for segment in arrangement.segments:
        a = int(max(segment.source_start_s, 0.0) * sr)
        b = int(min(segment.source_end_s, track.duration_s) * sr)
        if b - a < fade * 2:
            continue
        pieces.append(_shape(segment, track.samples[a:b], sr))

    if not pieces:
        raise ValueError("arrangement produced no audio")

    out = pieces[0]
    for piece in pieces[1:]:
        n = min(fade, len(out), len(piece))
        if n <= 0:
            out = np.concatenate([out, piece])
            continue
        # Equal-power crossfade, so the join does not dip in level.
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        fade_out, fade_in = np.cos(ramp * np.pi / 2), np.sin(ramp * np.pi / 2)
        if out.ndim == 2:
            fade_out, fade_in = fade_out[:, None], fade_in[:, None]
        joined = out[-n:] * fade_out + piece[:n] * fade_in
        out = np.concatenate([out[:-n], joined, piece[n:]])

    peak = float(np.max(np.abs(out)))
    if peak > 0.999:
        out = out * (0.999 / peak)

    written = Track(path=Path(source), samples=out.astype(np.float32),
                    sample_rate=sr).write(out_path)
    return {
        "output": str(written),
        "duration_s": round(len(out) / sr, 2),
        "source_duration_s": round(track.duration_s, 2),
        "segments": len(pieces),
    }


# The bridge is the hook's own material with the low end taken out. That is
# what a breakdown is: the same song, stripped, so its return means something.
BRIDGE_HIGHPASS_HZ = 230.0
BRIDGE_REVERB_WET = 0.16
BRIDGE_GAIN_DB = -1.5

# The riser: a highpass climbing until almost nothing is left, so the full-band
# downbeat lands as a release.
RISER_BARS = 2.0
RISER_SWEEP_HZ = (240.0, 2600.0)

DEFAULT_BRIDGE_BARS = 12.0
DEFAULT_TARGET_S = 170.0


def _longest(sections: list, label: str):
    matching = [s for s in sections if s.label == label]
    return max(matching, key=lambda s: s.end_s - s.start_s) if matching else None


def plan_extension(
    structure,
    grid: BarGrid,
    target_duration_s: float = DEFAULT_TARGET_S,
    bridge_bars: float = DEFAULT_BRIDGE_BARS,
) -> tuple[Arrangement, list[str]]:
    """Lay out a longer arrangement with a bridge and a drop.

    Returns the arrangement and a plain-language account of each decision, so
    the edit can be argued with rather than just accepted.
    """
    sections = structure.sections
    if len(sections) < 3:
        raise ValueError("not enough sections to rearrange")

    hook_label = max(structure.repetition, key=lambda k: structure.repetition[k])
    hook = _longest(sections, hook_label)

    # The bridge comes from the longest section that is not the hook, so the
    # breakdown has something of its own to say.
    others = [s for s in sections if s.label != hook_label]
    bridge_source = max(others, key=lambda s: s.end_s - s.start_s) if others else hook

    # Treat a short, quiet final section as an outro and keep it last.
    last = sections[-1]
    body_end = last.start_s if (last.duration_s < 12.0 and len(sections) > 3) else last.end_s
    outro = last if body_end != last.end_s else None

    why: list[str] = [
        f"Hook is **{hook_label}** — it recurs {structure.repetition[hook_label]} times; "
        f"the longest instance ({hook.duration_s:.0f}s at {hook.start_s:.0f}s) is reused.",
        f"Bridge is built from **{bridge_source.label}**, the longest non-hook section, "
        f"highpassed at {BRIDGE_HIGHPASS_HZ:.0f} Hz so the bass and kick drop out.",
    ]

    segments: list[Segment] = [
        Segment(
            source_start_s=grid.snap(0.0),
            source_end_s=grid.snap(body_end),
            role="original body",
        )
    ]

    bridge_start = grid.snap(bridge_source.start_s)
    bridge_len = min(grid.bars(bridge_bars), bridge_source.duration_s)
    bridge_len = max(bridge_len, grid.bars(4))
    segments.append(Segment(
        source_start_s=bridge_start,
        source_end_s=grid.snap(bridge_start + bridge_len),
        role="bridge (low end removed)",
        highpass_hz=BRIDGE_HIGHPASS_HZ,
        reverb_wet=BRIDGE_REVERB_WET,
        gain_db=BRIDGE_GAIN_DB,
        fade_in_s=0.25,
    ))

    riser_start = grid.snap(bridge_start + bridge_len)
    riser_end = grid.snap(riser_start + grid.bars(RISER_BARS))
    if riser_end > riser_start:
        segments.append(Segment(
            source_start_s=riser_start,
            source_end_s=riser_end,
            role="riser into the drop",
            highpass_sweep=RISER_SWEEP_HZ,
            reverb_wet=0.2,
            fade_out_s=0.05,
        ))
        why.append(
            f"A {RISER_BARS:.0f}-bar riser sweeps the highpass "
            f"{RISER_SWEEP_HZ[0]:.0f} Hz → {RISER_SWEEP_HZ[1]:.0f} Hz, so almost nothing "
            "is left when the full band returns."
        )

    # The drop: the hook back at full bandwidth.
    segments.append(Segment(
        source_start_s=grid.snap(hook.start_s),
        source_end_s=grid.snap(hook.end_s),
        role=f"drop — {hook_label} returns full",
    ))
    why.append(f"The drop is **{hook_label}** at full bandwidth straight after the riser.")

    # Fill toward the target by repeating the hook, then close with the outro.
    outro_len = outro.duration_s if outro else 0.0
    guard = 0
    while sum(s.duration_s for s in segments) + outro_len < target_duration_s and guard < 4:
        segments.append(Segment(
            source_start_s=grid.snap(hook.start_s),
            source_end_s=grid.snap(hook.end_s),
            role=f"{hook_label} again",
        ))
        guard += 1
    if guard:
        why.append(
            f"{hook_label} repeats {guard} more time(s) to reach "
            f"{target_duration_s / 60:.1f} min."
        )

    if outro is not None:
        segments.append(Segment(
            source_start_s=grid.snap(outro.start_s),
            source_end_s=outro.end_s,
            role="outro",
            fade_out_s=0.8,
        ))
        why.append(f"Original outro **{outro.label}** closes it, with a short fade.")

    return Arrangement(segments=segments), why
