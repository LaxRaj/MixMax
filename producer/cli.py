"""The `producer` command-line interface."""

from __future__ import annotations

from pathlib import Path

import click

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
