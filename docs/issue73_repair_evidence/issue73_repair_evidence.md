# Issue #73 Repair Evidence — Issue-Centered V2 (#60)

Generated: 2026-09-13T07:15:30Z  
Base commit: `e774f08`  
Implementation commit: `c19379b` (on main)  
Repair commit: (this commit)

**This directory is the sole SSOT for Issue #73 repair submission.**

## Reviewer bundle (priority)

See `00_reviewer_bundle.md` for compact submission with all four invariants, test PASS output, and migration constraint summary.

## (1) Full alembic migration h9b0c1d2e3f4 — untruncated

| Location | Lines |
|----------|-------|
| Production | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (394) |
| SSOT copy | `sources/migration_h9b0c1d2e3f4.py` (394) |
| Git patch | `issue60_c19379b_key_files.patch` (lines 9–408, untruncated) |

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

Additional check constraints in same migration: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `proof_gaps`, `lawyer_assessments`.

## (2) Full backend/domain/issue_centered.py — untruncated

| Location | Lines |
|----------|-------|
| Production | `backend/domain/issue_centered.py` (1062) |
| SSOT copy | `sources/domain_issue_centered.py` (1062) |
| Git patch | `issue60_c19379b_key_files.patch` (lines 916–1983, untruncated) |

## (3) Full backend/application/issue_work_product.py — untruncated

| Location | Lines |
|----------|-------|
| Production | `backend/application/issue_work_product.py` (501) |
| SSOT copy | `sources/application_issue_work_product.py` (501) |
| Git patch | `issue60_c19379b_key_files.patch` (lines 409–915, untruncated) |

## (4) Captured test output — PASS

### pytest backend/tests/integration/test_issue_centered_v2.py

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py -v`

Full output: `test_issue_centered_v2_output.txt`

```
======================== 15 passed, 2 warnings in 1.37s ========================
```

All 15 tests PASSED including invariant tests:
- `test_claim_direction_production_disabled`
- `test_proof_task_fact_link_rejects_implicit_version`
- `test_proof_task_fact_link_cross_case_rejected`
- `test_position_formal_defense_requires_material`
- `test_issue_merge_and_split`

### live_issue_centered_v2_acceptance.py

Run: `LLM_MODE=deterministic .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full output: `live_issue_centered_v2_acceptance_output.txt`

```
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 30
```

## (5) Explicit assertions/verification for four invariants

### 5.1 No production mutation path creates ClaimDirection

**Domain guard** (`backend/domain/services.py` lines 919–922):
```python
if not _legacy_compat:
    raise ValidationError(
        "ClaimDirection production mutation disabled; use Claim Domain instead"
    )
```

**Production path blocked:** `backend/application/claim_direction.py` → `self.domain.create_claim_direction(...)` without `_legacy_compat`.

**Test assertion** (`test_claim_direction_production_disabled`):
```python
with pytest.raises(ValidationError, match="ClaimDirection production"):
    svc.create_claim_direction(case_id=case.id, payload={"claims": [], "parties": {}}, actor_id=actor_id)
```
**Result: PASSED**

### 5.2 ProofTaskFactLink rejects implicit current/latest and cross-case links

**Domain enforcement** (`link_fact_to_proof_task`, lines 419–428):
- Uses `get_proof_task_version(proof_task_key, proof_task_version)` — explicit version, not current/latest
- Uses `get_fact_version(fact_key, fact_version)` — explicit version, not current/latest
- Cross-case task: `ValidationError("cross-case proof task link rejected")`
- Cross-case fact: `ValidationError("cross-case fact link rejected")`

**Test assertions:**
- `test_proof_task_fact_link_rejects_implicit_version`: `proof_task_version=task.version+99` → `NotFoundError("proof task version not found")`; `fact_version=fact.version+99` → `NotFoundError("fact version not found")` — **PASSED**
- `test_proof_task_fact_link_cross_case_rejected`: fact from case C1 linked to task in case C2 → `ValidationError(match="cross-case")` — **PASSED**
- `test_proof_task_adopt_and_fact_link`: successful link requires explicit `proof_task_version=adopted.version` and `fact_version=fact.version` — **PASSED**

### 5.3 FORMAL_DEFENSE requires opponent_material_ref

**Domain enforcement** (`create_lawyer_position`, lines 135–137):
```python
if position_type == PositionType.FORMAL_DEFENSE.value:
    if not opponent_material_ref:
        raise ValidationError("FORMAL_DEFENSE requires opponent material reference")
```

**Test assertion** (`test_position_formal_defense_requires_material`):
```python
with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
    svc.create_lawyer_position(..., position_type="FORMAL_DEFENSE", ...)  # no opponent_material_ref
```
**Result: PASSED**

### 5.4 merge/split emit HumanDecision + AuditLog

**merge_issues** (lines 880–948): `HumanDecision(decision_type="MERGE_ISSUES")` persisted before mutation; `_audit(..., "merge_issues", ...)`.

**split_issue** (lines 968–1027): `HumanDecision(decision_type="SPLIT_ISSUE")` persisted before mutation; `_audit(..., "split_issue", ...)`.

**Test assertions** (`test_issue_merge_and_split`):
```python
assert len(merge_decisions) == 1  # decision_type == "MERGE_ISSUES"
assert merge_audits  # action == "merge_issues"
assert len(split_decisions) == 1  # decision_type == "SPLIT_ISSUE"
assert split_audits  # action == "split_issue"
```
**Result: PASSED**

## Untruncated diff

Complete git patch of implementation commit `c19379b` for all three key files: `issue60_c19379b_key_files.patch` (75531 bytes, 1983 lines, NOT truncated).
