"""The `producer` command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import click

from producer.analysis import analyze_vocal
from producer.audio import human_size
from producer.mastering import master_track

EXISTING_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
OUT_FILE = click.Path(dir_okay=False, path_type=Path)


@click.group()
@click.version_option(package_name="producer")
def cli() -> None:
    """Analyze, mix, master and QA-gate a raw vocal."""


@cli.command()
@click.option("--vocal", required=True, type=EXISTING_FILE, help="Raw vocal to master.")
@click.option("--reference", required=True, type=EXISTING_FILE, help="Reference track to match.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
def master(vocal: Path, reference: Path, out_path: Path) -> None:
    """Master VOCAL against REFERENCE and write the result to OUT."""
    result = master_track(vocal, reference, out_path)
    click.echo(f"Mastered -> {result} ({human_size(result.stat().st_size)})")


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
