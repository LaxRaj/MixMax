"""Rendering our own version into a comparison workspace."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from producer.audio import AUDIO_SUFFIXES
from producer.mastering import master_track
from producer.mix import mix_vocal
from producer.qa import run_qa

ORIGINAL = "original.wav"
OURS = "producer.wav"

# A per-track reference lives beside the track under this stem, in any audio
# format. It is a *different song* -- the tonal target -- so `benchmark` must
# never pick it up as a version of this one. See RESERVED_STEMS there.
REFERENCE_STEM = "reference"


class MissingReference(RuntimeError):
    """A track has no per-track reference and no fallback was supplied."""


def song_dirs(workspace: str | Path) -> list[Path]:
    """Every scaffolded song folder, i.e. anything holding an `original.wav`."""
    workspace = Path(workspace)
    return sorted(p.parent for p in workspace.glob(f"*/{ORIGINAL}"))


def find_track_reference(song_dir: str | Path) -> Path | None:
    """The reference sitting beside this track, if there is one.

    Any audio extension works, so a downloaded `reference.mp3` is fine. A
    symlink is fine too -- handy when several tracks share one reference
    without copying it into every folder.
    """
    song_dir = Path(song_dir)
    candidates = sorted(
        p for p in song_dir.glob(f"{REFERENCE_STEM}.*")
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    )
    return candidates[0] if candidates else None


def resolve_reference(song_dir: str | Path, default: str | Path | None = None) -> Path:
    """Per-track reference if present, otherwise the workspace-wide fallback."""
    found = find_track_reference(song_dir)
    if found is not None:
        return found
    if default is not None:
        return Path(default)
    raise MissingReference(
        f"{Path(song_dir).name} has no {REFERENCE_STEM}.* and no --reference fallback was given."
    )


def plan_references(
    workspace: str | Path, default: str | Path | None = None
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Work out which reference each track will use, before rendering anything.

    Returns (resolved, missing) so the caller can refuse the whole run rather
    than render half the workspace and then fail.
    """
    resolved: list[tuple[Path, Path]] = []
    missing: list[Path] = []
    for song_dir in song_dirs(workspace):
        try:
            resolved.append((song_dir, resolve_reference(song_dir, default)))
        except MissingReference:
            missing.append(song_dir)
    return resolved, missing


def render_one(song_dir: Path, reference: Path, premix: bool = True) -> dict:
    """Run our pipeline on one song's `original.wav`, writing `producer.wav`."""
    song_dir, reference = Path(song_dir), Path(reference)
    source = song_dir / ORIGINAL
    out_path = song_dir / OURS

    with TemporaryDirectory() as tmp:
        staged = source
        if premix:
            staged = Path(tmp) / "premixed.wav"
            mix_vocal(source, staged)
        master_track(staged, reference, out_path)

    return {
        "slug": song_dir.name,
        "output": str(out_path),
        "reference": str(reference),
        # Which reference produced a master decides how it sounds, so record
        # whether it was chosen for this track or inherited.
        "reference_source": "per-track" if find_track_reference(song_dir) else "fallback",
        "qa": run_qa(out_path),
    }


def render_workspace(
    workspace: str | Path,
    reference: str | Path | None = None,
    premix: bool = True,
) -> list[dict]:
    """Render every song in the workspace, each against its own reference."""
    resolved, missing = plan_references(workspace, reference)
    if missing:
        names = ", ".join(d.name for d in missing)
        raise MissingReference(
            f"No reference for: {names}. Drop a {REFERENCE_STEM}.wav in each folder, "
            "or pass --reference as a fallback."
        )
    return [render_one(song_dir, ref, premix) for song_dir, ref in resolved]
