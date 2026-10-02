"""Audio I/O and the `Track` abstraction shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

# Extensions the pipeline is willing to read.
AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg")


@dataclass
class Track:
    """A loaded audio file: where it came from, its samples, and its rate.

    `samples` is float32, shaped (n,) for mono or (n, channels) for multichannel,
    which is the layout both soundfile and pedalboard's `Pedalboard.__call__`
    disagree on -- see `channels_first` conversions at the call sites.
    """

    path: Path
    samples: np.ndarray
    sample_rate: int

    @classmethod
    def load(cls, path: str | Path) -> "Track":
        path = Path(path)
        samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
        return cls(path=path, samples=samples, sample_rate=int(sample_rate))

    def write(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), self.samples, self.sample_rate)
        return out_path

    @property
    def duration_s(self) -> float:
        return len(self.samples) / float(self.sample_rate)

    @property
    def channels(self) -> int:
        return 1 if self.samples.ndim == 1 else int(self.samples.shape[1])

    def mono(self) -> np.ndarray:
        """Mono mixdown, for analysis stages that only care about one signal."""
        if self.samples.ndim == 1:
            return self.samples
        return self.samples.mean(axis=1)


def human_size(num_bytes: int) -> str:
    """Byte count as a short human string, for CLI confirmation lines."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"
