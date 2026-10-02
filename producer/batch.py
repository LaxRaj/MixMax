"""Batch processing — run the whole pipeline over a folder and report on it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from producer.analysis import analyze_vocal
from producer.audio import AUDIO_SUFFIXES
from producer.mastering import master_track
from producer.mix import mix_vocal
from producer.qa import run_qa

BATCH_SUFFIXES = (".wav", ".mp3")


@dataclass
class PipelineResult:
    """One file's trip through analyze -> mix -> master -> qa."""

    filename: str
    analysis: dict
    qa: dict
    output_path: Path | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"filename": self.filename, "analysis": self.analysis, "qa": self.qa}


def _discover(input_dir: Path) -> list[Path]:
    """Audio files in `input_dir`, sorted by filename for a stable report."""
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in BATCH_SUFFIXES
    )


def _process_one(path: Path, reference: Path, out_dir: Path) -> PipelineResult:
    out_path = out_dir / f"{path.stem}_mastered.wav"
    try:
        analysis = analyze_vocal(path)
        with TemporaryDirectory() as tmp:
            premixed = Path(tmp) / f"{path.stem}_premixed.wav"
            mix_vocal(path, premixed)
            master_track(premixed, reference, out_path)
        return PipelineResult(
            filename=path.name,
            analysis=analysis,
            qa=run_qa(out_path),
            output_path=out_path,
        )
    except Exception as exc:  # one bad file shouldn't sink the whole batch
        return PipelineResult(
            filename=path.name,
            analysis={},
            qa={"pass": False, "flags": [f"pipeline error: {exc}"]},
            error=str(exc),
        )


def _cell(value: object, spec: str = "") -> str:
    """Render a possibly-missing metric for the markdown table."""
    if value is None or value == {}:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)) and spec:
        return format(value, spec)
    return str(value)


def build_report(results: list[PipelineResult]) -> str:
    """The markdown report: a summary line, then one row per file."""
    total = len(results)
    passed = sum(1 for r in results if r.qa.get("pass"))

    lines = [
        "# Batch QA report",
        "",
        f"**{passed}/{total} passed automated QA**",
        "",
        "| File | Tempo (BPM) | LUFS | Clipping | Mono-OK | QA Pass |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            "| {file} | {tempo} | {lufs} | {clip} | {mono} | {verdict} |".format(
                file=r.filename,
                tempo=_cell(r.analysis.get("tempo_bpm"), ".1f"),
                lufs=_cell(r.qa.get("lufs"), ".1f"),
                clip=_cell(r.qa.get("clipping_detected")),
                mono=_cell(r.qa.get("mono_compatible")),
                verdict="PASS" if r.qa.get("pass") else "FAIL",
            )
        )

    flagged = [r for r in results if r.qa.get("flags")]
    if flagged:
        lines += ["", "## Flags", ""]
        for r in flagged:
            lines.append(f"- **{r.filename}**")
            for flag in r.qa["flags"]:
                lines.append(f"  - {flag}")

    lines += [
        "",
        "## Subjective verdict",
        "",
        "Automated QA only checks loudness, clipping and phase. Listen to each",
        "mastered file and record whether it sounds release-ready.",
        "",
        "| File | Sounds release-ready? | Notes |",
        "| --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r.filename} |  |  |")

    return "\n".join(lines) + "\n"


def run_batch(input_dir: str | Path, reference: str | Path, out_dir: str | Path) -> list[dict]:
    """Run the full pipeline over every audio file in `input_dir`."""
    input_dir, reference, out_dir = Path(input_dir), Path(reference), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [_process_one(p, reference, out_dir) for p in _discover(input_dir)]
    (out_dir / "report.md").write_text(build_report(results))
    return [r.to_dict() for r in results]
