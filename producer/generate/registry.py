"""Backends by name."""

from __future__ import annotations

from typing import Callable

from producer.generate.base import Generator
from producer.generate.fake import FakeGenerator


def _elevenlabs() -> Generator:
    # Imported on demand so a missing key is only an error when this backend is asked for.
    from producer.generate.vendors.elevenlabs import ElevenLabsMusic

    return ElevenLabsMusic()


_FACTORIES: dict[str, Callable[[], Generator]] = {
    "fake": FakeGenerator,
    "elevenlabs": _elevenlabs,
}


def register(name: str, factory: Callable[[], Generator]) -> None:
    _FACTORIES[name] = factory


def registered() -> list[str]:
    return sorted(_FACTORIES)


def get_generator(name: str) -> Generator:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise KeyError(
            f"Unknown generation backend {name!r}. Registered: {', '.join(registered())}."
        )
    return factory()
