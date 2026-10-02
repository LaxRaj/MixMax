"""The `producer` command-line interface."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import click

from producer.analysis import analyze_vocal
from producer.batch import run_batch
from producer.audio import human_size
from producer.mastering import master_track
from producer.mix import mix_vocal
from producer.qa import run_qa

EXISTING_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
OUT_FILE = click.Path(dir_okay=False, path_type=Path)
IN_DIR = click.Path(exists=True, file_okay=False, path_type=Path)
OUT_DIR = click.Path(file_okay=False, path_type=Path)


@click.group()
@click.version_option(package_name="producer")
def cli() -> None:
    """Analyze, mix, master and QA-gate a raw vocal."""


@cli.command()
@click.option("--vocal", required=True, type=EXISTING_FILE, help="Raw vocal to master.")
@click.option("--reference", required=True, type=EXISTING_FILE, help="Reference track to match.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
@click.option("--premix", is_flag=True, help="Run the vocal mix chain before mastering.")
def master(vocal: Path, reference: Path, out_path: Path, premix: bool) -> None:
    """Master VOCAL against REFERENCE and write the result to OUT."""
    source = vocal
    with tempfile.TemporaryDirectory() as tmp:
        if premix:
            source = Path(tmp) / f"{vocal.stem}_premixed.wav"
            mix_vocal(vocal, source)
            click.echo("Pre-mixed through the vocal chain.")
        result = master_track(source, reference, out_path)

    click.echo(f"Mastered -> {result} ({human_size(result.stat().st_size)})")
    echo_qa(run_qa(result))


@cli.command()
@click.option("--vocal", required=True, type=EXISTING_FILE, help="Vocal to analyze.")
@click.option("--out", "out_path", type=OUT_FILE, help="Also write the JSON here.")
def analyze(vocal: Path, out_path: Path | None) -> None:
    """Report tempo, pitch range and dynamic range for VOCAL as JSON."""
    analysis = analyze_vocal(vocal)
    payload = json.dumps(analysis, indent=2)
    click.echo(payload)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n")


@cli.command()
@click.option("--vocal", required=True, type=EXISTING_FILE, help="Vocal to mix.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
def mix(vocal: Path, out_path: Path) -> None:
    """Run VOCAL through the vocal mix chain and write it to OUT."""
    result = mix_vocal(vocal, out_path)
    click.echo(f"Mixed -> {result} ({human_size(result.stat().st_size)})")


def echo_qa(report: dict) -> None:
    """Print a QA report as a verdict line plus one line per failed check."""
    verdict = "PASS" if report["pass"] else "FAIL"
    click.echo(f"QA {verdict} — {report['lufs']} LUFS")
    for flag in report["flags"]:
        click.echo(f"  - {flag}")


@cli.command()
@click.option("--file", "file_path", required=True, type=EXISTING_FILE, help="Audio to check.")
def qa(file_path: Path) -> None:
    """Run the automated QA gate against FILE and print the report as JSON."""
    click.echo(json.dumps(run_qa(file_path), indent=2))


@cli.command()
@click.option("--input-dir", required=True, type=IN_DIR, help="Folder of vocals.")
@click.option("--reference", required=True, type=EXISTING_FILE, help="Shared reference track.")
@click.option("--out-dir", required=True, type=OUT_DIR, help="Where masters and report.md go.")
def batch(input_dir: Path, reference: Path, out_dir: Path) -> None:
    """Run the full pipeline over every file in INPUT_DIR and write report.md."""
    results = run_batch(input_dir, reference, out_dir)
    if not results:
        click.echo(f"No .wav/.mp3 files found in {input_dir}")
        return

    passed = sum(1 for r in results if r["qa"].get("pass"))
    for r in results:
        verdict = "PASS" if r["qa"].get("pass") else "FAIL"
        click.echo(f"  {verdict}  {r['filename']}")
    click.echo(f"{passed}/{len(results)} passed automated QA")
    click.echo(f"Report -> {out_dir / 'report.md'}")
