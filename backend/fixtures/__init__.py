"""Deterministic golden fixtures for integration tests and material pipelines."""

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR,
    DETERMINISTIC_PDF_EPOCH,
    FIXTURE_NAMES,
    ensure_fixtures,
    generate,
    pdf_has_deterministic_metadata,
    pin_pdf_deterministic_metadata,
    sync_golden_fixtures,
    validate_fixtures,
)

__all__ = [
    "DETERMINISTIC_PDF_EPOCH",
    "DEFAULT_FIXTURES_DIR",
    "FIXTURE_NAMES",
    "ensure_fixtures",
    "generate",
    "pdf_has_deterministic_metadata",
    "pin_pdf_deterministic_metadata",
    "sync_golden_fixtures",
    "validate_fixtures",
]
