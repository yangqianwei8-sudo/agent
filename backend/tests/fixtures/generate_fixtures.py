"""CLI shim — delegates to production deterministic fixture generator."""

from __future__ import annotations

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR as FIXTURES,
)
from backend.fixtures.deterministic import (
    DETERMINISTIC_PDF_EPOCH,
    generate,
)

__all__ = ["DETERMINISTIC_PDF_EPOCH", "FIXTURES", "generate"]

if __name__ == "__main__":
    generate()
    print("fixtures written to", FIXTURES)
