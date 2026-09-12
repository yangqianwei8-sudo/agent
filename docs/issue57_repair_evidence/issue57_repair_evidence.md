# Issue #57 Repair Evidence — Issue-Centered V2 (#56)

Generated: 2026-09-12T06:30:00Z  
Implementation commit: `c19379be1bef5ebcf84c9684765598888559b57b`  
Repair commit: (this commit)

## (1) Full migration — proof_gaps + lawyer_assessments with check constraints

Untruncated patch for `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (394 lines) included in:

`data/acceptance_reports/issue56_c19379b_key_files.patch`

**proof_gaps check constraints (verified in migration):**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`

**lawyer_assessments check constraints (verified in migration):**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`

Also includes FK to `(issue_key, issue_version)` and optional `(proof_task_key, proof_task_version)`.

## (2) Full domain + application files

Untruncated patches included in `issue56_c19379b_key_files.patch`:
- `backend/domain/issue_centered.py` (1062 lines)
- `backend/application/issue_work_product.py` (501 lines)

## (3) Test + live acceptance output

### pytest backend/tests/integration/test_issue_centered_v2.py

```
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.1.1, pluggy-1.6.0
collected 13 items

backend/tests/integration/test_issue_centered_v2.py::test_position_ai_candidate_lawyer_confirm PASSED
backend/tests/integration/test_issue_centered_v2.py::test_position_formal_defense_requires_material PASSED
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link PASSED
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_cross_case_rejected PASSED
backend/tests/integration/test_issue_centered_v2.py::test_conflict_and_gap_lawyer_actions PASSED
backend/tests/integration/test_issue_centered_v2.py::test_lawyer_assessment_ai_blocked PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_merge_and_split PASSED
backend/tests/integration/test_issue_centered_v2.py::test_claim_direction_production_disabled PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_proof_state PASSED
backend/tests/integration/test_issue_centered_v2.py::test_green_issue_not_auto_ready PASSED
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_api PASSED
backend/tests/integration/test_issue_centered_v2.py::test_workspace_includes_issue_work_product PASSED
backend/tests/integration/test_issue_centered_v2.py::test_structural_gap_renamed PASSED

======================== 13 passed, 2 warnings in 1.16s ========================
```

### live_issue_centered_v2_acceptance.py

```
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 15
  1 Case
  2 Material + Evidence accepted
  4 Fact confirmed
  5-6 Issue confirmed
  7-8 Positions
  9-11 ProofTask + SUPPORT fact
  12 ADVERSE fact link
  13 Conflict detected
  14 ProofGap created
  17-18 LawyerAssessment V1/V2
  16 gap waived
  19-21 Claim + links
  22 Readiness NOT_READY
  29 Issue Work Product
  30 Workspace + Agent context data
```

## (4) Explicit constraint verification

### No production ClaimDirection mutation

`backend/domain/services.py` `create_claim_direction()` raises `ValidationError("ClaimDirection production mutation disabled; use Claim Domain instead")` unless `_legacy_compat=True`. Test: `test_claim_direction_production_disabled`.

Production path `backend/application/claim_direction.py` calls `self.domain.create_claim_direction(...)` without `_legacy_compat` — blocked at domain layer.

### ProofTaskFactLink rejects implicit current/latest and cross-case links

`link_fact_to_proof_task()` in `backend/domain/issue_centered.py`:
- Requires explicit `proof_task_version` and `fact_version` (no get_current_* for linking)
- Validates `task.case_id != case_id` → `ValidationError("cross-case proof task link rejected")`
- Validates `fact.case_id != case_id` → `ValidationError("cross-case fact link rejected")`
- Tests: `test_proof_task_adopt_and_fact_link`, `test_proof_task_fact_link_cross_case_rejected`

### FORMAL_DEFENSE requires opponent_material_ref

`create_lawyer_position()` lines 135-137: if `position_type == FORMAL_DEFENSE` and not `opponent_material_ref`, raises `ValidationError("FORMAL_DEFENSE requires opponent material reference")`. Test: `test_position_formal_defense_requires_material`.

### merge/split emit HumanDecision + AuditLog

`merge_issues()` creates HumanDecision with `decision_type="MERGE_ISSUES"` before mutation, then `_audit(..., "merge_issues", ...)`.

`split_issue()` creates HumanDecision with `decision_type="SPLIT_ISSUE"` before mutation, then `_audit(..., "split_issue", ...)`.

Test: `test_issue_merge_and_split` (merge + split succeed with decision/audit paths wired).
