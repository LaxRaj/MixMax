"""The `producer` command-line interface."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import click

from producer.analysis import analyze_vocal
from producer.benchmark import measure as measure_audio
from producer.dashboard import build_dashboard
from producer.batch import run_batch
from producer.benchmark import build_benchmark_report, compare, discover_versions
from producer.blindtest import build_blind_test, build_tally_report, tally
from producer.intake import DEFAULT_SERVICES, run_intake
from producer.library import (
    Library,
    analyze_reference,
    derive_thresholds,
    discover_references,
    match_reference,
    summarize,
)
from producer.workspace import (
    REFERENCE_STEM,
    MissingReference,
    plan_references,
    render_workspace,
    song_dirs,
)
from producer.audio import human_size
from producer.mastering import master_track
from producer.mix import ChainParams, DEFAULT_PARAMS, mix_vocal
from producer.qa import DEFAULT_PROFILE, QAProfile, run_qa
from producer.shootout import DEFAULT_TARGETS, build_shootout, build_shootout_report
from producer.arrange import (
    DEFAULT_BRIDGE_BARS,
    BarGrid,
    plan_extension,
    render_arrangement,
)
from producer.combine import (
    DEFAULT_DUCK_DB,
    DEFAULT_VOCAL_OVER_BEAT_DB,
    align_to_grid,
    combine,
    find_offset,
)
from producer.progress import build_comparison
from producer.song import deliver_master
from producer.store import StoreError, open_store
from producer.sync import Syncer
from producer.structure import analyze_structure, build_structure_report
from producer.master_chain import DEFAULT_MASTER_PARAMS, master_full_mix
from producer.loudness import normalize_to_target
from producer.standards import (
    delivery_ceiling,
    evaluate,
    evaluate_all,
    load_standards,
    summarize_conformance,
    to_qa_profile,
)
from producer.tune import tune_chain
from producer.report_plot import plot_comparison
from producer.generate import GenerationError, GenRequest, get_generator
from producer.generate.ledger import BudgetExceeded, Ledger
from producer.generate.run import GENERATION_FILE, generate_candidates
from producer.generate.fit import fit_candidate
from producer.generate.spec import analyze_spec

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
@click.option("--params", "params_path", type=EXISTING_FILE, help="Chain settings from `producer tune`.")
def mix(vocal: Path, out_path: Path, params_path: Path | None) -> None:
    """Run VOCAL through the vocal mix chain and write it to OUT."""
    params = ChainParams.load(params_path) if params_path else DEFAULT_PARAMS
    result = mix_vocal(vocal, out_path, params)
    click.echo(f"Mixed -> {result} ({human_size(result.stat().st_size)})")


def echo_qa(report: dict) -> None:
    """Print a QA report as a verdict line plus one line per failed check."""
    verdict = "PASS" if report["pass"] else "FAIL"
    click.echo(f"QA {verdict} — {report['lufs']} LUFS")
    for flag in report["flags"]:
        click.echo(f"  - {flag}")


@cli.command()
@click.option("--file", "file_path", required=True, type=EXISTING_FILE, help="Audio to check.")
@click.option("--profile", "profile_path", type=EXISTING_FILE,
              help="QA thresholds from `producer library thresholds`.")
@click.option("--standard", "standard_key",
              help="Judge against a published standard instead, e.g. spotify.")
def qa(file_path: Path, profile_path: Path | None, standard_key: str | None) -> None:
    """Run the automated QA gate against FILE and print the report as JSON."""
    if profile_path and standard_key:
        raise click.ClickException("Use --profile or --standard, not both.")

    if standard_key:
        table = load_standards()
        if standard_key not in table:
            raise click.ClickException(
                f"Unknown standard {standard_key!r}. Available: {', '.join(table)}."
            )
        import tempfile as _tempfile

        payload = to_qa_profile(table[standard_key])
        with _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
        profile = QAProfile.load(fh.name)
    elif profile_path:
        profile = QAProfile.load(profile_path)
    else:
        profile = DEFAULT_PROFILE

    click.echo(json.dumps(run_qa(file_path, profile), indent=2))


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


@cli.command()
@click.option("--vocal", required=True, type=EXISTING_FILE, help="Raw vocal to fit against.")
@click.option("--reference", required=True, type=EXISTING_FILE, help="Reference we master to.")
@click.option("--target", required=True, type=EXISTING_FILE,
              help="Master to approach — e.g. LANDR's version of this same vocal.")
@click.option("--out", "out_path", type=OUT_FILE, default="chain_params.json", show_default=True,
              help="Where the fitted settings are written.")
@click.option("--budget", default=60, show_default=True, help="How many renders to try.")
@click.option("--seed", default=0, show_default=True, help="Seed, for a reproducible search.")
def tune(vocal: Path, reference: Path, target: Path, out_path: Path, budget: int, seed: int) -> None:
    """Fit the mix chain to a TARGET master, by measurement.

    This optimises measurable similarity. Nothing here listens, so a smaller
    distance is a lead to test, not proof of a better-sounding master.
    """
    click.echo(f"Fitting {vocal.name} toward {target.name} over {budget} renders…")

    with click.progressbar(length=budget + 1, label="  searching") as bar:
        last = {"n": 0}

        def on_step(n: int, _loss: float, _best: float) -> None:
            bar.update(n - last["n"])
            last["n"] = n

        result = tune_chain(vocal, reference, target, budget=budget, seed=seed, on_step=on_step)

    result.params.save(out_path)

    click.echo(f"Distance  {result.baseline_loss:.3f} -> {result.loss:.3f} "
               f"({result.improvement * 100:+.1f}%)")
    for name, value in result.params.to_dict().items():
        default = getattr(DEFAULT_PARAMS, name)
        marker = " " if abs(value - default) < 1e-6 else "*"
        click.echo(f"  {marker} {name:20} {value:>8.2f}   (was {default:.2f})")
    click.echo(f"Settings -> {out_path}")

    if result.improvement <= 0.01:
        click.secho(
            "Barely moved. The mastering stage sets most of the tone, so the mix "
            "chain may have little room here — try a different reference instead.",
            fg="yellow",
        )
    click.secho(
        "Measured similarity only. Put it through a blind test before trusting it.",
        fg="yellow",
    )


DEFAULT_LIBRARY = Path("reference_library.json")
LIBRARY_OPT = click.option(
    "--library", "library_path", type=OUT_FILE, default=DEFAULT_LIBRARY,
    show_default=True, help="Catalogue file.",
)


@cli.group()
def library() -> None:
    """Build a reference library from real releases, and learn from it."""


@library.command("add")
@click.option("--input", "source", required=True,
              type=click.Path(exists=True, path_type=Path),
              help="A song, or a folder of them (searched recursively).")
@click.option("--genre", default="unspecified", show_default=True,
              help="Tag these tracks, so thresholds can be derived per style.")
@click.option("--kind", type=click.Choice(["full-mix", "vocal-only"]),
              help="Override the automatic full-mix / vocal-only detection.")
@LIBRARY_OPT
def library_add(source: Path, genre: str, kind: str | None, library_path: Path) -> None:
    """Measure reference tracks and catalogue them.

    Only measurements are stored — the audio stays where it is.
    """
    paths = discover_references(source)
    if not paths:
        raise click.ClickException(f"No audio found under {source}.")

    catalogue = Library.load(library_path)
    with click.progressbar(paths, label="  analysing", item_show_func=lambda p: p.name if p else "") as items:
        for path in items:
            try:
                catalogue.add(analyze_reference(path, genre=genre, kind=kind))
            except Exception as exc:
                click.echo(f"\n  skipped {path.name}: {exc}")

    catalogue.save(library_path)
    click.echo(f"{len(catalogue)} reference(s) catalogued -> {library_path}")
    click.echo(f"Genres: {', '.join(catalogue.genres)}")


@library.command("list")
@click.option("--genre", help="Only this genre.")
@LIBRARY_OPT
def library_list(genre: str | None, library_path: Path) -> None:
    """Show what is in the library."""
    entries = Library.load(library_path).filter(genre=genre)
    if not entries:
        raise click.ClickException("Library is empty. Run `producer library add` first.")

    click.echo(f"{'title':<34} {'genre':<12} {'kind':<11} {'bpm':>6} {'key':<9} {'LUFS':>7}")
    for e in sorted(entries, key=lambda x: (x.genre, x.title)):
        click.echo(
            f"{e.title[:33]:<34} {e.genre[:11]:<12} {e.kind:<11} "
            f"{e.tempo_bpm:>6.1f} {e.key:<9} {e.lufs:>7.1f}"
        )


@library.command("stats")
@click.option("--genre", help="Only this genre.")
@LIBRARY_OPT
def library_stats(genre: str | None, library_path: Path) -> None:
    """What finished records in the library actually measure like."""
    entries = Library.load(library_path).filter(genre=genre)
    if not entries:
        raise click.ClickException("Nothing to summarise. Run `producer library add` first.")
    click.echo(json.dumps(summarize(entries, genre).to_dict(), indent=2))


@library.command("thresholds")
@click.option("--genre", help="Derive from this genre only.")
@click.option("--out", "out_path", type=OUT_FILE, default="qa_profile.json", show_default=True)
@LIBRARY_OPT
def library_thresholds(genre: str | None, out_path: Path, library_path: Path) -> None:
    """Replace the hand-picked QA window with one measured off real records."""
    entries = Library.load(library_path).filter(genre=genre)
    try:
        profile = derive_thresholds(entries, genre)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profile, indent=2) + "\n")

    click.echo(f"From {profile['derived_from']} reference(s):")
    click.echo(f"  LUFS window  {profile['lufs_min']} .. {profile['lufs_max']}   "
               f"(built-in was {DEFAULT_PROFILE.lufs_min} .. {DEFAULT_PROFILE.lufs_max})")
    click.echo(f"  {profile['corpus_inside_window']}/{profile['derived_from']} of the corpus "
               f"sits inside that window ({profile['corpus_pass_rate'] * 100:.0f}%) — "
               "trimming at p5/p95 deliberately leaves the extremes out.")
    click.echo(f"Profile -> {out_path}")
    click.echo("Use it:  producer qa --file OUT.wav --profile " + str(out_path))
    click.secho(
        "This is what these records measure like, not what sounds good.", fg="yellow"
    )


@library.command("match")
@click.option("--vocal", required=True, type=EXISTING_FILE, help="The vocal to find a reference for.")
@click.option("--genre", help="Restrict to this genre.")
@click.option("--top", default=3, show_default=True, help="How many suggestions.")
@LIBRARY_OPT
def library_match(vocal: Path, genre: str | None, top: int, library_path: Path) -> None:
    """Suggest the reference that asks the least of the mastering stage."""
    catalogue = Library.load(library_path)
    if not len(catalogue):
        raise click.ClickException("Library is empty. Run `producer library add` first.")

    metrics = measure_vocal(vocal)
    ranked = match_reference(catalogue, metrics, tempo_bpm=metrics.get("tempo_bpm"), genre=genre)
    if not ranked:
        raise click.ClickException(f"No references match genre={genre!r}.")

    source_kind = classify_vocal_kind(metrics)
    for entry, score in ranked[:top]:
        click.echo(f"  {score:6.3f}  {entry.title[:38]:<40} {entry.genre:<12} "
                   f"{entry.tempo_bpm:>5.1f}bpm  {entry.key}")
    click.echo(f"\nBest: {ranked[0][0].path}")

    mismatched = [e for e, _ in ranked[:top] if e.kind != source_kind]
    if source_kind == "vocal-only" and mismatched:
        click.secho(
            "Your source reads as a bare vocal but these references are full mixes. "
            "Mastering toward them asks matchering to invent low end that was never "
            "recorded — prefer vocal-only references, or master after the beat is in.",
            fg="yellow",
        )


def measure_vocal(path: Path) -> dict:
    """Measurements plus tempo, which is what reference matching ranks on."""
    metrics = measure_audio(path)
    metrics["tempo_bpm"] = analyze_vocal(path)["tempo_bpm"]
    return metrics


def classify_vocal_kind(metrics: dict) -> str:
    from producer.library import classify_kind

    return classify_kind(metrics["band_balance_db"])


STANDARDS_OPT = click.option(
    "--standards", "standards_path", type=EXISTING_FILE,
    help="Replace the built-in targets with a JSON table.",
)


@cli.group()
def standards() -> None:
    """Check a master against published loudness standards."""


@standards.command("list")
@STANDARDS_OPT
def standards_list(standards_path: Path | None) -> None:
    """Show the targets, and where each figure came from."""
    table = load_standards(standards_path)
    click.echo(f"{'key':<16} {'target':>8} {'peak':>8}  {'up?':<5} {'source':<34} as of")
    for s in table.values():
        click.echo(
            f"{s.key:<16} {s.target_lufs:>6.0f} LUFS {s.max_true_peak_dbtp:>5.0f} dBTP  "
            f"{('yes' if s.normalizes_up else 'no'):<5} {(s.source or '—')[:33]:<34} {s.as_of or '—'}"
        )
    click.secho(
        "\nPlatform targets drift. Verify against the current published spec "
        "before trusting one for a release; --standards replaces this table.",
        fg="yellow",
    )


@standards.command("check")
@click.option("--file", "file_path", required=True, type=EXISTING_FILE, help="Master to check.")
@click.option("--standard", "standard_key", help="Only this one.")
@click.option("--out", "out_path", type=OUT_FILE, help="Write the report as JSON.")
@STANDARDS_OPT
def standards_check(
    file_path: Path, standard_key: str | None, out_path: Path | None, standards_path: Path | None
) -> None:
    """Report what each platform will do to this master on playback."""
    table = load_standards(standards_path)
    if standard_key:
        if standard_key not in table:
            raise click.ClickException(
                f"Unknown standard {standard_key!r}. Available: {', '.join(table)}."
            )
        table = {standard_key: table[standard_key]}

    metrics = measure_audio(file_path)
    results = evaluate_all(metrics, table)

    click.echo(f"{file_path.name}: {metrics['lufs']:.1f} LUFS, "
               f"true peak {metrics['true_peak_dbtp']:+.2f} dBTP, "
               f"crest {metrics['crest_factor_db']:.1f} dB\n")
    click.echo(f"{'platform':<28} {'target':>7} {'gain':>8} {'delivered':>10} {'peak after':>11}")
    for r in results:
        verdict = click.style("ok", fg="green") if r.conforms else click.style("issues", fg="red")
        click.echo(
            f"{table[r.standard].name[:27]:<28} {r.target_lufs:>6.0f} "
            f"{r.normalization_gain_db:>+7.1f} {r.delivered_lufs:>9.1f} "
            f"{r.true_peak_after_norm_dbtp:>+10.2f}   {verdict}"
        )

    notes = summarize_conformance(metrics, results, table)
    if notes:
        click.echo("\nWhat that means:")
        for note in notes:
            click.echo(f"  - {note}")

    problems = [(r.standard, i) for r in results for i in r.issues]
    if problems:
        click.echo("\nIssues:")
        for key, issue in problems:
            click.echo(f"  - {table[key].name}: {issue}")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(
            {"file": str(file_path), "measurement": metrics,
             "conformance": [r.to_dict() for r in results]}, indent=2) + "\n")
        click.echo(f"\nReport -> {out_path}")


@standards.command("profile")
@click.option("--standard", "standard_key", required=True, help="Which target to encode.")
@click.option("--out", "out_path", type=OUT_FILE, default="qa_profile.json", show_default=True)
@STANDARDS_OPT
def standards_profile(standard_key: str, out_path: Path, standards_path: Path | None) -> None:
    """Write a QA profile that accepts masters this standard delivers cleanly."""
    table = load_standards(standards_path)
    if standard_key not in table:
        raise click.ClickException(
            f"Unknown standard {standard_key!r}. Available: {', '.join(table)}."
        )

    payload = to_qa_profile(table[standard_key])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n")

    click.echo(f"{table[standard_key].name}: "
               f"{payload['lufs_min']} .. {payload['lufs_max']} LUFS, "
               f"true peak <= {payload['true_peak_max_dbtp']:.0f} dBTP")
    click.echo(f"Profile -> {out_path}")
    click.echo(f"Use it:  producer qa --file OUT.wav --profile {out_path}")


@cli.command()
@click.option("--workspace", type=IN_DIR, help="Workspace created by `intake`.")
@click.option("--library", "library_path", type=EXISTING_FILE, help="Reference library catalogue.")
@click.option("--qa-profile", "qa_profile_path", type=EXISTING_FILE, help="Derived QA profile.")
@click.option("--params", "chain_params_path", type=EXISTING_FILE, help="Fitted chain settings.")
@click.option("--results", "results_path", type=EXISTING_FILE, help="Tallied listening results JSON.")
@click.option("--out", "out_path", type=OUT_FILE, default="web/public/dashboard.json",
              show_default=True, help="Where the page reads its data from.")
def dashboard(
    workspace: Path | None, library_path: Path | None, qa_profile_path: Path | None,
    chain_params_path: Path | None, results_path: Path | None, out_path: Path,
) -> None:
    """Collect the whole pipeline's state for the dashboard page."""
    data = build_dashboard(
        workspace=workspace, library_path=library_path, qa_profile_path=qa_profile_path,
        chain_params_path=chain_params_path, results_path=results_path,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n")

    summary = data["evidence_summary"]
    for stage in data["stages"]:
        click.echo(f"  {stage['label']:<18} {stage['done']}/{stage['total']}")

    counts = summary["counts"]
    click.echo(
        f"\nEvidence: {summary['grounded_pct']}% of {summary['total']} numbers are grounded "
        f"(published {counts.get('published', 0)}, measured {counts.get('measured', 0)}, "
        f"fitted {counts.get('fitted', 0)}, reported {counts.get('reported', 0)}, "
        f"guessed {counts.get('guessed', 0)})"
    )
    if counts.get("guessed"):
        click.secho(
            f"{counts['guessed']} number(s) are still hand-picked defaults nobody has checked.",
            fg="yellow",
        )
    click.echo(f"Dashboard -> {out_path}")


@cli.command()
@click.option("--source", required=True, type=EXISTING_FILE,
              help="The track to render — a full mix with its beat, or a bare vocal.")
@click.option("--reference", type=EXISTING_FILE,
              help="Reference to match tone and loudness against. Without one, only "
                   "loudness is set and the tone is left untouched.")
@click.option("--out-dir", required=True, type=OUT_DIR, help="Where the versions are written.")
@click.option("--target", "targets", multiple=True,
              help=f"Standard to render for; repeatable. Default: {', '.join(DEFAULT_TARGETS)}.")
@click.option("--kind", type=click.Choice(["full-mix", "vocal-only"]),
              help="Override the automatic detection that decides whether the vocal chain runs.")
@click.option("--competitor", "competitors", multiple=True, type=EXISTING_FILE,
              help="A commercial master of the same track; repeatable.")
@click.option("--params", "params_path", type=EXISTING_FILE, help="Fitted chain settings.")
@click.option("--no-original", is_flag=True, help="Leave the unprocessed source out.")
def shootout(
    source: Path, reference: Path | None, out_dir: Path, targets: tuple[str, ...],
    kind: str | None, competitors: tuple[Path, ...], params_path: Path | None,
    no_original: bool,
) -> None:
    """Render one track at several published targets, ready for a blind test.

    Loudness normalisation hides how hard a master was limited. Rendering the
    same source at each target and then gain-matching for listening is what
    makes that audible.
    """
    chain = ChainParams.load(params_path) if params_path else DEFAULT_PARAMS
    try:
        result = build_shootout(
            source=source, reference=reference, out_dir=out_dir,
            targets=targets or DEFAULT_TARGETS, kind=kind, params=chain,
            competitors={p.stem: p for p in competitors},
            include_original=not no_original,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if not result["tonal_matching"]:
        click.secho("No reference given — setting loudness only, tone untouched.", fg="yellow")
    click.echo(f"Detected {result['kind']}; "
               + ("vocal chain applied."
                  if result["vocal_chain_applied"]
                  else "mastering chain applied (subsonic cleanup + gentle glue)."))
    if result.get("mono_source"):
        click.secho(
            "Source is mono — no stereo image to work with. Widening one means "
            "inventing the side signal, which costs mono compatibility, so it was "
            "left alone.", fg="yellow")
    click.echo(f"\n{'version':<18} {'target':>7} {'LUFS':>7} {'peak':>8} {'crest':>7}")
    for v in result["versions"]:
        target = f"{v['target_lufs']:.0f}" if v["target_lufs"] is not None else "—"
        click.echo(
            f"{v['name']:<18} {target:>7} {v['lufs']:>7.1f} "
            f"{v['true_peak_dbtp']:>+8.2f} {v['crest_factor_db']:>7.1f}"
        )

    missed = [v for v in result["versions"] if v["loudness"] and not v["loudness"]["on_target"]]
    for v in missed:
        click.secho(f"  {v['name']} landed at {v['loudness']['achieved_lufs']:.1f} LUFS, "
                    f"not {v['target_lufs']:.0f} — limiting pulled it back.", fg="yellow")

    if not result["differs_in_processing"]:
        click.secho(
            "\nNo version needed limiting, so these are the same master at different "
            "levels. A blind test gain-matches them back together and would be asking "
            "listeners to tell identical files apart — add --target loud:-8.",
            fg="red",
        )
    elif result["limited_versions"]:
        click.echo(f"\nLimiting engaged for: {', '.join(result['limited_versions'])}. "
                   "The rest reached their target on gain alone.")

    duplicates = result.get("identical_after_matching") or []
    if duplicates:
        click.secho(
            f"{', '.join(duplicates)} needed no limiting, so they are the same audio at "
            "different levels and the blind test will make them identical. Keep one.",
            fg="yellow",
        )

    report = out_dir / "shootout.md"
    report.write_text(build_shootout_report(result))
    (out_dir / "shootout.json").write_text(json.dumps(result, indent=2) + "\n")

    click.echo(f"\nReport -> {report}")
    click.echo(f"Next:  producer blindtest --versions-dir {out_dir} --out-dir blind/<slug>")


@cli.command()
@click.option("--source", required=True, type=EXISTING_FILE, help="The mix to master.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
@click.option("--lufs", "target_lufs", default=-16.0, show_default=True, type=float,
              help="The loudness you actually want the master to have.")
@click.option("--standard", "standard_key", default="spotify", show_default=True,
              help="The platform whose normalisation we aim at.")
@click.option("--reference", type=EXISTING_FILE, help="Match tone against this track.")
@click.option("--no-chain", is_flag=True, help="Skip the mastering chain; set loudness only.")
def deliver(
    source: Path, out_path: Path, target_lufs: float, standard_key: str,
    reference: Path | None, no_chain: bool,
) -> None:
    """Master to a chosen loudness, leaving headroom for the platform to lift.

    A master sitting on the platform's target with no headroom cannot be raised,
    so it plays quieter than everything else. Leaving exactly the gap the
    platform wants to close means a quieter, more dynamic master still arrives
    at full playback loudness.
    """
    table = load_standards()
    if standard_key not in table:
        raise click.ClickException(
            f"Unknown standard {standard_key!r}. Available: {', '.join(table)}."
        )
    standard = table[standard_key]

    result, info = deliver_master(
        source, out_path, target_lufs, standard_key, reference, chain=not no_chain
    )
    if info and info["mono_source"]:
        click.secho("Mono source — no stereo image to work with.", fg="yellow")

    conformance = evaluate(measure_audio(out_path), standard)
    click.echo(
        f"Mastered to {result.achieved_lufs:.1f} LUFS @ {result.true_peak_dbtp:+.2f} dBTP"
        f"  ({'limited' if result.limited else 'gain only'})"
    )
    click.echo(
        f"{standard.name} lifts it {conformance.normalization_gain_db:+.1f} dB "
        f"-> delivered {conformance.delivered_lufs:.1f} LUFS "
        f"@ {conformance.true_peak_after_norm_dbtp:+.2f} dBTP"
    )
    if abs(conformance.delivered_lufs - standard.target_lufs) <= 0.3:
        click.secho(
            f"Lands on {standard.name}'s target while keeping the dynamics of a "
            f"{target_lufs:.0f} LUFS master.", fg="green")
    click.echo(f"Master -> {out_path}")


@cli.command()
@click.option("--source", required=True, type=EXISTING_FILE, help="The track to break down.")
@click.option("--out", "out_path", type=OUT_FILE, help="Write the markdown report here.")
@click.option("--json", "json_path", type=OUT_FILE, help="Write the raw structure as JSON.")
def structure(source: Path, out_path: Path | None, json_path: Path | None) -> None:
    """Break a track into sections and say where the arrangement is thin.

    Turning a loop into a song is an arrangement problem, not a processing one —
    no amount of mastering adds a bridge.
    """
    result = analyze_structure(source)

    click.echo(f"{result.duration_s / 60:.2f} min · {result.tempo_bpm:.0f} BPM · {result.key}\n")
    click.echo(f"{'#':>2} {'sec':<4} {'start':>6} {'len':>6} {'energy':>8} {'onsets/s':>9} {'low':>8}")
    for s in result.sections:
        click.echo(
            f"{s.index + 1:>2} {s.label:<4} {int(s.start_s // 60)}:{int(s.start_s % 60):02d}".ljust(22)
            + f"{s.duration_s:>5.0f}s {s.energy_db:>7.1f} {s.onset_rate:>9.1f} {s.low_energy_db:>7.1f}"
        )

    click.echo("\nrepetition: " + (" ".join(f"{k}x{v}" for k, v in result.repetition.items()) or "—"))
    if result.transitions:
        click.echo("transitions:")
        for t in result.transitions:
            arrow = {"lift": "▲", "drop": "▼", "breakdown": "◇", "bass returns": "◆"}[t["kind"]]
            click.echo(
                f"  {arrow} {t['kind']:<13} at {int(t['at_s'] // 60)}:{int(t['at_s'] % 60):02d}  "
                f"{t['from_label']} -> {t['to_label']}  "
                f"energy {t['energy_change_db']:+.1f} dB  low {t.get('low_change_db', 0):+.1f} dB"
            )
    else:
        click.secho("transitions: none — nothing drops or builds.", fg="yellow")

    if result.notes:
        click.echo("\nwhat the arrangement is missing:")
        for note in result.notes:
            click.echo(f"  - {note}")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(build_structure_report(result, title=f"Arrangement — {source.stem}"))
        click.echo(f"\nReport -> {out_path}")
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
        click.echo(f"JSON   -> {json_path}")


@cli.command()
@click.option("--source", required=True, type=EXISTING_FILE, help="The track to extend.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
@click.option("--minutes", default=2.8, show_default=True, type=float,
              help="Roughly how long the result should be.")
@click.option("--bridge-bars", default=DEFAULT_BRIDGE_BARS, show_default=True, type=float,
              help="Length of the breakdown, in bars.")
@click.option("--dry-run", is_flag=True, help="Show the plan without rendering.")
def arrange(
    source: Path, out_path: Path, minutes: float, bridge_bars: float, dry_run: bool
) -> None:
    """Add a bridge and a drop, and extend the track toward a full song.

    This composes nothing. Every sample out is a sample in — moved, filtered or
    faded. A breakdown built from the track's own material is a real technique,
    but it is not the same as writing a new part.
    """
    structure = analyze_structure(source)
    grid = BarGrid.from_audio(source)
    try:
        arrangement, why = plan_extension(
            structure, grid, target_duration_s=minutes * 60.0, bridge_bars=bridge_bars
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"{grid.tempo_bpm:.1f} BPM · bar {grid.bar_s:.3f}s · edits snapped to bar lines\n")
    click.echo(f"{'role':<34} {'from':>8} {'to':>8} {'len':>7}")
    for segment in arrangement.segments:
        click.echo(f"{segment.role:<34} {segment.source_start_s:>8.2f} "
                   f"{segment.source_end_s:>8.2f} {segment.duration_s:>6.1f}s")
    click.echo(f"\n{structure.duration_s / 60:.2f} min -> {arrangement.duration_s / 60:.2f} min")
    for note in why:
        click.echo(f"  - {note}")

    if dry_run:
        return

    info = render_arrangement(source, arrangement, out_path)
    click.echo(f"\nArranged -> {info['output']} ({info['duration_s'] / 60:.2f} min, "
               f"{info['segments']} segments)")
    click.secho(
        "Built entirely from the source. A bridge made of existing material is a "
        "real technique, not a new part written for the song.", fg="yellow")


# Named `compare_cmd` because `compare` is already imported from
# producer.benchmark: shadowing it made `producer benchmark` call this Click
# object instead, with a TypeError about Context.
@cli.command("compare")
@click.option("--workspace", required=True, type=IN_DIR, help="Workspace created by `intake`.")
@click.option("--out", "out_path", type=OUT_FILE, default="web/public/compare.json",
              show_default=True, help="Where the comparison view reads its data.")
def compare_cmd(workspace: Path, out_path: Path) -> None:
    """How finished each track is, and what is actually stopping it.

    A compliant master of a bare vocal is still a bare vocal, so this reports
    completion rather than conformance.
    """
    data = build_comparison(workspace)
    if not data["tracks"]:
        raise click.ClickException(f"No tracks in {workspace}. Run `producer intake` first.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n")

    colours = {"done": "green", "partial": "yellow", "blocked": "red", "todo": "white"}
    for track in data["tracks"]:
        click.echo(f"\n{track['slug']}  —  {track['percent']}%  "
                   f"({track['kind']}, {track['duration_s'] / 60:.2f} min)")
        for stage in track["stages"]:
            mark = {"done": "✓", "partial": "~", "blocked": "✗", "todo": "·"}[stage["state"]]
            click.secho(f"   {mark} {stage['label']:<18} {stage['detail']}",
                        fg=colours[stage["state"]])
        click.echo(f"   next: {track['next_step']}")

    click.echo(f"\nComparison -> {out_path}")


@cli.command("combine")
@click.option("--vocal", required=True, type=EXISTING_FILE, help="The vocal take.")
@click.option("--beat", required=True, type=EXISTING_FILE, help="The instrumental.")
@click.option("--out", "out_path", required=True, type=OUT_FILE, help="Destination WAV.")
@click.option("--offset", "offset_s", type=float,
              help="Seconds the vocal starts after the beat. Detected if omitted.")
@click.option("--vocal-over-beat", "vocal_over_beat_db", default=DEFAULT_VOCAL_OVER_BEAT_DB,
              show_default=True, type=float,
              help="How far the vocal sits above the beat, in LU.")
@click.option("--duck", "duck_db", default=DEFAULT_DUCK_DB, show_default=True, type=float,
              help="How far the beat steps back under the voice. 0 disables.")
@click.option("--check-alignment", is_flag=True, help="Report the offset and stop.")
def combine_cmd(
    vocal: Path, beat: Path, out_path: Path, offset_s: float | None,
    vocal_over_beat_db: float, duck_db: float, check_alignment: bool,
) -> None:
    """Mix a vocal over a beat — the stage nothing else here can clear.

    Lines the two up, sets the balance by loudness, and ducks the beat under
    the words so the vocal stays intelligible.
    """
    if check_alignment:
        alignment = align_to_grid(vocal, beat)
        if not alignment.trustworthy:
            click.echo(f"grid: {alignment.method} (confidence {alignment.confidence:.2f})")
            alignment = find_offset(vocal, beat)
        click.echo(f"Offset {alignment.offset_s:+.3f}s via {alignment.method} "
                   f"(confidence {alignment.confidence:.2f})")
        if not alignment.trustworthy:
            click.secho("Too weak to trust — pass --offset yourself.", fg="yellow")
        return

    result = combine(vocal, beat, out_path, offset_s, vocal_over_beat_db, duck_db)
    alignment = result["alignment"]

    click.echo(f"Aligned {alignment['offset_s']:+.3f}s ({alignment['method']}, "
               f"confidence {alignment['confidence']:.2f})")
    if not alignment["trustworthy"]:
        click.secho(
            "Alignment confidence is low. Check the first downbeat by ear, and pass "
            "--offset if the vocal sits early or late.", fg="yellow")

    click.echo(f"Vocal {result['vocal_lufs']} LUFS, beat {result['beat_lufs']} LUFS "
               f"-> beat {result['beat_gain_db']:+.1f} dB for a "
               f"{result['vocal_over_beat_db']:.1f} LU gap")
    if result["duck_db"]:
        click.echo(f"Beat ducks {result['duck_db']:.1f} dB under the vocal")
    if result["headroom_trim_db"]:
        click.echo(f"Trimmed {result['headroom_trim_db']:.1f} dB to leave mastering headroom")
    click.echo(f"\nMix -> {result['output']} ({result['duration_s'] / 60:.2f} min)")
    click.echo("Next:  producer deliver --source "
               f"{out_path} --out MASTER.wav --lufs -16")


@cli.command("sync")
@click.option("--workspace", default="comparisons", show_default=True, type=OUT_DIR,
              help="Workspace created by `intake`.")
@click.option("--vocals", "vocals_dir", default="vocals", show_default=True, type=OUT_DIR,
              help="Where uploaded files are kept as they arrived.")
@click.option("--store", "store_dir", type=OUT_DIR,
              help="Use this local folder as the store instead of Vercel Blob.")
@click.option("--watch", is_flag=True, help="Keep running, checking for new work.")
@click.option("--interval", default=20.0, show_default=True, type=float,
              help="Seconds between checks when watching.")
def sync_cmd(workspace: Path, vocals_dir: Path, store_dir: Path | None,
             watch: bool, interval: float) -> None:
    """Do what the studio web app was asked for, and publish the result.

    Checks uploaded files, applies settings requests by re-rendering, rebuilds
    each song's feedback.md, and pushes song data and audio back for the UI.
    The web app never processes audio; nothing happens there until this runs.
    """
    import time

    try:
        store = open_store(store_dir)
    except StoreError as exc:
        raise click.ClickException(str(exc)) from exc
    syncer = Syncer(workspace, store, vocals_dir, log=lambda msg: click.echo(f"  {msg}"))
    click.echo(f"Syncing {workspace} with {store.name}")

    def one_pass() -> None:
        summary = syncer.run_once()
        for upload in summary.uploads:
            report = upload["report"]
            colour = {"ready": "green", "caution": "yellow", "blocked": "red"}[report["verdict"]]
            click.secho(f"upload   {upload.get('filename')} ({report['kind']} for "
                        f"{report['slug']}) — {report['verdict']}", fg=colour)
        for request in summary.requests:
            result = request["result"]
            click.secho(
                f"request  {request['slug']}: {result['state']}"
                + (f" — {result['message']}" if result.get("message") else "")
                + (f" — re-rendered {', '.join(result['rendered'])}" if result.get("rendered") else ""),
                fg="green" if result["state"] == "done" else "red",
            )
        for slug, count in summary.feedback.items():
            if summary.did_something or not watch:
                click.echo(f"feedback {slug}: {count} note(s) -> {workspace / slug / 'feedback.md'}")
        for slug in summary.published:
            click.echo(f"publish  {slug}")
        if summary.problems:
            click.secho("\nNeeds a look:", fg="yellow", bold=True)
            for problem in summary.problems:
                click.secho(f"  - {problem}", fg="yellow")
            click.echo(f"Full list: {workspace / 'UPLOAD_LOG.md'}")

    if not watch:
        try:
            one_pass()
        except StoreError as exc:
            raise click.ClickException(str(exc)) from exc
        return

    click.echo(f"Watching every {interval:g}s. Ctrl-C to stop.")
    while True:
        try:
            one_pass()
        except StoreError as exc:
            # A dropped connection should not end a long watch.
            click.secho(f"store unreachable: {exc}", fg="red")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            click.echo("\nStopped.")
            return



@cli.command("generate")
@click.option("--vocal", required=True, type=EXISTING_FILE, help="The vocal the backing is for.")
@click.option("--style", required=True, help="What the backing should sound like, in words.")
@click.option("--out-dir", required=True, type=OUT_DIR, help="Where candidates are written.")
@click.option("--backend", default="fake", show_default=True,
              help="Which generator to use. `fake` copies existing beats and costs nothing.")
@click.option("--n", "n_candidates", default=3, show_default=True, type=click.IntRange(1, 8),
              help="How many candidates to ask for.")
@click.option("--seed", type=int, help="Seed, for a repeatable request where the backend allows.")
@click.option("--lyrics", "lyrics_path", type=EXISTING_FILE, help="Lyrics as a text file.")
def generate_cmd(
    vocal: Path, style: str, out_dir: Path, backend: str, n_candidates: int,
    seed: int | None, lyrics_path: Path | None,
) -> None:
    """Ask a generator for candidate backings for VOCAL.

    Every call is written to a ledger beside the candidates, and refused before
    it is made if it would take spending past MIXMAX_GEN_BUDGET_USD.
    """
    try:
        generator = get_generator(backend)
    except KeyError as exc:
        raise click.ClickException(exc.args[0]) from exc

    req = GenRequest(
        vocal=vocal, style=style, seed=seed, n_candidates=n_candidates,
        lyrics=lyrics_path.read_text() if lyrics_path else None,
    )
    try:
        run = generate_candidates(generator, req, out_dir)
    except (BudgetExceeded, GenerationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    for result in run["candidates"]:
        click.echo(f"{result.path}  {result.vendor}/{result.model}  "
                   f"${result.cost_usd:.2f}  {result.latency_s:.1f}s")
    if backend == "fake":
        click.secho("The fake backend copies existing audio. Nothing here was generated "
                    "for this vocal.", fg="yellow")
    click.echo(f"Total ${run['total_cost_usd']:.2f} · ledger ${run['ledger_spent_usd']:.2f} of "
               f"${run['budget_usd']:.2f} -> {out_dir / GENERATION_FILE}")


@cli.command("spec")
@click.option("--vocal", required=True, type=EXISTING_FILE, help="The vocal to measure.")
@click.option("--lyrics", "lyrics_path", type=EXISTING_FILE, help="Lyrics as a text file.")
@click.option("--language", help="The language the vocal is in. Never guessed.")
@click.option("--out", "out_path", type=OUT_FILE, help="Also write the JSON here.")
def spec_cmd(vocal: Path, lyrics_path: Path | None, language: str | None,
             out_path: Path | None) -> None:
    """Measure what VOCAL asks of a backing: tempo, key, range, sections, contour.

    Anything that cannot be measured is reported as null with the reason under
    `unmeasured`, rather than filled with a guess.
    """
    spec = analyze_spec(vocal, lyrics_path.read_text() if lyrics_path else None, language)
    payload = json.dumps(spec.to_dict(), indent=2)
    click.echo(payload)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n")


@cli.command("fit")
@click.option("--vocal", required=True, type=EXISTING_FILE, help="The vocal.")
@click.option("--candidate", required=True, type=EXISTING_FILE, help="A candidate backing.")
@click.option("--out", "out_path", required=True, type=OUT_FILE,
              help="Where the fitted candidate goes, if it passes.")
@click.option("--spec", "spec_path", type=EXISTING_FILE,
              help="A VocalSpec from `producer spec`. Measured from the vocal if omitted.")
def fit_cmd(vocal: Path, candidate: Path, out_path: Path, spec_path: Path | None) -> None:
    """Tempo-lock CANDIDATE to VOCAL and check the key, or reject it with reasons.

    A rejection is a result, not an error: the exit code is 0 either way, and
    the report beside --out says what was done and what could not be checked.
    """
    spec = json.loads(spec_path.read_text()) if spec_path else None
    result = fit_candidate(vocal, candidate, spec, out_path)

    report = out_path.with_suffix(".fit.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result.to_dict(), indent=2) + "\n")

    if result.passed:
        click.secho(f"FIT -> {result.output}", fg="green")
    else:
        click.secho("REJECT", fg="red", bold=True)
        for reason in result.reasons:
            click.echo(f"  - {reason}")
    for note in result.notes:
        click.echo(f"  · {note}")
    click.echo(f"Report -> {report}")
