# Issue #60 Repair Evidence — Issue-Centered V2 (#57)

Generated: 2026-09-13T08:08:53.667286Z
Base commit: `572a640`
Repair commit: (this commit)

**This directory is the sole SSOT for Issue #60 repair submission.**

## Reviewer bundle (priority)

See `docs/issue60_repair_evidence/00_reviewer_bundle.md` for compact submission with all four invariants, full inline code, and full raw test PASS output.

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

## (4) Captured test output — PASS (actual run 2026-09-13T08:08:53.667286Z)

### pytest

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full output: `test_issue_centered_v2_output.txt`

```
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.1.1, pluggy-1.6.0 -- /home/devbox/project/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/devbox/project
configfile: pyproject.toml
plugins: anyio-4.15.1
collecting ... collected 19 items

backend/tests/integration/test_issue_centered_v2.py::test_position_ai_candidate_lawyer_confirm PASSED [  5%]
backend/tests/integration/test_issue_centered_v2.py::test_position_formal_defense_requires_material PASSED [ 10%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link PASSED [ 15%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_rejects_implicit_version PASSED [ 21%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_cross_case_rejected PASSED [ 26%]
backend/tests/integration/test_issue_centered_v2.py::test_conflict_and_gap_lawyer_actions PASSED [ 31%]
backend/tests/integration/test_issue_centered_v2.py::test_lawyer_assessment_ai_blocked PASSED [ 36%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_merge_and_split PASSED [ 42%]
backend/tests/integration/test_issue_centered_v2.py::test_claim_direction_production_disabled PASSED [ 47%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_proof_state PASSED [ 52%]
backend/tests/integration/test_issue_centered_v2.py::test_green_issue_not_auto_ready PASSED [ 57%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_api PASSED [ 63%]
backend/tests/integration/test_issue_centered_v2.py::test_workspace_includes_issue_work_product PASSED [ 68%]
backend/tests/integration/test_issue_centered_v2.py::test_agent_issue_object_context PASSED [ 73%]
backend/tests/integration/test_issue_centered_v2.py::test_structural_gap_renamed PASSED [ 78%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_1_no_production_claim_direction_creation PASSED [ 84%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case PASSED [ 89%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_formal_defense_requires_opponent_material_ref PASSED [ 94%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_4_merge_split_emit_human_decision_and_audit_log PASSED [100%]

=============================== warnings summary ===============================
.venv/lib/python3.11/site-packages/fastapi/testclient.py:1
  /home/devbox/project/.venv/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link
  /home/devbox/project/backend/tests/conftest.py:66: SAWarning: transaction already deassociated from connection
    transaction.rollback()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 19 passed, 2 warnings in 1.40s ========================
```

### live acceptance

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full output: `live_issue_centered_v2_acceptance_output.txt`

```
/home/devbox/project/.venv/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 30
  1 Case
  2 Material
  3 Evidence accepted
  4 Fact confirmed
  5 AI candidate Issue
  6 Issue confirmed
  7 OUR Position
  8 anticipated opponent defense
  9 AI ProofTask
  10 ProofTask adopted
  11 SUPPORT Fact link
  12 ADVERSE Fact link
  13 Conflict detected
  14 ProofGap created
  15 supplemental material/evidence/fact
  17 LawyerAssessment V1
  18 LawyerAssessment V2 history
  16 gap waived
  19 confirmed Claim
  20 Claim→Issue link
  21 Claim→Fact link
  22 Readiness NOT_READY
  23 supplement critical facts
  24 Readiness READY
  25 PleadingStructuredInput
  26 closed relation graph PASS
  27 generate pleading
  28 reverse trace Draft→Fact→Evidence→SourceSpan→Material
  29 Issue Work Product
  30 Workspace + Agent Issue/Object context
```

## (5) Explicit assertions/verification for four invariants

Dedicated module: `backend/tests/integration/test_issue_centered_v2_invariants.py`

All four invariant tests **PASSED** — see section (A) in `docs/issue60_repair_evidence/00_reviewer_bundle.md` and full test module inlined in section (F).

## Untruncated diff

Full production source inlined in `docs/issue60_repair_evidence/00_reviewer_bundle.md` sections (C)–(F). NOT truncated.
