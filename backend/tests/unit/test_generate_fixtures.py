"""Tests for deterministic Phase 4 golden fixture generation."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from backend.tests.fixtures.generate_fixtures import (
    DETERMINISTIC_PDF_EPOCH,
    FIXTURES,
    generate,
)

_PDF_FIXTURES = ("sample_text.pdf", "sample_scanned.pdf")
_ALL_FIXTURES = (*_PDF_FIXTURES, "sample.docx", "sample_image.png")


def test_generate_writes_all_expected_fixtures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = generate(output_dir=Path(tmp))
        for name in _ALL_FIXTURES:
            assert (root / name).is_file(), f"missing generated fixture: {name}"


def test_pdf_fixtures_regenerate_byte_identically() -> None:
    """Two consecutive generate() runs must produce identical PDF bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        after_first = {name: (root / name).read_bytes() for name in _PDF_FIXTURES}
        generate(output_dir=root)
        after_second = {name: (root / name).read_bytes() for name in _PDF_FIXTURES}
        for name in _PDF_FIXTURES:
            assert after_first[name] == after_second[name], f"{name} changed between runs"


def test_pdf_fixtures_use_fixed_creation_date() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = generate(output_dir=Path(tmp))
        for name in _PDF_FIXTURES:
            text = (root / name).read_text(encoding="latin-1")
            dates = re.findall(r"(CreationDate|ModDate) \(D:([^)]+)\)", text)
            assert dates, f"{name} missing PDF date metadata"
            for field, value in dates:
                assert value == DETERMINISTIC_PDF_EPOCH, (
                    f"{name} {field}={value!r}, expected fixed epoch {DETERMINISTIC_PDF_EPOCH!r}"
                )


def test_docx_fixture_regenerates_byte_identically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        after_first = (root / "sample.docx").read_bytes()
        generate(output_dir=root)
        after_second = (root / "sample.docx").read_bytes()
        assert after_first == after_second


def test_committed_fixtures_match_deterministic_generator() -> None:
    """Committed golden files must match fresh deterministic generation."""
    with tempfile.TemporaryDirectory() as tmp:
        generated = generate(output_dir=Path(tmp))
        for name in _ALL_FIXTURES:
            committed = FIXTURES / name
            assert committed.is_file(), f"missing committed fixture: {name}"
            assert committed.read_bytes() == (generated / name).read_bytes(), (
                f"{name} drifted from deterministic generator output"
            )
