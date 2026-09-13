# Issue #60 Resubmit — Issue-Centered V2 (#57 repair)

Generated: 2026-09-13T07:11:56Z | Base: 9c3247f | Implementation: c19379b (on main)

Full untruncated copies: `docs/issue60_repair_evidence/sources/{migration_h9b0c1d2e3f4.py,domain_issue_centered.py,application_issue_work_product.py}`

Production paths: `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (394L), `backend/domain/issue_centered.py` (1062L), `backend/application/issue_work_product.py` (501L)

Git patch: `docs/issue60_repair_evidence/issue60_c19379b_key_files.patch` (1975L, untruncated)

## (A) Four required invariants — explicit verification

### A1. No production mutation path creates ClaimDirection

`backend/domain/services.py` `create_claim_direction()` raises `ValidationError` unless `_legacy_compat=True`.

Production `backend/application/claim_direction.py` calls without `_legacy_compat` — blocked.

**TEST:** `test_claim_direction_production_disabled` — PASSED (`pytest.raises ValidationError match "ClaimDirection production"`)

### A2. ProofTaskFactLink rejects implicit current/latest and cross-case links

`backend/domain/issue_centered.py` `link_fact_to_proof_task()` requires explicit `proof_task_version` + `fact_version` via `get_proof_task_version`/`get_fact_version` (NOT `get_current_*`).

Nonexistent version → `NotFoundError`; cross-case → `ValidationError("cross-case proof task link rejected"/"cross-case fact link rejected")`.

**TESTS:** `test_proof_task_fact_link_rejects_implicit_version` PASSED; `test_proof_task_fact_link_cross_case_rejected` PASSED; `test_proof_task_adopt_and_fact_link` PASSED.

### A3. FORMAL_DEFENSE requires opponent_material_ref

`backend/domain/issue_centered.py` `create_lawyer_position()` lines 135-137:

```python
if position_type == PositionType.FORMAL_DEFENSE.value:
    if not opponent_material_ref:
        raise ValidationError("FORMAL_DEFENSE requires opponent material reference")
```

**TEST:** `test_position_formal_defense_requires_material` PASSED.

### A4. merge/split emit HumanDecision + AuditLog

`merge_issues()` creates `HumanDecision` `decision_type="MERGE_ISSUES"` before mutation, then `_audit(...,"merge_issues",...)`.

`split_issue()` creates `HumanDecision` `decision_type="SPLIT_ISSUE"` before mutation, then `_audit(...,"split_issue",...)`.

**TEST:** `test_issue_merge_and_split` asserts `len(merge_decisions)==1`, `merge_audits` present, `len(split_decisions)==1`, `split_audits` present — PASSED.

## (B) Test output — actual run 2026-09-13T07:11:56Z

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py -v`

```
======================== 15 passed, 2 warnings in 3.03s ========================
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
```

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

```
ISSUE-CENTERED CASE WORKSPACE V2: PASS
Steps completed: 30
```

Full captured output: `test_issue_centered_v2_output.txt`, `live_issue_centered_v2_acceptance_output.txt`.

## (C) Migration — proof_gaps + lawyer_assessments + check constraints (complete, untruncated)

```python
    op.create_table(
        "proof_gaps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gap_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=True),
        sa.Column("proof_task_version", sa.Integer(), nullable=True),
        sa.Column("gap_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("what_exists", sa.Text(), nullable=True),
        sa.Column("what_is_missing", sa.Text(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("suggested_material_types", sa.JSON(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["issue_key", "issue_version"], ["issues.issue_key", "issues.version"], name="fk_proof_gaps_issue"),
        sa.ForeignKeyConstraint(["proof_task_key", "proof_task_version"], ["proof_tasks.proof_task_key", "proof_tasks.version"], name="fk_proof_gaps_proof_task"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')", name="ck_proof_gaps_type"),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')", name="ck_proof_gaps_status"),
        sa.CheckConstraint("source_type IN ('AI_DETECTED','LAWYER_CREATED')", name="ck_proof_gaps_source"),
    )
    op.create_table(
        "lawyer_assessments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("assessment_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["issue_key", "issue_version"], ["issues.issue_key", "issues.version"], name="fk_lawyer_assessments_issue"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["lawyer_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_key", "version", name="uq_lawyer_assessments_key_version"),
        sa.CheckConstraint("status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')", name="ck_lawyer_assessments_status"),
    )
```

Also in migration: `proof_task_fact_links` `ck_proof_task_fact_link_role`/`status`; `issue_conflicts` `ck_issue_conflicts_status`/`source`; `conflict_fact_links` `ck_conflict_fact_link_role`; `issue_positions` side/type/source/status constraints; `proof_tasks` status/source constraints; `issue_legal_theory_links` role/status constraints.

Full migration (394 lines): `sources/migration_h9b0c1d2e3f4.py`.

## (D) Domain — backend/domain/issue_centered.py (1062 lines, full copy in sources/)

```python
def create_lawyer_position(...):
    if position_type == PositionType.FORMAL_DEFENSE.value:
        if not opponent_material_ref:
            raise ValidationError("FORMAL_DEFENSE requires opponent material reference")

def link_fact_to_proof_task(..., proof_task_version: int, fact_version: int, ...):
    task = self.repo.get_proof_task_version(proof_task_key, proof_task_version)
    if task is None: raise NotFoundError("proof task version not found")
    if task.case_id != case_id: raise ValidationError("cross-case proof task link rejected")
    fact = self.repo.get_fact_version(fact_key, fact_version)
    if fact is None: raise NotFoundError("fact version not found")
    if fact.case_id != case_id: raise ValidationError("cross-case fact link rejected")

def merge_issues(...):
    decision = self._new_decision(..., decision_type="MERGE_ISSUES", ...)
    self.repo.add_decision(decision); self.repo.flush()
    ... # mutation
    self._audit(actor_id, "merge_issues", "issues", merged.id, ...)

def split_issue(...):
    decision = self._new_decision(..., decision_type="SPLIT_ISSUE", ...)
    self.repo.add_decision(decision); self.repo.flush()
    ... # mutation
    self._audit(actor_id, "split_issue", "issues", source.id, ...)
```

## (E) Application — backend/application/issue_work_product.py (501 lines, full copy in sources/)

`IssueWorkProductService` builds canonical read projection: positions, proof_tasks, conflicts, proof_gaps, lawyer_assessment, proof_state (RED/YELLOW/GREEN), litigation plan. Verified by `test_issue_work_product_api`, `test_workspace_includes_issue_work_product` PASSED.
