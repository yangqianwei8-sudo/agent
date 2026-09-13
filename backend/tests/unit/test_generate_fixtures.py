"""Tests for deterministic Phase 4 golden fixture generation."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from backend.fixtures.deterministic import (
    DEFAULT_FIXTURES_DIR as FIXTURES,
)
from backend.fixtures.deterministic import (
    DETERMINISTIC_PDF_EPOCH,
    ensure_fixtures,
    generate,
    pdf_has_deterministic_metadata,
    validate_fixtures,
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


def test_pdf_fixtures_have_stable_id_digest() -> None:
    """ReportLab invariant mode must pin /ID so PDF bytes do not churn."""
    id_pattern = re.compile(r"/ID\s*\[<([0-9a-f]+)><\1>\]")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        first_ids = {}
        for name in _PDF_FIXTURES:
            text = (root / name).read_text(encoding="latin-1")
            match = id_pattern.search(text)
            assert match is not None, f"{name} missing stable /ID digest"
            first_ids[name] = match.group(1)
        generate(output_dir=root)
        for name in _PDF_FIXTURES:
            text = (root / name).read_text(encoding="latin-1")
            match = id_pattern.search(text)
            assert match is not None, f"{name} missing stable /ID digest on second run"
            assert match.group(1) == first_ids[name], f"{name} /ID digest changed between runs"


def test_docx_fixture_regenerates_byte_identically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        after_first = (root / "sample.docx").read_bytes()
        generate(output_dir=root)
        after_second = (root / "sample.docx").read_bytes()
        assert after_first == after_second


def test_ensure_fixtures_generates_when_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = ensure_fixtures(output_dir=Path(tmp))
        for name in _ALL_FIXTURES:
            assert (root / name).is_file(), f"missing fixture after ensure: {name}"


def test_ensure_fixtures_validates_without_overwriting() -> None:
    """Integration tests must validate golden files without rewriting them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        before = {name: (root / name).read_bytes() for name in _ALL_FIXTURES}
        ensure_fixtures(output_dir=root)
        after = {name: (root / name).read_bytes() for name in _ALL_FIXTURES}
        assert before == after


def test_validate_fixtures_detects_drift() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        (root / "sample_text.pdf").write_bytes(b"corrupt")
        try:
            validate_fixtures(root)
        except ValueError as exc:
            assert "sample_text.pdf" in str(exc)
        else:
            raise AssertionError("expected drift validation to fail")


def test_pdf_has_deterministic_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = generate(output_dir=Path(tmp))
        for name in _PDF_FIXTURES:
            assert pdf_has_deterministic_metadata((root / name).read_bytes())
        assert not pdf_has_deterministic_metadata(b"%PDF-1.4\nnot-a-real-fixture")


def test_committed_fixtures_match_deterministic_generator() -> None:
    """Committed golden files must match fresh deterministic generation."""
    validate_fixtures(FIXTURES)
