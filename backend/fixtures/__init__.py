"""Deterministic golden fixtures for integration tests and material pipelines."""

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR,
    DETERMINISTIC_PDF_EPOCH,
    FIXTURE_NAMES,
    ensure_fixtures,
    generate,
)

__all__ = [
    "DETERMINISTIC_PDF_EPOCH",
    "DEFAULT_FIXTURES_DIR",
    "FIXTURE_NAMES",
    "ensure_fixtures",
    "generate",
]
