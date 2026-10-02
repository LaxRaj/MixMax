"""Rendering our own version into a comparison workspace."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from producer.mastering import master_track
from producer.mix import mix_vocal
from producer.qa import run_qa

ORIGINAL = "original.wav"
OURS = "producer.wav"


def song_dirs(workspace: str | Path) -> list[Path]:
    """Every scaffolded song folder, i.e. anything holding an `original.wav`."""
    workspace = Path(workspace)
    return sorted(p.parent for p in workspace.glob(f"*/{ORIGINAL}"))


def render_one(song_dir: Path, reference: Path, premix: bool = True) -> dict:
    """Run our pipeline on one song's `original.wav`, writing `producer.wav`."""
    source = song_dir / ORIGINAL
    out_path = song_dir / OURS

    with TemporaryDirectory() as tmp:
        staged = source
        if premix:
            staged = Path(tmp) / "premixed.wav"
            mix_vocal(source, staged)
        master_track(staged, reference, out_path)

    return {"slug": song_dir.name, "output": str(out_path), "qa": run_qa(out_path)}


def render_workspace(workspace: str | Path, reference: str | Path, premix: bool = True) -> list[dict]:
    """Render every song in the workspace."""
    reference = Path(reference)
    return [render_one(d, reference, premix) for d in song_dirs(workspace)]
