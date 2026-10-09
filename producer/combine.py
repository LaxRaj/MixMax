"""Put a vocal over a beat.

This is the stage the progress view calls `backing`, and the one nothing else
in the pipeline could clear. It is a mix problem, not a mastering one: line the
two up, set the balance, and keep the beat out of the way of the words.

It does not write music. The beat has to come from somewhere -- written,
bought or licensed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as pyln
from scipy.signal import correlate

from producer.audio import Track

# How far apart the two takes might start. Wider costs time and invites a
# spurious peak somewhere in the song.
MAX_OFFSET_S = 30.0

# A rap vocal sits above the instrumental. This is the gap in integrated
# loudness, and it is the single most consequential number here.
DEFAULT_VOCAL_OVER_BEAT_DB = 4.0

# Ducking: how far the beat steps back under the voice, and how quickly.
DEFAULT_DUCK_DB = 2.5
DUCK_ATTACK_S = 0.015
DUCK_RELEASE_S = 0.20

# Confidence is peak-to-sidelobe: how far the best lag stands above the best
# *other* lag, once a window either side is excluded. A periodic signal
# correlated against another periodic signal produces a row of near-equal
# peaks, and a metric that compares the peak to the mean cannot tell that
# apart from a real lock — it reports high confidence on a wrong answer.
ALIGNMENT_CONFIDENCE_FLOOR = 0.35

# Lags within this of the peak belong to the same peak, not a rival one.
SIDELOBE_EXCLUSION_S = 1.0


@dataclass
class Alignment:
    offset_s: float
    confidence: float
    method: str

    @property
    def trustworthy(self) -> bool:
        return self.confidence >= ALIGNMENT_CONFIDENCE_FLOOR

    def to_dict(self) -> dict:
        return {**asdict(self), "trustworthy": self.trustworthy}


def _onset_envelope(path: Path, sr: int = 22050) -> tuple[np.ndarray, int]:
    """Onset strength at a low rate — enough to line two takes up."""
    y, loaded = librosa.load(str(path), sr=sr, mono=True)
    hop = 256
    env = librosa.onset.onset_strength(y=y, sr=loaded, hop_length=hop)
    env = env - env.mean()
    norm = np.linalg.norm(env)
    return (env / norm if norm > 0 else env), loaded // hop


def find_offset(vocal: str | Path, beat: str | Path) -> Alignment:
    """Where the vocal starts relative to the beat, in seconds.

    A rap vocal's syllables land on the beat's grid, so their onset envelopes
    correlate when the two are lined up. Confidence is the peak's height over
    the rest of the correlation, and a weak peak is reported rather than used.
    """
    vocal_env, rate = _onset_envelope(Path(vocal))
    beat_env, _ = _onset_envelope(Path(beat))
    if vocal_env.size < 4 or beat_env.size < 4:
        return Alignment(0.0, 0.0, "too short to align")

    correlation = correlate(beat_env, vocal_env, mode="full")
    lags = np.arange(-len(vocal_env) + 1, len(beat_env))
    window = int(MAX_OFFSET_S * rate)
    keep = np.abs(lags) <= window
    correlation, lags = correlation[keep], lags[keep]
    if correlation.size == 0:
        return Alignment(0.0, 0.0, "no overlap")

    peak = int(np.argmax(correlation))
    best = float(correlation[peak])
    if best <= 0:
        return Alignment(0.0, 0.0, "no correlation")

    # Peak-to-sidelobe: ignore everything near the peak, then ask how good the
    # best rival is. Two periodic signals give rivals almost as strong as the
    # winner, which is exactly the case that must not read as confident.
    exclude = int(SIDELOBE_EXCLUSION_S * rate)
    rivals = np.concatenate([correlation[: max(peak - exclude, 0)],
                             correlation[peak + exclude:]])
    if rivals.size == 0:
        return Alignment(float(lags[peak]) / rate, 0.0, "nothing to compare against")

    runner_up = float(np.max(rivals))
    confidence = float(np.clip(1.0 - max(runner_up, 0.0) / best, 0.0, 1.0))

    return Alignment(float(lags[peak]) / rate, round(confidence, 3), "onset correlation")


# Grid alignment searches within half a bar either way. Beyond that the answer
# is a musical choice (which bar the verse starts on), not a measurement.
GRID_SEARCH_BARS = 0.5
GRID_STEP_S = 0.005
GRID_SUBDIVISION = 4          # sixteenths
MIN_GRID_PERIODICITY = 0.3


def measure_pulse(y: np.ndarray, sr: int) -> tuple[float, np.ndarray, float]:
    """Tempo, beat frames, and how periodic the onsets actually are.

    A beat tracker always returns a tempo. Periodicity is the onset envelope's
    autocorrelation at that tempo's lag, and it is what says whether there was
    a pulse to find: drums score around 0.45 and up, an a cappella around 0.1.
    The tempo is returned as tracked, not folded.
    """
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])

    centred = onset_env - onset_env.mean()
    auto = librosa.autocorrelate(centred, max_size=int(4 * sr / 512))
    auto = auto / (auto[0] + 1e-9)
    lag = int(round((60.0 / tempo) * sr / 512)) if tempo > 0 else 0
    periodicity = float(auto[lag]) if 0 < lag < len(auto) else 0.0
    return tempo, beats, periodicity


def align_to_grid(vocal: str | Path, beat: str | Path) -> Alignment:
    """Lock the vocal to the beat's own grid.

    Correlating two onset envelopes fails when both are periodic. But a beat
    with drums has a grid that *is* measurable, and a vocal recorded over it
    puts its syllables on that grid. So this scores candidate offsets by how
    closely the vocal's onsets land on the beat's subdivisions.

    It searches half a bar either way. Which *bar* a verse starts on is an
    arrangement decision; where it sits inside the bar is a measurement.
    """
    beat_path, vocal_path = Path(beat), Path(vocal)
    beat_y, sr = librosa.load(str(beat_path), sr=22050, mono=True)
    # Only trust this when the beat really has a pulse.
    tempo, beats, periodicity = measure_pulse(beat_y, sr)
    if periodicity < MIN_GRID_PERIODICITY or len(beats) < 8:
        return Alignment(0.0, 0.0, f"beat has no usable grid (periodicity {periodicity:.2f})")

    grid_times = librosa.frames_to_time(beats, sr=sr)
    step = float(np.median(np.diff(grid_times))) / GRID_SUBDIVISION
    fine = np.arange(grid_times[0], grid_times[-1], step)

    vocal_y, vsr = librosa.load(str(vocal_path), sr=22050, mono=True)
    onsets = librosa.onset.onset_detect(y=vocal_y, sr=vsr, units="time")
    if onsets.size < 16:
        return Alignment(0.0, 0.0, "not enough vocal onsets to place")

    span = GRID_SEARCH_BARS * (60.0 / tempo) * 4.0
    candidates = np.arange(-span, span + GRID_STEP_S, GRID_STEP_S)
    scores = np.empty_like(candidates)
    for i, candidate in enumerate(candidates):
        shifted = onsets + candidate
        inside = shifted[(shifted >= fine[0]) & (shifted <= fine[-1])]
        if inside.size == 0:
            scores[i] = 1.0
            continue
        nearest = np.abs(inside[:, None] - fine[None, :]).min(axis=1)
        scores[i] = float(np.mean(nearest)) / step   # 0 = dead on, 0.5 = worst

    best = int(np.argmin(scores))
    # Confidence: how much tighter the best fit is than the average candidate.
    confidence = float(np.clip((np.mean(scores) - scores[best]) / (np.mean(scores) + 1e-9) * 3.0,
                               0.0, 1.0))
    return Alignment(
        round(float(candidates[best]), 4), round(confidence, 3),
        f"grid lock at {tempo:.1f} BPM (periodicity {periodicity:.2f})",
    )


def _as_stereo(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return np.stack([samples, samples], axis=1).astype(np.float32)
    if samples.shape[1] == 1:
        return np.repeat(samples, 2, axis=1).astype(np.float32)
    return samples[:, :2].astype(np.float32)


def _integrated(samples: np.ndarray, sample_rate: int) -> float:
    meter = pyln.Meter(sample_rate)
    if len(samples) < meter.block_size * sample_rate:
        return float("-inf")
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(meter.integrated_loudness(samples))


def _duck(beat: np.ndarray, vocal: np.ndarray, sample_rate: int, depth_db: float) -> np.ndarray:
    """Step the beat back wherever the vocal is present.

    A real sidechain compressor, done by hand: pedalboard has no key input, and
    for intelligibility a smoothed envelope follower is what matters anyway.
    """
    if depth_db <= 0:
        return beat

    key = np.abs(vocal).mean(axis=1) if vocal.ndim == 2 else np.abs(vocal)
    # One-pole follower, fast to duck and slow to let go, so it does not chatter.
    attack = np.exp(-1.0 / (DUCK_ATTACK_S * sample_rate))
    release = np.exp(-1.0 / (DUCK_RELEASE_S * sample_rate))
    envelope = np.empty_like(key)
    running = 0.0
    for i, value in enumerate(key):
        coefficient = attack if value > running else release
        running = coefficient * running + (1.0 - coefficient) * value
        envelope[i] = running

    peak = float(envelope.max()) or 1e-9
    amount = np.clip(envelope / peak, 0.0, 1.0)
    gain = 10.0 ** ((-depth_db * amount) / 20.0)
    return (beat * gain[:, None]).astype(np.float32)


def combine(
    vocal: str | Path,
    beat: str | Path,
    out_path: str | Path,
    offset_s: float | None = None,
    vocal_over_beat_db: float = DEFAULT_VOCAL_OVER_BEAT_DB,
    duck_db: float = DEFAULT_DUCK_DB,
) -> dict:
    """Mix `vocal` over `beat` and write the result."""
    vocal_track, beat_track = Track.load(vocal), Track.load(beat)
    sr = beat_track.sample_rate

    if vocal_track.sample_rate != sr:
        resampled = librosa.resample(
            np.ascontiguousarray(vocal_track.mono(), dtype=np.float32),
            orig_sr=vocal_track.sample_rate, target_sr=sr, res_type="soxr_hq",
        )
        vocal_samples = _as_stereo(resampled)
    else:
        vocal_samples = _as_stereo(vocal_track.samples)
    beat_samples = _as_stereo(beat_track.samples)

    if offset_s is not None:
        alignment = Alignment(float(offset_s), 1.0, "given")
    else:
        detected = align_to_grid(vocal, beat)
        if not detected.trustworthy:
            detected = find_offset(vocal, beat)
        if detected.trustworthy:
            alignment = detected
        else:
            # Falling back to zero is the safe default: a vocal cut in a DAW
            # against the beat starts with it. Applying a weak detection would
            # silently move the whole take.
            alignment = Alignment(
                0.0, detected.confidence,
                f"{detected.method} too weak ({detected.confidence:.2f}) — "
                f"suggested {detected.offset_s:+.2f}s, used 0",
            )
    shift = int(round(alignment.offset_s * sr))

    # Lay both on one timeline, padding whichever starts later.
    vocal_start = max(shift, 0)
    beat_start = max(-shift, 0)
    length = max(vocal_start + len(vocal_samples), beat_start + len(beat_samples))

    vocal_laid = np.zeros((length, 2), dtype=np.float32)
    beat_laid = np.zeros((length, 2), dtype=np.float32)
    vocal_laid[vocal_start:vocal_start + len(vocal_samples)] = vocal_samples
    beat_laid[beat_start:beat_start + len(beat_samples)] = beat_samples

    # Balance by integrated loudness, which is what the ear is judging.
    vocal_lufs = _integrated(vocal_laid, sr)
    beat_lufs = _integrated(beat_laid, sr)
    if np.isfinite(vocal_lufs) and np.isfinite(beat_lufs):
        wanted = (vocal_lufs - vocal_over_beat_db) - beat_lufs
        beat_laid = (beat_laid * (10.0 ** (wanted / 20.0))).astype(np.float32)
    else:
        wanted = 0.0

    ducked = _duck(beat_laid, vocal_laid, sr, duck_db)
    mixed = vocal_laid + ducked

    peak = float(np.max(np.abs(mixed)))
    headroom_trim = 0.0
    if peak > 0.89:
        # Leave room for mastering rather than handing it a file at full scale.
        headroom_trim = 20 * np.log10(0.89 / peak)
        mixed = (mixed * (0.89 / peak)).astype(np.float32)

    written = Track(path=Path(vocal), samples=mixed, sample_rate=sr).write(out_path)

    return {
        "output": str(written),
        "alignment": alignment.to_dict(),
        "duration_s": round(len(mixed) / sr, 2),
        "vocal_lufs": round(vocal_lufs, 2) if np.isfinite(vocal_lufs) else None,
        "beat_lufs": round(beat_lufs, 2) if np.isfinite(beat_lufs) else None,
        "beat_gain_db": round(float(wanted), 2),
        "vocal_over_beat_db": vocal_over_beat_db,
        "duck_db": duck_db,
        "headroom_trim_db": round(headroom_trim, 2),
    }
