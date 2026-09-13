# Issue #60 Repair Evidence — Issue-Centered V2 (#57)

Generated: 2026-09-13T07:42:41Z  
Base commit: `6ecb000`  
Implementation commit: `c19379b` (on main)  
Repair commit: (this commit)

**This directory is the sole SSOT for Issue #60 repair submission.**

## Reviewer bundle (priority)

See `docs/issue60_repair_evidence/00_reviewer_bundle.md` for compact submission with all four invariants, test PASS output, and migration constraint summary.

## (1) Full alembic migration h9b0c1d2e3f4 — untruncated (production path)

| Location | Lines |
|----------|-------|
| Production | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (405) |
| SSOT copy | `sources/migration_h9b0c1d2e3f4.py` (405) |
| Implementation patch | `issue60_c19379b_key_files.patch` (1975 lines, untruncated) |
| Repair patch | `issue60_repair_production.patch` (257 lines) |

**proof_gaps** table with check constraints:
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`
- FK `fk_proof_gaps_issue` on `(issue_key, issue_version)`
- FK `fk_proof_gaps_proof_task` on `(proof_task_key, proof_task_version)` (optional)

**lawyer_assessments** table with check constraints:
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`
- FK `fk_lawyer_assessments_issue` on `(issue_key, issue_version)`
- Unique `uq_lawyer_assessments_key_version` on `(assessment_key, version)`

Additional check constraints in same migration: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `issue_legal_theory_links`.

## (2) Full backend/domain/issue_centered.py — untruncated (production path)

| Location | Lines |
|----------|-------|
| Production | `backend/domain/issue_centered.py` (1070) |
| SSOT copy | `sources/domain_issue_centered.py` (1070) |

Module docstring documents INV-2, INV-3, INV-4 enforcement points.

## (3) Full backend/application/issue_work_product.py — untruncated (production path)

| Location | Lines |
|----------|-------|
| Production | `backend/application/issue_work_product.py` (506) |
| SSOT copy | `sources/application_issue_work_product.py` (506) |

## (4) Captured test output — PASS

### pytest test_issue_centered_v2.py + test_issue_centered_v2_invariants.py

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full output: `test_issue_centered_v2_output.txt`

```
======================== 19 passed, 2 warnings in 1.64s ========================
```

### live_issue_centered_v2_acceptance.py

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full output: `live_issue_centered_v2_acceptance_output.txt`

```
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 30
```

## (5) Explicit assertions/verification for four invariants

Dedicated module: `backend/tests/integration/test_issue_centered_v2_invariants.py`  
SSOT copy: `sources/test_issue_centered_v2_invariants.py`

### 5.1 No production mutation path creates ClaimDirection

**Test:** `test_invariant_1_no_production_claim_direction_creation`
```python
with pytest.raises(ValidationError, match="ClaimDirection production"):
    svc.create_claim_direction(case_id=case.id, payload={"claims": [], "parties": {}}, actor_id=actor_id)
assert rows == []  # no ClaimDirection row created
```
**Result: PASSED**

### 5.2 ProofTaskFactLink rejects implicit current/latest and cross-case links

**Test:** `test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case`
- `proof_task_version=task.version+99` → `NotFoundError("proof task version not found")`
- `fact_version=fact.version+99` → `NotFoundError("fact version not found")`
- cross-case fact → `ValidationError(match="cross-case")`
**Result: PASSED**

### 5.3 FORMAL_DEFENSE requires opponent_material_ref

**Test:** `test_invariant_3_formal_defense_requires_opponent_material_ref`
```python
with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
    svc.create_lawyer_position(..., position_type="FORMAL_DEFENSE", opponent_material_ref=None)
assert pos.opponent_material_ref == "material:answer-001"  # succeeds with ref
```
**Result: PASSED**

### 5.4 merge/split emit HumanDecision + AuditLog

**Test:** `test_invariant_4_merge_split_emit_human_decision_and_audit_log`
```python
assert len(merge_decisions) == 1  # decision_type == "MERGE_ISSUES"
assert len(merge_audits) >= 1     # action == "merge_issues"
assert len(split_decisions) == 1  # decision_type == "SPLIT_ISSUE"
assert len(split_audits) >= 1     # action == "split_issue"
```
**Result: PASSED**

## Untruncated diff

Implementation patch: `issue60_c19379b_key_files.patch` (1975 lines, NOT truncated).  
Repair production patch: `issue60_repair_production.patch` (docstrings + invariant tests).
