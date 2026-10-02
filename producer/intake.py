"""Intake — validate raw vocals and scaffold a comparison workspace.

This is the front door. Friends send whatever their phone produced, so before
anyone spends money on LANDR credits or an evening listening, this gate answers
one question per file: *is this recording good enough to be worth comparing?*

Nothing here improves a recording. It reports what's wrong while re-recording
is still cheap.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from producer.audio import Track

# What we accept on the way in. The m4a family needs transcoding first.
NATIVE_SUFFIXES = (".wav", ".flac", ".aiff", ".aif", ".ogg", ".mp3", ".caf")
TRANSCODE_SUFFIXES = (".m4a", ".mp4", ".aac", ".m4r")
INTAKE_SUFFIXES = NATIVE_SUFFIXES + TRANSCODE_SUFFIXES

TARGET_SAMPLE_RATE = 44100
STANDARD_SUBTYPE = "PCM_24"   # upload-grade; LANDR and friends all take 24-bit WAV

# Thresholds. Tuned to be actionable, not pedantic.
MIN_DURATION_S = 2.0          # below this, nothing downstream works
SHORT_DURATION_S = 10.0       # below this, loudness/tempo estimates get shaky
SILENT_PEAK_DBFS = -40.0      # effectively an empty file
QUIET_PEAK_DBFS = -12.0       # usable but will need a lot of makeup gain
CLIP_RUN_SAMPLES = 3          # consecutive full-scale samples = real clipping
DC_OFFSET_LIMIT = 0.001
NOISY_FLOOR_DBFS = -45.0
LOW_SNR_DB = 30.0

# A noise floor can only be measured where the performance actually stops.
# On continuous singing the quietest frames are still *signal*, and treating
# them as noise invents a problem on every clean file. So unless the quiet
# frames sit this far below the median, we report "unknown" rather than guess.
SILENCE_GAP_MIN_DB = 12.0
LONG_SILENCE_S = 2.0
DUAL_MONO_CORRELATION = 0.9999

READY, CAUTION, BLOCKED = "ready", "caution", "blocked"


@dataclass
class IntakeResult:
    """One raw vocal's trip through the gate."""

    source_file: str
    slug: str
    verdict: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    standardized_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class IntakeError(RuntimeError):
    """A file we cannot read at all."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "track"


def _db(value: float) -> float:
    return float(20.0 * np.log10(max(float(value), 1e-12)))


def transcode_to_wav(path: Path, out_path: Path) -> Path:
    """Convert m4a/aac to WAV using macOS's built-in afconvert."""
    if shutil.which("afconvert") is None:
        raise IntakeError(
            f"{path.name} is {path.suffix}, which libsndfile cannot read, and "
            "`afconvert` is unavailable. Convert it to WAV or MP3 first "
            "(on macOS: afconvert -f WAVE -d LEI24 in.m4a out.wav)."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI24", str(path), str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise IntakeError(f"Could not convert {path.name}: {result.stderr.strip() or 'afconvert failed'}")
    return out_path


def load_for_intake(path: Path, scratch_dir: Path) -> Track:
    """Load any accepted format, transcoding first when libsndfile can't."""
    path = Path(path)
    if path.suffix.lower() in TRANSCODE_SUFFIXES:
        path = transcode_to_wav(path, scratch_dir / f"{slugify(path.stem)}.wav")
    try:
        return Track.load(path)
    except Exception as exc:  # unreadable, truncated, or not actually audio
        raise IntakeError(f"Could not read {path.name}: {exc}") from exc


def _clipping_runs(mono: np.ndarray) -> int:
    """Count runs of consecutive full-scale samples.

    A single sample at full scale is usually harmless; a *run* is a flat-topped
    waveform, which means the recording was already distorted on the way in and
    no amount of mastering repairs it.
    """
    at_ceiling = np.abs(mono) >= 0.999
    if not at_ceiling.any():
        return 0
    padded = np.concatenate([[False], at_ceiling, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    lengths = edges[1::2] - edges[0::2]
    return int(np.sum(lengths >= CLIP_RUN_SAMPLES))


def _frame_rms_db(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    frame = max(1, int(0.05 * sample_rate))
    usable = len(mono) - (len(mono) % frame)
    if usable < frame:
        return np.array([_db(0.0)])
    frames = mono[:usable].reshape(-1, frame)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    return np.array([_db(v) for v in rms])


def _edge_silence_s(mono: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> tuple[float, float]:
    """Leading and trailing near-silence, in seconds."""
    frame_db = _frame_rms_db(mono, sample_rate)
    audible = np.flatnonzero(frame_db > threshold_db)
    if audible.size == 0:
        return round(len(mono) / sample_rate, 2), 0.0
    frame_s = 0.05
    leading = float(audible[0] * frame_s)
    trailing = float((len(frame_db) - 1 - audible[-1]) * frame_s)
    return round(leading, 2), round(trailing, 2)


def estimate_noise_floor(frame_db: np.ndarray) -> float | None:
    """The level of the quiet passages, or None when there are none.

    Returning None is the honest answer for a take with no gaps: we genuinely
    cannot separate room tone from a quietly-sung note.
    """
    if frame_db.size < 4:
        return None
    quiet = float(np.percentile(frame_db, 10))
    median = float(np.percentile(frame_db, 50))
    if median - quiet < SILENCE_GAP_MIN_DB:
        return None
    return quiet


def inspect(track: Track) -> dict:
    """Everything the gate needs to judge a recording."""
    mono = track.mono()
    frame_db = _frame_rms_db(mono, track.sample_rate)
    audible = frame_db[frame_db > -90.0]

    peak_dbfs = _db(np.max(np.abs(mono))) if mono.size else float("-inf")
    noise_floor = estimate_noise_floor(audible)
    leading, trailing = _edge_silence_s(mono, track.sample_rate)

    dual_mono = False
    if track.channels == 2:
        left, right = track.samples[:, 0], track.samples[:, 1]
        if left.std() > 0 and right.std() > 0:
            corr = float(np.corrcoef(left, right)[0, 1])
            dual_mono = bool(np.isfinite(corr) and corr > DUAL_MONO_CORRELATION)

    return {
        "duration_s": round(track.duration_s, 2),
        "sample_rate": track.sample_rate,
        "channels": track.channels,
        "peak_dbfs": round(peak_dbfs, 2),
        "clipping_runs": _clipping_runs(mono),
        "dc_offset": round(float(np.mean(mono)), 5) if mono.size else 0.0,
        "noise_floor_dbfs": round(noise_floor, 2) if noise_floor is not None else None,
        "snr_db": (
            round(peak_dbfs - noise_floor, 2)
            if noise_floor is not None and np.isfinite(peak_dbfs) else None
        ),
        "leading_silence_s": leading,
        "trailing_silence_s": trailing,
        "dual_mono": dual_mono,
    }


def judge(metrics: dict) -> tuple[str, list[str], list[str]]:
    """Turn metrics into a verdict plus plain-language reasons."""
    blockers: list[str] = []
    warnings: list[str] = []

    if metrics["duration_s"] < MIN_DURATION_S:
        blockers.append(f"Only {metrics['duration_s']}s long — too short to process.")
    elif metrics["duration_s"] < SHORT_DURATION_S:
        warnings.append(
            f"Only {metrics['duration_s']}s long. Loudness and tempo estimates get "
            "unreliable below ~10s, and it's a thin basis for judging a master."
        )

    if metrics["peak_dbfs"] < SILENT_PEAK_DBFS:
        blockers.append(f"Peaks at {metrics['peak_dbfs']} dBFS — effectively silent.")
    elif metrics["peak_dbfs"] < QUIET_PEAK_DBFS:
        warnings.append(
            f"Quiet: peaks at {metrics['peak_dbfs']} dBFS. Usable, but the makeup gain "
            "will lift the noise floor with it."
        )

    if metrics["clipping_runs"] > 0:
        blockers.append(
            f"{metrics['clipping_runs']} clipped region(s) in the raw recording. "
            "This distortion is baked in — no master removes it. Re-record with "
            "more headroom if you can."
        )

    if abs(metrics["dc_offset"]) > DC_OFFSET_LIMIT:
        warnings.append(
            f"DC offset of {metrics['dc_offset']}. Wastes headroom; a highpass fixes it "
            "(the mix chain's 80 Hz filter will)."
        )

    if metrics["noise_floor_dbfs"] is not None and metrics["noise_floor_dbfs"] > NOISY_FLOOR_DBFS:
        warnings.append(
            f"Noise floor at {metrics['noise_floor_dbfs']} dBFS — audible hiss or room tone. "
            "Compression will bring it forward."
        )

    if metrics["snr_db"] is not None and metrics["snr_db"] < LOW_SNR_DB:
        warnings.append(f"Signal-to-noise around {metrics['snr_db']} dB, which is low for a vocal.")

    if metrics["sample_rate"] < TARGET_SAMPLE_RATE:
        warnings.append(
            f"Recorded at {metrics['sample_rate']} Hz; it will be resampled up to "
            f"{TARGET_SAMPLE_RATE} Hz, which adds no detail back."
        )

    if metrics["leading_silence_s"] > LONG_SILENCE_S or metrics["trailing_silence_s"] > LONG_SILENCE_S:
        warnings.append(
            f"{metrics['leading_silence_s']}s silence at the start and "
            f"{metrics['trailing_silence_s']}s at the end. Trim it — it drags the "
            "loudness measurement down and wastes listeners' time."
        )

    if metrics["dual_mono"]:
        warnings.append("Stereo file with identical channels — it's really mono. Harmless, just larger.")

    verdict = BLOCKED if blockers else (CAUTION if warnings else READY)
    return verdict, blockers, warnings


def standardize(track: Track, out_path: Path) -> Path:
    """Write the one file every service receives.

    A fair comparison needs every service fed byte-identical input, so this is
    the canonical copy: 24-bit WAV at >= 44.1 kHz, otherwise untouched.
    """
    samples = track.samples
    sample_rate = track.sample_rate

    if sample_rate < TARGET_SAMPLE_RATE:
        axis = 0 if samples.ndim == 1 else 0
        resampled = librosa.resample(
            np.ascontiguousarray(samples.T if samples.ndim > 1 else samples, dtype=np.float32),
            orig_sr=sample_rate, target_sr=TARGET_SAMPLE_RATE, res_type="soxr_hq",
        )
        samples = resampled.T if samples.ndim > 1 else resampled
        sample_rate = TARGET_SAMPLE_RATE

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), samples, sample_rate, subtype=STANDARD_SUBTYPE)
    return out_path


def discover_raw(path: str | Path) -> list[Path]:
    """Accepted audio under `path` — a single file or a folder."""
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in INTAKE_SUFFIXES else []
    return sorted(
        p for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in INTAKE_SUFFIXES and not p.name.startswith(".")
    )


DEFAULT_SERVICES = ("landr",)


def run_intake(
    source: str | Path,
    workspace: str | Path,
    services: tuple[str, ...] = DEFAULT_SERVICES,
) -> list[dict]:
    """Validate every raw vocal under `source` and scaffold `workspace`."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    scratch = workspace / ".scratch"
    scratch.mkdir(exist_ok=True)

    results: list[IntakeResult] = []
    used_slugs: set[str] = set()

    for path in discover_raw(source):
        slug = slugify(path.stem)
        suffix = 2
        while slug in used_slugs:            # two friends, one song title
            slug, suffix = f"{slugify(path.stem)}-{suffix}", suffix + 1
        used_slugs.add(slug)

        try:
            track = load_for_intake(path, scratch)
        except IntakeError as exc:
            results.append(IntakeResult(
                source_file=str(path), slug=slug, verdict=BLOCKED, blockers=[str(exc)]
            ))
            continue

        metrics = inspect(track)
        verdict, blockers, warnings = judge(metrics)
        result = IntakeResult(
            source_file=str(path), slug=slug, verdict=verdict,
            blockers=blockers, warnings=warnings, metrics=metrics,
        )

        # Blocked files get no workspace slot -- nothing downstream should run.
        if verdict != BLOCKED:
            standardized = standardize(track, workspace / slug / "original.wav")
            result.standardized_path = str(standardized)

        results.append(result)

    shutil.rmtree(scratch, ignore_errors=True)

    payload = [r.to_dict() for r in results]
    (workspace / "intake.json").write_text(json.dumps(payload, indent=2) + "\n")
    (workspace / "INTAKE_REPORT.md").write_text(build_intake_report(results))
    (workspace / "MANIFEST.md").write_text(build_manifest(results, services))
    return payload


VERDICT_ICON = {READY: "✅", CAUTION: "⚠️", BLOCKED: "⛔"}


def build_intake_report(results: list[IntakeResult]) -> str:
    """Markdown: what came in, what's usable, and what to fix."""
    counts = {v: sum(1 for r in results if r.verdict == v) for v in (READY, CAUTION, BLOCKED)}

    lines = [
        "# Intake report",
        "",
        f"**{len(results)} file(s): {counts[READY]} ready, "
        f"{counts[CAUTION]} usable with caveats, {counts[BLOCKED]} blocked.**",
        "",
        "| File | Verdict | Length | Rate | Ch | Peak | Noise floor | SNR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        m = r.metrics
        lines.append(
            "| `{name}` | {icon} {verdict} | {dur} | {sr} | {ch} | {peak} | {nf} | {snr} |".format(
                name=Path(r.source_file).name,
                icon=VERDICT_ICON[r.verdict], verdict=r.verdict,
                dur=f"{m['duration_s']}s" if m else "—",
                sr=f"{m['sample_rate'] / 1000:.1f}k" if m else "—",
                ch=m.get("channels", "—") if m else "—",
                peak=f"{m['peak_dbfs']} dB" if m else "—",
                nf=f"{m['noise_floor_dbfs']} dB" if m and m.get("noise_floor_dbfs") is not None else "—",
                snr=f"{m['snr_db']} dB" if m and m.get("snr_db") is not None else "—",
            )
        )

    blocked = [r for r in results if r.verdict == BLOCKED]
    if blocked:
        lines += ["", "## ⛔ Blocked — not written to the workspace", ""]
        for r in blocked:
            lines.append(f"**{Path(r.source_file).name}**")
            lines += [f"- {b}" for b in r.blockers]
            lines.append("")

    flagged = [r for r in results if r.warnings]
    if flagged:
        lines += ["## ⚠️ Worth knowing before you spend credits", ""]
        for r in flagged:
            lines.append(f"**{Path(r.source_file).name}**")
            lines += [f"- {w}" for w in r.warnings]
            lines.append("")

    lines += [
        "## What this does and doesn't do",
        "",
        "Nothing here improves a recording. Every file that passed was copied",
        "verbatim to `<slug>/original.wav` as 24-bit WAV — resampled only if it",
        "arrived below 44.1 kHz. That single file is what every service receives,",
        "so the comparison stays fair.",
        "",
        "Clipping and a high noise floor are the two things mastering cannot fix.",
        "If either is flagged above, re-recording beats any amount of processing.",
    ]
    return "\n".join(lines) + "\n"


def build_manifest(results: list[IntakeResult], services: tuple[str, ...]) -> str:
    """The upload checklist — the one manual step in the loop."""
    usable = [r for r in results if r.verdict != BLOCKED]

    lines = [
        "# Upload checklist",
        "",
        "LANDR has no API we can drive, so this step is manual. For each track",
        "below, upload `original.wav`, download the result, and save it under the",
        "exact filename given — that name is how `producer benchmark` labels it.",
        "",
        "Upload the **same** `original.wav` to every service. Feeding one service",
        "a different file invalidates the comparison.",
        "",
    ]
    if not usable:
        lines.append("_Nothing passed intake yet._")
        return "\n".join(lines) + "\n"

    for r in usable:
        folder = Path(r.standardized_path).parent if r.standardized_path else Path(r.slug)
        lines += [f"## {r.slug}", "", f"Upload: `{folder}/original.wav`", ""]
        for service in services:
            lines.append(f"- [ ] **{service}** → save as `{folder}/{service}.wav`")
        lines += [f"- [ ] **producer** → `producer render` writes `{folder}/producer.wav`", ""]

    lines += [
        "## Then",
        "",
        "```bash",
        "producer render    --workspace <workspace> --reference <reference.wav>",
        "producer benchmark --versions-dir <workspace>/<slug> --out <workspace>/<slug>/benchmark.md",
        "producer blindtest --versions-dir <workspace>/<slug> --out-dir <workspace>/<slug>/blind",
        "```",
        "",
        "`original.wav` stays in the folder on purpose: it rides through the blind",
        "test as a control. If listeners rank the unmastered take first, the",
        "problem is the chain, not the recording.",
    ]
    return "\n".join(lines) + "\n"
