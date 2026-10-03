"""Render one source at several published targets, for a blind comparison.

The question this exists to settle: once a platform takes the loudness away,
does chasing it leave the master sounding better or worse? A listening test can
only answer that if the versions differ in *processing* and not in level, which
is exactly what rendering to different targets and then loudness-matching does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from producer.benchmark import measure
from producer.library import FULL_MIX, VOCAL_ONLY, classify_track
from producer.loudness import LoudnessResult, normalize_to_target
from producer.master_chain import DEFAULT_MASTER_PARAMS, MasterParams, master_full_mix
from producer.mastering import master_track
from producer.mix import DEFAULT_PARAMS, ChainParams
from producer.mix import mix_vocal
from producer.standards import Standard, load_standards

# The version rendered by the chain as it stands, at whatever loudness the
# reference implies. The control the others are judged against.
AS_IS = "as_is"
UNPROCESSED = "original"

# A deliberately crushed version belongs in the comparison. Without one, every
# version is the same master at a different level, the blind test gain-matches
# them back together, and listeners are asked to tell identical files apart.
LOUDNESS_WAR_LUFS = -8.0

DEFAULT_TARGETS = ("loud:-8", "spotify", "apple_music", "ebu_r128")


@dataclass
class ShootoutVersion:
    name: str
    path: Path
    target_lufs: float | None
    loudness: dict | None = None
    measurement: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "target_lufs": self.target_lufs,
            "loudness": self.loudness,
            "lufs": self.measurement.get("lufs"),
            "true_peak_dbtp": self.measurement.get("true_peak_dbtp"),
            "crest_factor_db": self.measurement.get("crest_factor_db"),
            "lra": self.measurement.get("lra"),
        }


def parse_target(token: str, table: dict[str, Standard]) -> tuple[str, float, float]:
    """Resolve a target to (name, lufs, ceiling).

    Accepts a standard's key, or `loud:-8` for an explicit LUFS value that no
    platform publishes but plenty of records chase.
    """
    if token in table:
        standard = table[token]
        return token, standard.target_lufs, standard.max_true_peak_dbtp
    if token.startswith("loud:"):
        try:
            value = float(token.split(":", 1)[1])
        except ValueError as exc:
            raise ValueError(f"Could not read a LUFS value from {token!r}") from exc
        return f"loud_{abs(value):g}".replace(".", "_"), value, -1.0
    raise ValueError(
        f"Unknown target {token!r}. Use a standard key ({', '.join(table)}) "
        "or an explicit level like loud:-8."
    )


def detect_kind(path: str | Path) -> str:
    """Full mix or bare vocal.

    This decides whether the vocal chain runs at all: its 80 Hz highpass and
    de-ess are right for a lone voice and actively wrong for a full track,
    where they would thin the kick and bass.
    """
    return classify_track(path)[0]


def build_shootout(
    source: str | Path,
    out_dir: str | Path,
    reference: str | Path | None = None,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    kind: str | None = None,
    params: ChainParams = DEFAULT_PARAMS,
    master_params: MasterParams = DEFAULT_MASTER_PARAMS,
    competitors: dict[str, Path] | None = None,
    include_original: bool = True,
    standards: dict[str, Standard] | None = None,
) -> dict:
    """Render `source` once per target, plus the chain's own output."""
    source, out_dir = Path(source), Path(out_dir)
    reference = Path(reference) if reference else None
    out_dir.mkdir(parents=True, exist_ok=True)
    table = standards if standards is not None else load_standards()

    resolved = [parse_target(t, table) for t in targets]
    kind = kind or detect_kind(source)
    versions: list[ShootoutVersion] = []

    with TemporaryDirectory() as tmp:
        staged = source
        chain_note: dict = {}
        if kind == VOCAL_ONLY:
            # A lone voice wants the vocal chain: rumble out, gate, compress,
            # de-ess, a little space.
            staged = Path(tmp) / "premixed.wav"
            mix_vocal(source, staged, params)
        else:
            # A finished mix wants the mastering chain instead: subsonic
            # cleanup and gentle glue, nothing that reshapes the balance.
            staged = Path(tmp) / "premastered.wav"
            chain_note = master_full_mix(source, staged, master_params)

        as_is = out_dir / f"{AS_IS}.wav"
        if reference is not None:
            master_track(staged, reference, as_is)
        else:
            # No reference means no tonal matching. Loudness is still worth
            # fixing, and inventing an EQ curve from nothing would be guessing
            # at the one thing a reference exists to decide.
            import shutil

            shutil.copyfile(staged, as_is)
        versions.append(ShootoutVersion(name=AS_IS, path=as_is, target_lufs=None))

        for name, target_lufs, ceiling in resolved:
            out = out_dir / f"{name}.wav"
            result: LoudnessResult = normalize_to_target(as_is, out, target_lufs, ceiling)
            versions.append(ShootoutVersion(
                name=name, path=out, target_lufs=target_lufs, loudness=result.to_dict(),
            ))

    if include_original:
        control = out_dir / f"{UNPROCESSED}.wav"
        import shutil

        shutil.copyfile(source, control)
        versions.append(ShootoutVersion(name=UNPROCESSED, path=control, target_lufs=None))

    for name, path in (competitors or {}).items():
        import shutil

        destination = out_dir / f"{name}.wav"
        if Path(path).resolve() != destination.resolve():
            shutil.copyfile(path, destination)
        versions.append(ShootoutVersion(name=name, path=destination, target_lufs=None))

    for version in versions:
        version.measurement = measure(version.path)

    rendered = [v for v in versions if v.loudness is not None]
    processed = [v for v in rendered if v.loudness["limited"]]
    # Versions that reached their target on gain alone are scalar multiples of
    # the same master, so the blind test's gain-matching makes them the same
    # file. Shipping several asks listeners to distinguish identical audio.
    untouched = [v.name for v in rendered if not v.loudness["limited"]]

    return {
        "source": str(source),
        "reference": str(reference) if reference else None,
        "tonal_matching": reference is not None,
        "kind": kind,
        "vocal_chain_applied": kind == VOCAL_ONLY,
        "master_chain_applied": kind != VOCAL_ONLY,
        "mono_source": chain_note.get("mono_source"),
        "versions": [v.to_dict() for v in versions],
        # If nothing needed limiting, every rendered version is the same audio
        # at a different level -- and the blind test will gain-match them into
        # the same file. Worth refusing to pretend otherwise.
        "differs_in_processing": len(processed) > 0,
        "limited_versions": [v.name for v in processed],
        "identical_after_matching": untouched if len(untouched) > 1 else [],
    }


