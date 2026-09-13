"""Deterministic golden fixtures for integration tests and material pipelines."""

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR,
    DETERMINISTIC_PDF_EPOCH,
    FIXTURE_NAMES,
    assert_pdf_fixture_metadata,
    ensure_fixtures,
    extract_stable_pdf_id,
    generate,
    pdf_has_deterministic_metadata,
    pin_pdf_deterministic_metadata,
    sync_golden_fixtures,
    validate_fixtures,
    verify_independent_generation_byte_identity,
    write_deterministic_pdf,
    write_deterministic_pdf_bytes,
)

__all__ = [
    "DETERMINISTIC_PDF_EPOCH",
    "DEFAULT_FIXTURES_DIR",
    "FIXTURE_NAMES",
    "assert_pdf_fixture_metadata",
    "ensure_fixtures",
    "extract_stable_pdf_id",
    "generate",
    "pdf_has_deterministic_metadata",
    "pin_pdf_deterministic_metadata",
    "sync_golden_fixtures",
    "validate_fixtures",
    "verify_independent_generation_byte_identity",
    "write_deterministic_pdf",
    "write_deterministic_pdf_bytes",
]
