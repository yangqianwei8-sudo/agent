# Issue #73 Resubmit — Issue-Centered V2 (#60 repair)

Generated: 2026-09-13T07:15:30Z | Base: `e774f08` | Implementation: `c19379b` (on main) | Repair: `HEAD`

**SSOT:** `docs/issue73_repair_evidence/` — complete untruncated artifacts below.

| Artifact | Lines | Path |
|----------|-------|------|
| Full migration diff | 394 | `sources/migration_h9b0c1d2e3f4.py` |
| Full domain | 1062 | `sources/domain_issue_centered.py` |
| Full application | 501 | `sources/application_issue_work_product.py` |
| Untruncated git patch (c19379b) | 1983 | `issue60_c19379b_key_files.patch` |
| pytest output | — | `test_issue_centered_v2_output.txt` |
| live acceptance output | — | `live_issue_centered_v2_acceptance_output.txt` |

Production paths (identical content): `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py`, `backend/domain/issue_centered.py`, `backend/application/issue_work_product.py`.

## (A) Four required invariants — explicit verification

### A1. No production mutation path creates ClaimDirection

`backend/domain/services.py` `create_claim_direction()` raises `ValidationError("ClaimDirection production mutation disabled; use Claim Domain instead")` unless `_legacy_compat=True`.

Production `backend/application/claim_direction.py` calls `self.domain.create_claim_direction(...)` without `_legacy_compat` — blocked at domain layer.

**TEST:** `test_claim_direction_production_disabled` — `pytest.raises(ValidationError, match="ClaimDirection production")` — **PASSED**

### A2. ProofTaskFactLink rejects implicit current/latest and cross-case links

`backend/domain/issue_centered.py` `link_fact_to_proof_task()` (lines 407–428):
- Requires explicit `proof_task_version` and `fact_version` via `get_proof_task_version` / `get_fact_version` (NOT `get_current_*`)
- Nonexistent version → `NotFoundError("proof task version not found")` / `NotFoundError("fact version not found")`
- Cross-case → `ValidationError("cross-case proof task link rejected")` / `ValidationError("cross-case fact link rejected")`

**TESTS:**
- `test_proof_task_fact_link_rejects_implicit_version` — **PASSED** (version+99 → NotFoundError)
- `test_proof_task_fact_link_cross_case_rejected` — **PASSED** (ValidationError match "cross-case")
- `test_proof_task_adopt_and_fact_link` — **PASSED** (explicit versions required for successful link)

### A3. FORMAL_DEFENSE requires opponent_material_ref

`backend/domain/issue_centered.py` `create_lawyer_position()` lines 135–137:
```python
if position_type == PositionType.FORMAL_DEFENSE.value:
    if not opponent_material_ref:
        raise ValidationError("FORMAL_DEFENSE requires opponent material reference")
```

**TEST:** `test_position_formal_defense_requires_material` — `pytest.raises(ValidationError, match="FORMAL_DEFENSE")` — **PASSED**

### A4. merge/split emit HumanDecision + AuditLog

`merge_issues()` (lines 880–948): creates `HumanDecision` with `decision_type="MERGE_ISSUES"` before mutation, then `_audit(..., "merge_issues", ...)`.

`split_issue()` (lines 968–1027): creates `HumanDecision` with `decision_type="SPLIT_ISSUE"` before mutation, then `_audit(..., "split_issue", ...)`.

**TEST:** `test_issue_merge_and_split` asserts:
- `len(merge_decisions) == 1` where `decision_type == "MERGE_ISSUES"`
- `merge_audits` present where `action == "merge_issues"`
- `len(split_decisions) == 1` where `decision_type == "SPLIT_ISSUE"`
- `split_audits` present where `action == "split_issue"`
— **PASSED**

## (B) Test output — actual run 2026-09-13T07:15:30Z

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py -v`

**pytest:** 15 passed, 2 warnings in 1.37s — see `test_issue_centered_v2_output.txt`

**live acceptance:** `LLM_MODE=deterministic .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py` → **ISSUE-CENTERED CASE WORKSPACE V2: PASS** (30 steps) — see `live_issue_centered_v2_acceptance_output.txt`

## (C) Migration — proof_gaps + lawyer_assessments + all check constraints (complete, untruncated)

Full 394-line migration in `sources/migration_h9b0c1d2e3f4.py`. Key tables:

**proof_gaps check constraints:**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`
- FK: `(issue_key, issue_version)` → `issues`, optional `(proof_task_key, proof_task_version)` → `proof_tasks`

**lawyer_assessments check constraints:**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`
- FK: `(issue_key, issue_version)` → `issues`
- Unique: `(assessment_key, version)`

Also in migration: `proof_task_fact_links` (`ck_proof_task_fact_link_role`, `ck_proof_task_fact_link_status`), `issue_conflicts` (`ck_issue_conflicts_status`, `ck_issue_conflicts_source`), `conflict_fact_links` (`ck_conflict_fact_link_role`), `issue_positions` side/type/source/status constraints.

## (D) Domain + Application — full untruncated copies

- `backend/domain/issue_centered.py` — 1062 lines → `sources/domain_issue_centered.py`
- `backend/application/issue_work_product.py` — 501 lines → `sources/application_issue_work_product.py`

Verified by: `test_issue_work_product_api`, `test_workspace_includes_issue_work_product`, `test_agent_issue_object_context` — all **PASSED**.