def build_shootout_report(result: dict) -> str:
    """Markdown summary of what each target actually achieved."""
    lines = [
        "# Loudness shootout",
        "",
        ("" if result.get("tonal_matching", True) else
         "> No reference supplied, so loudness was set but tone was left alone.\n"
         "> Supply one to enable reference matching.\n\n")
        + f"Source: `{Path(result['source']).name}` — detected as **{result['kind']}**"
        + (", vocal chain applied." if result["vocal_chain_applied"]
           else ", mastering chain applied (subsonic cleanup + gentle glue)."),
        "",
        ("> **Mono source.** There is no stereo image to work with, and widening a "
         "mono file means inventing the difference signal — which buys width by "
         "damaging mono fold-down. Left alone.\n" if result.get("mono_source") else ""),
        "",
        "| Version | Target | LUFS | True peak | Crest | LRA |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for v in result["versions"]:
        lines.append(
            "| {name} | {target} | {lufs} | {tp} | {crest} | {lra} |".format(
                name=v["name"],
                target=f"{v['target_lufs']:.0f}" if v["target_lufs"] is not None else "—",
                lufs=f"{v['lufs']:.1f}" if v["lufs"] is not None else "—",
                tp=f"{v['true_peak_dbtp']:+.2f}" if v["true_peak_dbtp"] is not None else "—",
                crest=f"{v['crest_factor_db']:.1f}" if v["crest_factor_db"] is not None else "—",
                lra=f"{v['lra']:.1f}" if v.get("lra") is not None else "—",
            )
        )

    if not result.get("differs_in_processing"):
        lines += [
            "",
            "> **This comparison is vacuous.** No version needed limiting to reach its",
            "> target, so they are all the same master at different levels — and the",
            "> blind test gain-matches them back together. Add a loud target",
            "> (`--target loud:-8`) so at least one version is genuinely crushed.",
        ]

    lines += [
        "",
        "## What the listening test settles",
        "",
        "These versions are the same master at different loudnesses, so the blind",
        "test gain-matches them all to the quietest. Once level is equalised the",
        "only thing left is the limiting each target required — which is precisely",
        "the thing streaming normalisation makes invisible in the numbers.",
        "",
        "If listeners cannot tell them apart, loudness was never worth chasing.",
        "If the quieter renders win, chasing it was actively costing you.",
    ]
    if result.get("limited_versions"):
        lines += [
            "",
            f"Limiting engaged for: {', '.join(result['limited_versions'])}. "
            "The rest reached their target on gain alone and are untouched.",
        ]
    return "\n".join(lines) + "\n"
