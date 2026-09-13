# Issue #60 Resubmit — Issue-Centered V2 (#57 repair)

Generated: 2026-09-13T07:42:41Z | Base: `6ecb000` | Implementation: `c19379b` (on main)

**SSOT:** `docs/issue60_repair_evidence/` — complete untruncated artifacts below.

## Production artifacts (modified in repair)

| Artifact | Lines | Production path |
|----------|-------|-----------------|
| Full migration | 405 | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` |
| Full domain | 1070 | `backend/domain/issue_centered.py` |
| Full application | 506 | `backend/application/issue_work_product.py` |
| Invariant tests | 197 | `backend/tests/integration/test_issue_centered_v2_invariants.py` |
| Implementation patch (c19379b) | 1975 | `issue60_c19379b_key_files.patch` |
| Repair production patch | 257 | `issue60_repair_production.patch` |

SSOT copies (identical to production): `sources/migration_h9b0c1d2e3f4.py`, `sources/domain_issue_centered.py`, `sources/application_issue_work_product.py`, `sources/test_issue_centered_v2_invariants.py`.

## (A) Four required invariants — explicit code-level assertions

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
— **PASSED**

## (B) Test output — actual run 2026-09-13T07:42:41Z

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

```
======================== 19 passed, 2 warnings in 1.64s ========================
  test_position_ai_candidate_lawyer_confirm PASSED
  test_position_formal_defense_requires_material PASSED
  test_proof_task_adopt_and_fact_link PASSED
  test_proof_task_fact_link_rejects_implicit_version PASSED
  test_proof_task_fact_link_cross_case_rejected PASSED
  test_conflict_and_gap_lawyer_actions PASSED
  test_lawyer_assessment_ai_blocked PASSED
  test_issue_merge_and_split PASSED
  test_claim_direction_production_disabled PASSED
  test_issue_work_product_proof_state PASSED
  test_green_issue_not_auto_ready PASSED
  test_issue_work_product_api PASSED
  test_workspace_includes_issue_work_product PASSED
  test_agent_issue_object_context PASSED
  test_structural_gap_renamed PASSED
  test_invariant_1_no_production_claim_direction_creation PASSED
  test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case PASSED
  test_invariant_3_formal_defense_requires_opponent_material_ref PASSED
  test_invariant_4_merge_split_emit_human_decision_and_audit_log PASSED
```

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

```
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 30
```

Full captured output: `test_issue_centered_v2_output.txt`, `live_issue_centered_v2_acceptance_output.txt`.

## (C) Migration — proof_gaps + lawyer_assessments + all CheckConstraints (complete, untruncated)

Full migration in production path and `sources/migration_h9b0c1d2e3f4.py` (405 lines, NOT truncated).

**proof_gaps check constraints:**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`

**lawyer_assessments check constraints:**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`

Also: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `issue_legal_theory_links` — every CheckConstraint listed in migration module docstring.

## (D) Domain + Application — full untruncated production files

- `backend/domain/issue_centered.py` — 1070 lines (invariant docstring + full implementation)
- `backend/application/issue_work_product.py` — 506 lines (read-only projection)

Verified by: all 19 pytest tests + live acceptance 30 steps — **PASSED**.
