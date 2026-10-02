"""Reference-based mastering, wrapping `matchering`."""

from __future__ import annotations

from pathlib import Path

import matchering as mg


def _silence_matchering_logs() -> None:
    """matchering is chatty by default; the CLI prints its own summary instead."""
    mg.log(info_handler=None, warning_handler=None, show_codes=False)


def master_track(vocal: str | Path, reference: str | Path, out_path: str | Path) -> Path:
    """Match `vocal` to the tonal/loudness profile of `reference`, write 16-bit PCM."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _silence_matchering_logs()
    mg.process(
        target=str(vocal),
        reference=str(reference),
        results=[mg.pcm16(str(out_path))],
    )
    return out_path
