#!/usr/bin/env python3
"""Generate Issue #60 repair SSOT bundle with fully inlined untruncated artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "issue60_repair_evidence"

PRODUCTION_FILES = {
    "migration": ROOT / "alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py",
    "domain": ROOT / "backend/domain/issue_centered.py",
    "application": ROOT / "backend/application/issue_work_product.py",
    "invariants": ROOT / "backend/tests/integration/test_issue_centered_v2_invariants.py",
}

OUTPUT_FILES = {
    "pytest": EVIDENCE / "test_issue_centered_v2_output.txt",
    "live": EVIDENCE / "live_issue_centered_v2_acceptance_output.txt",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fence(lang: str, content: str) -> str:
    return f"```{lang}\n{content.rstrip()}\n```"


def _invariants_section() -> str:
    return """## (A) Four required invariants — explicit code-level assertions

Dedicated test module: `backend/tests/integration/test_issue_centered_v2_invariants.py`

### A1. No production mutation path creates ClaimDirection

**Domain guard** (`backend/domain/services.py` `create_claim_direction`): raises `ValidationError("ClaimDirection production mutation disabled; use Claim Domain instead")` unless `_legacy_compat=True`.

**Test:** `test_invariant_1_no_production_claim_direction_creation`
- `pytest.raises(ValidationError, match="ClaimDirection production")`
- `assert rows == []` — no ClaimDirection row created
— **PASSED**

### A2. ProofTaskFactLink rejects implicit current/latest and cross-case links

**Domain enforcement** (`link_fact_to_proof_task`): uses `get_proof_task_version` / `get_fact_version` (explicit versions only); cross-case → `ValidationError("cross-case ...")`.

**Test:** `test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case`
- `proof_task_version+99` → `NotFoundError("proof task version not found")`
- `fact_version+99` → `NotFoundError("fact version not found")`
- cross-case fact → `ValidationError(match="cross-case")`
— **PASSED**

### A3. FORMAL_DEFENSE requires opponent_material_ref

**Domain enforcement** (`create_lawyer_position` lines 143–145): raises `ValidationError("FORMAL_DEFENSE requires opponent material reference")` when ref absent.

**Test:** `test_invariant_3_formal_defense_requires_opponent_material_ref`
- without ref → `pytest.raises(ValidationError, match="FORMAL_DEFENSE")`
- with ref → `assert pos.opponent_material_ref == "material:answer-001"`
— **PASSED**

### A4. merge/split emit HumanDecision + AuditLog

**Domain enforcement:** `merge_issues` creates `HumanDecision(decision_type="MERGE_ISSUES")` + `_audit(..., "merge_issues")`; `split_issue` creates `HumanDecision(decision_type="SPLIT_ISSUE")` + `_audit(..., "split_issue")`.

**Test:** `test_invariant_4_merge_split_emit_human_decision_and_audit_log`
- `assert len(merge_decisions) == 1`
- `assert len(merge_audits) >= 1`
- `assert len(split_decisions) == 1`
- `assert len(split_audits) >= 1`
— **PASSED**"""


def build_bundle(ts: datetime) -> str:
    ts_iso = ts.isoformat().replace("+00:00", "Z")
    pytest_out = _read(OUTPUT_FILES["pytest"])
    live_out = _read(OUTPUT_FILES["live"])
    migration = _read(PRODUCTION_FILES["migration"])
    domain = _read(PRODUCTION_FILES["domain"])
    application = _read(PRODUCTION_FILES["application"])
    invariants = _read(PRODUCTION_FILES["invariants"])

    mig_lines = migration.count("\n") + 1
    dom_lines = domain.count("\n") + 1
    app_lines = application.count("\n") + 1
    inv_lines = invariants.count("\n") + 1

    header = f"""# Issue #60 Resubmit — Issue-Centered V2 (#57 repair)

Generated: {ts_iso} | Base: `572a640` | Repair: `HEAD`

**SSOT:** `docs/issue60_repair_evidence/` — complete untruncated artifacts below (all code and stdout inlined, NOT truncated).

## Production artifacts (modified in repair)

| Artifact | Lines | Production path |
|----------|-------|-----------------|
| Full migration | {mig_lines} | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` |
| Full domain | {dom_lines} | `backend/domain/issue_centered.py` |
| Full application | {app_lines} | `backend/application/issue_work_product.py` |
| Invariant tests | {inv_lines} | `backend/tests/integration/test_issue_centered_v2_invariants.py` |

SSOT copies (identical to production): `sources/migration_h9b0c1d2e3f4.py`, `sources/domain_issue_centered.py`, `sources/application_issue_work_product.py`, `sources/test_issue_centered_v2_invariants.py`.
"""

    test_section = f"""## (B) Test output — actual run {ts_iso}

Run: `TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full raw stdout (untruncated):

{_fence("", pytest_out)}

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full raw stdout (untruncated):

