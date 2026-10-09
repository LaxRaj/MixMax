"""Backends by name."""

from __future__ import annotations

from typing import Callable

from producer.generate.base import Generator
from producer.generate.fake import FakeGenerator

_FACTORIES: dict[str, Callable[[], Generator]] = {"fake": FakeGenerator}


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
