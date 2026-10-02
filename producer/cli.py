"""The `producer` command-line interface."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import click

from producer.analysis import analyze_vocal
from producer.batch import run_batch
from producer.benchmark import build_benchmark_report, compare, discover_versions
from producer.blindtest import build_blind_test, build_tally_report, tally
from producer.intake import DEFAULT_SERVICES, run_intake
from producer.workspace import (
    REFERENCE_STEM,
    MissingReference,
    plan_references,
    render_workspace,
    song_dirs,
)
from producer.audio import human_size
from producer.mastering import master_track
from producer.mix import mix_vocal
from producer.qa import run_qa
from producer.report_plot import plot_comparison

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
@click.option("--plot", is_flag=True, help="Also write a before/after comparison PNG.")
def master(vocal: Path, reference: Path, out_path: Path, premix: bool, plot: bool) -> None:
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

    if plot:
        png = plot_comparison(vocal, result, result.with_suffix(".comparison.png"))
        click.echo(f"Plot -> {png}")


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


@cli.command()
@click.option("--versions-dir", required=True, type=IN_DIR,
              help="Folder with one file per service, e.g. producer.wav / landr.wav.")
@click.option("--baseline", default="producer", show_default=True,
              help="Which version to diff the others against.")
@click.option("--out", "out_path", type=OUT_FILE, help="Write the markdown report here.")
def benchmark(versions_dir: Path, baseline: str, out_path: Path | None) -> None:
    """Measure our master against commercial ones and say where it differs."""
    versions = discover_versions(versions_dir)
    if len(versions) < 2:
        raise click.ClickException(
            f"Need at least two versions to compare; found {len(versions)} in {versions_dir}."
        )
    if baseline not in versions:
        raise click.ClickException(
            f"Baseline '{baseline}' not found. Available: {', '.join(sorted(versions))}."
        )

    comparison = compare(versions, baseline=baseline)
    report = build_benchmark_report(comparison, title=f"Benchmark — {versions_dir.name}")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        click.echo(f"Benchmark -> {out_path}")
    else:
        click.echo(report)


@cli.command()
@click.option("--versions-dir", required=True, type=IN_DIR, help="Folder with one file per service.")
@click.option("--out-dir", required=True, type=OUT_DIR, help="Share this folder with listeners.")
@click.option("--key", "key_path", type=OUT_FILE,
              help="Where the un-blinding key goes. Defaults to a sibling of --out-dir.")
@click.option("--target-lufs", type=float, help="Match to this loudness instead of the quietest version.")
@click.option("--seed", type=int, help="Seed the label shuffle, for a reproducible test.")
def blindtest(versions_dir: Path, out_dir: Path, key_path: Path | None,
              target_lufs: float | None, seed: int | None) -> None:
    """Build a loudness-matched, anonymized listening test for friends."""
    versions = discover_versions(versions_dir)
    try:
        result = build_blind_test(versions, out_dir, key_path, target_lufs, seed)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Matched everything to {result['target_lufs']} LUFS:")
    for version in result["versions"]:
        click.echo(f"  {version['label']}  <- {version['source']}"
                   f"  ({version['original_lufs']} LUFS, {version['applied_gain_db']:+} dB)")
    click.echo(f"Share -> {result['out_dir']}")
    click.secho(f"Keep the key private -> {result['key_path']}", fg="yellow")


@cli.command(name="tally")
@click.option("--key", "key_path", required=True, type=EXISTING_FILE, help="The key written by blindtest.")
@click.option("--responses", required=True, type=click.Path(exists=True, path_type=Path),
              help="A filled scoresheet CSV, or a folder of them.")
@click.option("--out", "out_path", type=OUT_FILE, help="Write the markdown results here.")
def tally_cmd(key_path: Path, responses: Path, out_path: Path | None) -> None:
    """Un-blind the returned scoresheets and aggregate them."""
    result = tally(key_path, responses)
    if not result["rows"]:
        raise click.ClickException(f"No usable rows found in {responses}.")

    report = build_tally_report(result)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        click.echo(f"Results -> {out_path}")
    else:
        click.echo(report)


@cli.command()
@click.option("--input", "source", required=True,
              type=click.Path(exists=True, path_type=Path),
              help="A raw vocal, or a folder of them (searched recursively).")
@click.option("--workspace", required=True, type=OUT_DIR,
              help="Where the comparison workspace is scaffolded.")
@click.option("--service", "services", multiple=True,
              help=f"Service to compare against; repeatable. Default: {', '.join(DEFAULT_SERVICES)}.")
def intake(source: Path, workspace: Path, services: tuple[str, ...]) -> None:
    """Validate raw vocals and scaffold a comparison workspace."""
    results = run_intake(source, workspace, services or DEFAULT_SERVICES)
    if not results:
        raise click.ClickException(f"No audio files found under {source}.")

    colours = {"ready": "green", "caution": "yellow", "blocked": "red"}
    for r in results:
        click.secho(f"  {r['verdict']:8} {Path(r['source_file']).name}", fg=colours[r["verdict"]])
        for issue in r["blockers"] + r["warnings"]:
            click.echo(f"           {issue}")

    usable = [r for r in results if r["verdict"] != "blocked"]
    click.echo(f"{len(usable)}/{len(results)} usable -> {workspace}")
    click.echo(f"Report   -> {workspace / 'INTAKE_REPORT.md'}")
    click.echo(f"Next     -> {workspace / 'MANIFEST.md'} (upload checklist)")
    if not usable:
        raise click.ClickException("Nothing passed intake; see the blockers above.")


@cli.command()
@click.option("--workspace", required=True, type=IN_DIR, help="Workspace created by `intake`.")
@click.option("--reference", type=EXISTING_FILE,
              help=f"Fallback reference for tracks with no {REFERENCE_STEM}.* of their own.")
@click.option("--no-premix", is_flag=True, help="Master the raw vocal without the mix chain.")
@click.option("--dry-run", is_flag=True, help="Show which reference each track would use, then stop.")
def render(workspace: Path, reference: Path | None, no_premix: bool, dry_run: bool) -> None:
    """Render our own version of every track, each against its own reference.

    Drop a `reference.wav` (or .mp3, or a symlink) beside a track to give it
    its own tonal target; --reference covers everything else.
    """
    if not song_dirs(workspace):
        raise click.ClickException(
            f"No song folders in {workspace}. Run `producer intake` first."
        )

    resolved, missing = plan_references(workspace, reference)
    if missing:
        raise click.ClickException(
            "No reference for: {names}.\n"
            "Drop a {stem}.wav in each of those folders, or pass --reference "
            "as a fallback.".format(
                names=", ".join(d.name for d in missing), stem=REFERENCE_STEM
            )
        )

    if dry_run:
        for song_dir, ref in resolved:
            origin = "per-track" if ref.parent == song_dir else "fallback"
            click.echo(f"  {song_dir.name:24} {origin:9} {ref}")
        return

    try:
        results = render_workspace(workspace, reference, premix=not no_premix)
    except MissingReference as exc:
        raise click.ClickException(str(exc)) from exc

    for result in results:
        verdict = "PASS" if result["qa"]["pass"] else "FAIL"
        click.echo(f"  {verdict}  {result['slug']} -> {result['output']}")
        click.echo(f"          reference: {Path(result['reference']).name} ({result['reference_source']})")
        for flag in result["qa"]["flags"]:
            click.echo(f"          {flag}")
