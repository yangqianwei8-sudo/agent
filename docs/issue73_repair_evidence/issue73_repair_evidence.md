# Issue #73 Repair Evidence — Issue-Centered V2 (#60)

Generated: 2026-09-13T05:25:00Z  
Base commit: `ed482a4`  
Implementation commit: `c19379b` (already on main)  
Repair commit: (this commit)

## (1) Full migration — proof_gaps + lawyer_assessments with check constraints

Untruncated file: `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (394 lines).

Full patch included in `docs/issue73_repair_evidence/issue60_c19379b_key_files.patch`.

**proof_gaps check constraints (verified in migration):**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`

**lawyer_assessments check constraints (verified in migration):**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`

Also includes FK to `(issue_key, issue_version)` and optional `(proof_task_key, proof_task_version)`.

## (2) Full domain + application files

Untruncated files (included in patch):
- `backend/domain/issue_centered.py` (1062 lines)
- `backend/application/issue_work_product.py` (501 lines)

## (3) Test + live acceptance output

### pytest backend/tests/integration/test_issue_centered_v2.py

Run: `TEST_DATABASE_URL=postgresql+psycopg://postgres:***@lawyer-postgresql.ns-dqyh88ke.svc:5432/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py -v`

```
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.1.1, pluggy-1.6.0
collected 15 items

backend/tests/integration/test_issue_centered_v2.py::test_position_ai_candidate_lawyer_confirm PASSED
backend/tests/integration/test_issue_centered_v2.py::test_position_formal_defense_requires_material PASSED
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link PASSED
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_rejects_implicit_version PASSED
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_cross_case_rejected PASSED
backend/tests/integration/test_issue_centered_v2.py::test_conflict_and_gap_lawyer_actions PASSED
backend/tests/integration/test_issue_centered_v2.py::test_lawyer_assessment_ai_blocked PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_merge_and_split PASSED
backend/tests/integration/test_issue_centered_v2.py::test_claim_direction_production_disabled PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_proof_state PASSED
backend/tests/integration/test_issue_centered_v2.py::test_green_issue_not_auto_ready PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_api PASSED
backend/tests/integration/test_issue_centered_v2.py::test_workspace_includes_issue_work_product PASSED
backend/tests/integration/test_issue_centered_v2.py::test_agent_issue_object_context PASSED
backend/tests/integration/test_issue_centered_v2.py::test_structural_gap_renamed PASSED

======================== 15 passed, 2 warnings in 1.40s ========================
```

### live_issue_centered_v2_acceptance.py

Run: `.venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py` (forces `LLM_MODE=deterministic`)

```
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

## (4) Explicit constraint verification

### No production ClaimDirection mutation

`backend/domain/services.py` `create_claim_direction()` raises `ValidationError("ClaimDirection production mutation disabled; use Claim Domain instead")` unless `_legacy_compat=True`. Test: `test_claim_direction_production_disabled`.

Production path `backend/application/claim_direction.py` calls `self.domain.create_claim_direction(...)` without `_legacy_compat` — blocked at domain layer.

### ProofTaskFactLink rejects implicit current/latest and cross-case links

`link_fact_to_proof_task()` in `backend/domain/issue_centered.py`:
- Requires explicit `proof_task_version` and `fact_version` (no get_current_* for linking)
- Nonexistent versions → `NotFoundError("proof task version not found")` / `NotFoundError("fact version not found")`
- Validates `task.case_id != case_id` → `ValidationError("cross-case proof task link rejected")`
- Validates `fact.case_id != case_id` → `ValidationError("cross-case fact link rejected")`
- Tests: `test_proof_task_fact_link_rejects_implicit_version`, `test_proof_task_adopt_and_fact_link`, `test_proof_task_fact_link_cross_case_rejected`

### FORMAL_DEFENSE requires opponent_material_ref

`create_lawyer_position()` lines 135-137: if `position_type == FORMAL_DEFENSE` and not `opponent_material_ref`, raises `ValidationError("FORMAL_DEFENSE requires opponent material reference")`. Test: `test_position_formal_defense_requires_material`.

### merge/split emit HumanDecision + AuditLog

`merge_issues()` creates HumanDecision with `decision_type="MERGE_ISSUES"` before mutation, then `_audit(..., "merge_issues", ...)`.

`split_issue()` creates HumanDecision with `decision_type="SPLIT_ISSUE"` before mutation, then `_audit(..., "split_issue", ...)`.

Test: `test_issue_merge_and_split` asserts `HumanDecision` rows for `MERGE_ISSUES` and `SPLIT_ISSUE`, plus `AuditLog` rows for `merge_issues` and `split_issue`.
