"""Domain package."""

from backend.domain.enums import StaleEvent
from backend.domain.errors import (
    ConflictError,
    DomainError,
    ImmutableError,
    NotFoundError,
    ValidationError,
)

__all__ = [
    "DomainService",
    "DomainError",
    "NotFoundError",
    "ValidationError",
    "ConflictError",
    "ImmutableError",
    "StaleEvent",
]


def __getattr__(name: str):
    if name == "DomainService":
        from backend.domain.services import DomainService

        return DomainService
    raise AttributeError(name)