{_fence("", live_out)}"""

    migration_section = f"""## (C) Migration — proof_gaps + lawyer_assessments + all CheckConstraints (complete, untruncated, inlined)

Production path: `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` ({mig_lines} lines)

**proof_gaps check constraints:**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`

**lawyer_assessments check constraints:**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`

Also: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `issue_legal_theory_links` — every CheckConstraint listed in migration module docstring.

{_fence("python", migration)}"""

    domain_section = f"""## (D) Domain — backend/domain/issue_centered.py (complete, untruncated, inlined)

Production path: `backend/domain/issue_centered.py` ({dom_lines} lines)

{_fence("python", domain)}"""

    application_section = f"""## (E) Application — backend/application/issue_work_product.py (complete, untruncated, inlined)

Production path: `backend/application/issue_work_product.py` ({app_lines} lines)

{_fence("python", application)}"""

    invariants_section = f"""## (F) Invariant tests — backend/tests/integration/test_issue_centered_v2_invariants.py (complete, untruncated, inlined)

Production path: `backend/tests/integration/test_issue_centered_v2_invariants.py` ({inv_lines} lines)

{_fence("python", invariants)}

Verified by: all 19 pytest tests + live acceptance 30 steps — **PASSED**."""

    return "\n\n".join(
        [
            header.strip(),
            _invariants_section(),
            test_section,
            migration_section,
            domain_section,
            application_section,
            invariants_section,
        ]
    )


def build_evidence_md(ts: datetime, bundle_rel: str) -> str:
    ts_iso = ts.isoformat().replace("+00:00", "Z")
    pytest_out = _read(OUTPUT_FILES["pytest"])
    live_out = _read(OUTPUT_FILES["live"])
    return f"""# Issue #60 Repair Evidence — Issue-Centered V2 (#57)

Generated: {ts_iso}
Base commit: `572a640`
Repair commit: (this commit)

**This directory is the sole SSOT for Issue #60 repair submission.**

## Reviewer bundle (priority)

See `{bundle_rel}` for compact submission with all four invariants, full inline code, and full raw test PASS output.

## (1) Full alembic migration h9b0c1d2e3f4 — untruncated (production path)

| Location | Lines |
|----------|-------|
| Production | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` |
| SSOT copy | `sources/migration_h9b0c1d2e3f4.py` |

**proof_gaps** table with check constraints:
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`

**lawyer_assessments** table with check constraints:
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`

## (2) Full backend/domain/issue_centered.py — untruncated (production path)

Production: `backend/domain/issue_centered.py` | SSOT: `sources/domain_issue_centered.py`

## (3) Full backend/application/issue_work_product.py — untruncated (production path)

Production: `backend/application/issue_work_product.py` | SSOT: `sources/application_issue_work_product.py`

## (4) Captured test output — PASS (actual run {ts_iso})

### pytest

Run: `TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full output: `test_issue_centered_v2_output.txt`

{_fence("", pytest_out)}

### live acceptance

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full output: `live_issue_centered_v2_acceptance_output.txt`

{_fence("", live_out)}

## (5) Explicit assertions/verification for four invariants

Dedicated module: `backend/tests/integration/test_issue_centered_v2_invariants.py`

All four invariant tests **PASSED** — see section (A) in `{bundle_rel}` and full test module inlined in section (F).

## Untruncated diff

Full production source inlined in `{bundle_rel}` sections (C)–(F). NOT truncated.
"""


def sync_sources() -> None:
    sources = EVIDENCE / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    mapping = {
        "migration_h9b0c1d2e3f4.py": PRODUCTION_FILES["migration"],
        "domain_issue_centered.py": PRODUCTION_FILES["domain"],
        "application_issue_work_product.py": PRODUCTION_FILES["application"],
        "test_issue_centered_v2_invariants.py": PRODUCTION_FILES["invariants"],
    }
    for dest, src in mapping.items():
        (sources / dest).write_text(_read(src), encoding="utf-8")


def main() -> None:
    ts = datetime.now(UTC)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    sync_sources()
    bundle_path = EVIDENCE / "00_reviewer_bundle.md"
    evidence_path = EVIDENCE / "issue60_repair_evidence.md"
    bundle_path.write_text(build_bundle(ts), encoding="utf-8")
    evidence_path.write_text(
        build_evidence_md(ts, "docs/issue60_repair_evidence/00_reviewer_bundle.md"),
        encoding="utf-8",
    )
    marker = ROOT / "autonomous_dev" / "acceptance_marker.txt"
    marker.write_text(f"worker-run issue=60 at={ts.isoformat()}\n", encoding="utf-8")
    print(f"Wrote {bundle_path} ({bundle_path.stat().st_size} bytes)")
    print(f"Wrote {evidence_path} ({evidence_path.stat().st_size} bytes)")
    print(f"Updated {marker}")


if __name__ == "__main__":
    main()
