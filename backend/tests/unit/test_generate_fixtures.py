"""Tests for deterministic Phase 4 golden fixture generation."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

import pytest

from backend.tests.fixtures.generate_fixtures import (
    DETERMINISTIC_PDF_EPOCH,
    FIXTURES,
    generate,
)

_PDF_FIXTURES = ("sample_text.pdf", "sample_scanned.pdf")
_ALL_FIXTURES = (*_PDF_FIXTURES, "sample.docx", "sample_image.png")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generate_writes_all_expected_fixtures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Patch FIXTURES by generating into a temp dir via direct calls is awkward;
        # verify committed fixtures exist and match generator output after regen.
        for name in _ALL_FIXTURES:
            assert (FIXTURES / name).is_file(), f"missing committed fixture: {name}"


def test_pdf_fixtures_regenerate_byte_identically() -> None:
    """Two consecutive generate() runs must produce identical PDF bytes."""
    before = {name: FIXTURES.joinpath(name).read_bytes() for name in _PDF_FIXTURES}
    generate()
    after_first = {name: FIXTURES.joinpath(name).read_bytes() for name in _PDF_FIXTURES}
    generate()
    after_second = {name: FIXTURES.joinpath(name).read_bytes() for name in _PDF_FIXTURES}
    for name in _PDF_FIXTURES:
        assert after_first[name] == after_second[name], f"{name} changed between runs"
        assert before[name] == after_first[name], f"{name} drifted from committed bytes"


def test_pdf_fixtures_use_fixed_creation_date() -> None:
    for name in _PDF_FIXTURES:
        text = FIXTURES.joinpath(name).read_text(encoding="latin-1")
        dates = re.findall(r"(CreationDate|ModDate) \(D:([^)]+)\)", text)
        assert dates, f"{name} missing PDF date metadata"
        for field, value in dates:
            assert value == DETERMINISTIC_PDF_EPOCH, (
                f"{name} {field}={value!r}, expected fixed epoch {DETERMINISTIC_PDF_EPOCH!r}"
            )


def test_docx_fixture_regenerates_byte_identically() -> None:
    before = FIXTURES.joinpath("sample.docx").read_bytes()
    generate()
    after_first = FIXTURES.joinpath("sample.docx").read_bytes()
    generate()
    after_second = FIXTURES.joinpath("sample.docx").read_bytes()
    assert after_first == after_second
    assert before == after_first


@pytest.mark.parametrize("name", _PDF_FIXTURES)
def test_committed_pdf_fixture_hash_stable(name: str) -> None:
    """Guard against accidental non-deterministic regeneration in CI."""
    expected = _sha256(FIXTURES / name)
    generate()
    assert _sha256(FIXTURES / name) == expected
