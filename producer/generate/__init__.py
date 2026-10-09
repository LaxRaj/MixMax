"""Backing-track generation: the half of a song this pipeline does not make.

Generation is rented, never trained. Everything vendor-specific lives behind
`Generator` in `vendors/`; only the types in `base` cross that boundary, so a
vendor can be swapped without touching the pipeline around it.
"""

from producer.generate.base import (
    GenerationError,
    Generator,
    GenRequest,
    GenResult,
    NotSupported,
)
from producer.generate.registry import get_generator, registered

__all__ = [
    "GenRequest", "GenResult", "Generator", "GenerationError", "NotSupported",
    "get_generator", "registered",
]
