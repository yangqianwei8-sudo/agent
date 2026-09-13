"""CLI shim — delegates to production deterministic fixture generator."""

from __future__ import annotations

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR as FIXTURES,
)
from backend.fixtures.deterministic import (
    DETERMINISTIC_PDF_EPOCH,
    generate,
    sync_golden_fixtures,
)

__all__ = ["DETERMINISTIC_PDF_EPOCH", "FIXTURES", "generate", "sync_golden_fixtures"]

if __name__ == "__main__":
    sync_golden_fixtures()
    print("fixtures written to", FIXTURES)
