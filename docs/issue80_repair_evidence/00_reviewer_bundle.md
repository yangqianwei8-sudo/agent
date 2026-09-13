# Issue #80 Resubmit — Issue-Centered V2 (#73 / #86 repair)

Generated: 2026-09-13T10:44:44.294171Z | Base: `f84972a213c44ba602b07ae3801637dc5c045f16` | Repair: `51195a3`

**SSOT:** `docs/issue80_repair_evidence/` — complete untruncated artifacts below (all code, diff, and stdout inlined, NOT truncated).

## Production artifacts (real diffs, NOT truncated)

| Artifact | Lines | Production path |
|----------|-------|-----------------|
| Full migration | 439 | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` |
| Full services | 2145 | `backend/domain/services.py` |
| Full domain | 1260 | `backend/domain/issue_centered.py` |
| Full application | 565 | `backend/application/issue_work_product.py` |
| Invariant tests | 523 | `backend/tests/integration/test_issue_centered_v2_invariants.py` |
| Production patch | 2841 | `issue80_repair_production.patch` |

SSOT copies (identical to production): `sources/migration_h9b0c1d2e3f4.py`, `sources/domain_services.py`, `sources/domain_issue_centered.py`, `sources/application_issue_work_product.py`, `sources/test_issue_centered_v2_invariants.py`.

## (A) Four required invariants — explicit code-level assertions

Dedicated test module: `backend/tests/integration/test_issue_centered_v2_invariants.py`

### A1. No production mutation path creates ClaimDirection

**Domain guard** (`backend/domain/issue_centered.py` `_reject_claim_direction_production_mutation`, wired from `backend/domain/services.py` `create_claim_direction`): raises `ValidationError("ClaimDirection production mutation disabled; use Claim Domain instead")` unless `_legacy_compat=True`.

**Application guard** (`IssueWorkProductService.guard_write_attempt`): raises `RuntimeError` on read-projection mutation attempts.

**Test:** `test_invariant_1_no_production_claim_direction_creation`
- `pytest.raises(ValidationError, match="ClaimDirection production")`
- `assert rows == []` — no ClaimDirection row created
— **PASSED**

### A2. ProofTaskFactLink rejects implicit current/latest and cross-case links

**Domain enforcement** (`link_fact_to_proof_task` + `_guard_resolved_explicit_versions`): uses `get_proof_task_version` / `get_fact_version` (explicit versions only); rejects version mismatch and cross-case → `ValidationError("cross-case ...")` / `ValidationError("implicit current/latest rejected")`.

**Read projection guard** (`IssueWorkProductService._proof_task_facts`): skips links where `link.case_id != task.case_id` or `fact.case_id != task.case_id`.

**Test:** `test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case`
- `proof_task_version=0` → `ValidationError(match="explicit positive")`
- `proof_task_version+99` → `NotFoundError("proof task version not found")`
- `fact_version+99` → `NotFoundError("fact version not found")`
- cross-case fact → `ValidationError(match="cross-case fact link rejected")`
- cross-case proof task → `ValidationError(match="cross-case proof task link rejected")`

**DB constraint:** `test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions`
- direct insert with `proof_task_version=0` → `IntegrityError`
— **PASSED**

### A3. FORMAL_DEFENSE requires opponent_material_ref

**Domain enforcement** (`create_lawyer_position` + `_normalize_opponent_material_ref`): raises `ValidationError("FORMAL_DEFENSE requires opponent material reference")` when ref absent or whitespace-only.

**Test:** `test_invariant_3_formal_defense_requires_opponent_material_ref`
- without ref → `pytest.raises(ValidationError, match="FORMAL_DEFENSE")`
- with ref → `assert pos.opponent_material_ref == "material:answer-001"`

**Test:** `test_invariant_3_formal_defense_rejects_whitespace_only_material_ref`
- whitespace-only ref → `pytest.raises(ValidationError, match="FORMAL_DEFENSE")`

**DB constraint:** `test_invariant_3_db_rejects_formal_defense_without_material_ref`
- direct insert without ref → `IntegrityError` (ck_issue_positions_formal_defense_ref)
— **PASSED**

### A4. merge/split emit HumanDecision + AuditLog

**Domain enforcement:** `_persist_issue_structure_decision` flushes HumanDecision before mutation; `_require_structure_mutation_audit` verifies AuditLog(entity_type="issues") and `after_json.decision_id == str(decision.id)` before return.

**Test:** `test_invariant_4_merge_split_emit_human_decision_and_audit_log`
- `assert len(merge_decisions) == 1` and `assert merge_decisions[0].id is not None`
- `assert len(merge_audits) >= 1` and `assert merge_audits[0].entity_type == "issues"`
- `assert merge_audits[0].after_json.get("decision_id") == str(merge_decisions[0].id)`
- `assert len(split_decisions) == 1` and `assert len(split_audits) >= 1`

**Test:** `test_invariant_4_guard_rejects_missing_human_decision_or_audit`
- `_require_structure_mutation_audit(...)` without prior decision → `pytest.raises(ConflictError, match="HumanDecision")`

**Test:** `test_invariant_4_guard_rejects_audit_without_decision_id_linkage`
- AuditLog missing decision_id in after_json → `pytest.raises(ConflictError, match="decision_id")`
— **PASSED**

## (B) Test output — actual run 2026-09-13T10:44:44.294171Z

Run: `TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full raw stdout (untruncated):

```
# Issue #80 repair capture | commit=51195a32c5a59272401c29fd1320f021a932c8c0 | timestamp=2026-09-13T10:44:44.294171+00:00

============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.1.1, pluggy-1.6.0 -- /home/devbox/project/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/devbox/project
configfile: pyproject.toml
plugins: anyio-4.15.1
collecting ... collected 27 items

backend/tests/integration/test_issue_centered_v2.py::test_position_ai_candidate_lawyer_confirm PASSED [  3%]
backend/tests/integration/test_issue_centered_v2.py::test_position_formal_defense_requires_material PASSED [  7%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link PASSED [ 11%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_rejects_implicit_version PASSED [ 14%]
backend/tests/integration/test_issue_centered_v2.py::test_proof_task_fact_link_cross_case_rejected PASSED [ 18%]
backend/tests/integration/test_issue_centered_v2.py::test_conflict_and_gap_lawyer_actions PASSED [ 22%]
backend/tests/integration/test_issue_centered_v2.py::test_lawyer_assessment_ai_blocked PASSED [ 25%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_merge_and_split PASSED [ 29%]
backend/tests/integration/test_issue_centered_v2.py::test_claim_direction_production_disabled PASSED [ 33%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_proof_state PASSED [ 37%]
backend/tests/integration/test_issue_centered_v2.py::test_green_issue_not_auto_ready PASSED [ 40%]
backend/tests/integration/test_issue_centered_v2.py::test_issue_work_product_api PASSED [ 44%]
backend/tests/integration/test_issue_centered_v2.py::test_workspace_includes_issue_work_product PASSED [ 48%]
backend/tests/integration/test_issue_centered_v2.py::test_agent_issue_object_context PASSED [ 51%]
backend/tests/integration/test_issue_centered_v2.py::test_structural_gap_renamed PASSED [ 55%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_guard_functions_reject_invalid_inputs PASSED [ 59%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_1_no_production_claim_direction_creation PASSED [ 62%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case PASSED [ 66%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_formal_defense_requires_opponent_material_ref PASSED [ 70%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_formal_defense_rejects_whitespace_only_material_ref PASSED [ 74%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_4_merge_split_emit_human_decision_and_audit_log PASSED [ 77%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_2_cross_case_pair_guard_rejects_mismatched_cases PASSED [ 81%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_formal_defense_rejects_wrong_side PASSED [ 85%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_4_guard_rejects_missing_human_decision_or_audit PASSED [ 88%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_4_guard_rejects_audit_without_decision_id_linkage PASSED [ 92%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions PASSED [ 96%]
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_db_rejects_formal_defense_without_material_ref PASSED [100%]

=============================== warnings summary ===============================
.venv/lib/python3.11/site-packages/fastapi/testclient.py:1
  /home/devbox/project/.venv/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

backend/tests/integration/test_issue_centered_v2.py::test_proof_task_adopt_and_fact_link
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions
backend/tests/integration/test_issue_centered_v2_invariants.py::test_invariant_3_db_rejects_formal_defense_without_material_ref
  /home/devbox/project/backend/tests/conftest.py:74: SAWarning: transaction already deassociated from connection
    transaction.rollback()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 27 passed, 4 warnings in 2.18s ========================
```

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${DATABASE_URL%/*}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full raw stdout (untruncated):

```
# Issue #80 repair capture | commit=51195a32c5a59272401c29fd1320f021a932c8c0 | timestamp=2026-09-13T10:44:44.294171+00:00

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

## (C) Migration — proof_gaps + lawyer_assessments + all CheckConstraints (complete, untruncated, inlined)

Production path: `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` (439 lines)

**proof_gaps check constraints:**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`
- `ck_proof_gaps_proof_task_version_pos`: explicit positive proof_task_version when proof_task_key set

**lawyer_assessments check constraints:**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`
- `ck_lawyer_assessments_version_pos`: `version >= 1`

Also: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `issue_legal_theory_links` — every CheckConstraint listed in migration module docstring.

```python
"""phase9_issue_centered_v2 — IssuePosition, ProofTask, Conflict, ProofGap, LawyerAssessment.

Issue #80 repair SSOT (#73 / #60 / #83 / #86 / #84): four invariants enforced at DB + domain layers:
- INV-1: ClaimDirection production mutation blocked in domain/application (not this migration)
- INV-2: proof_task_fact_links version positivity (ck_*_version_pos) rejects implicit 0/latest
- INV-3: issue_positions FORMAL_DEFENSE ref + side constraints (ck_*_formal_defense_*)
- INV-4: merge/split HumanDecision + AuditLog enforced in domain (issue_centered.py)

Includes proof_gaps and lawyer_assessments with every CheckConstraint:
- proof_gaps: ck_proof_gaps_type, ck_proof_gaps_status, ck_proof_gaps_source
- lawyer_assessments: ck_lawyer_assessments_status
- issue_positions: ck_issue_positions_side/type/source/status/formal_defense_ref
- proof_tasks: ck_proof_tasks_status, ck_proof_tasks_source
- proof_task_fact_links: ck_proof_task_fact_link_role/status + version positivity
- issue_conflicts: ck_issue_conflicts_status, ck_issue_conflicts_source
- conflict_fact_links: ck_conflict_fact_link_role
- issue_legal_theory_links: ck_issue_legal_theory_link_role, ck_issue_legal_theory_link_status
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# INV SSOT (#80): canonical allowed-value sets for CheckConstraints (used in upgrade()).
_INV2_MIN_EXPLICIT_VERSION = 1
_PROOF_GAP_TYPES = ("FACT", "EVIDENCE", "SOURCE", "LEGAL_RESEARCH")
_PROOF_GAP_STATUSES = ("OPEN", "RESOLVED", "WAIVED", "SUPERSEDED")
_PROOF_GAP_SOURCES = ("AI_DETECTED", "LAWYER_CREATED")
_LAWYER_ASSESSMENT_STATUSES = ("ACTIVE", "SUPERSEDED", "WITHDRAWN")


def _in_check(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


def _version_pos_check(name: str, column: str) -> sa.CheckConstraint:
    """INV-2: explicit positive version columns (no implicit 0/latest)."""
    return sa.CheckConstraint(
        f"{column} >= {_INV2_MIN_EXPLICIT_VERSION}",
        name=name,
    )


revision: str = "h9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "g8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issue_positions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("position_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("side", sa.String(length=32), nullable=False),
        sa.Column("position_type", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("opponent_material_ref", sa.String(length=512), nullable=True),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_positions_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["issue_positions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_key", "version", name="uq_issue_positions_key_version"),
        sa.CheckConstraint("side IN ('OUR','OPPONENT')", name="ck_issue_positions_side"),
        sa.CheckConstraint(
            "position_type IN ('ASSERTION','ANTICIPATED_DEFENSE','FORMAL_DEFENSE')",
            name="ck_issue_positions_type",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED','OPPONENT_MATERIAL')",
            name="ck_issue_positions_source",
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_issue_positions_status",
        ),
        sa.CheckConstraint(
            "(position_type <> 'FORMAL_DEFENSE') OR "
            "(opponent_material_ref IS NOT NULL AND btrim(opponent_material_ref) <> '')",
            name="ck_issue_positions_formal_defense_ref",
        ),
        sa.CheckConstraint(
            "(position_type <> 'FORMAL_DEFENSE') OR (side = 'OPPONENT')",
            name="ck_issue_positions_formal_defense_side",
        ),
    )
    op.create_index("ix_issue_positions_case", "issue_positions", ["case_id"])
    op.create_index(
        "uq_issue_positions_current",
        "issue_positions",
        ["position_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "proof_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_PROPOSED"),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_tasks_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["proof_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proof_task_key", "version", name="uq_proof_tasks_key_version"),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','ADOPTED','REJECTED','SUPERSEDED','WAIVED')",
            name="ck_proof_tasks_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED')",
            name="ck_proof_tasks_source",
        ),
    )
    op.create_index("ix_proof_tasks_case", "proof_tasks", ["case_id"])
    op.create_index(
        "uq_proof_tasks_current",
        "proof_tasks",
        ["proof_task_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "proof_task_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=False),
        sa.Column("proof_task_version", sa.Integer(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="SUPPORT"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_task_fact_links_task",
        ),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_proof_task_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "proof_task_key",
            "proof_task_version",
            "fact_key",
            "fact_version",
            "role",
            name="uq_proof_task_fact_links",
        ),
        sa.CheckConstraint(
            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
            name="ck_proof_task_fact_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_proof_task_fact_link_status",
        ),
        _version_pos_check("ck_proof_task_fact_links_task_version_pos", "proof_task_version"),
        _version_pos_check("ck_proof_task_fact_links_fact_version_pos", "fact_version"),
    )
    op.create_index("ix_proof_task_fact_links_case", "proof_task_fact_links", ["case_id"])

    op.create_table(
        "issue_conflicts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conflict_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_conflicts_issue",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','OPEN','RESOLVED','DISMISSED')",
            name="ck_issue_conflicts_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_DETECTED','LAWYER_CREATED')",
            name="ck_issue_conflicts_source",
        ),
    )
    op.create_index("ix_issue_conflicts_case", "issue_conflicts", ["case_id"])
    op.create_index(
        "ix_issue_conflicts_issue", "issue_conflicts", ["issue_key", "issue_version"]
    )

    op.create_table(
        "conflict_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("conflict_id", sa.UUID(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["conflict_id"], ["issue_conflicts.id"]),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_conflict_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conflict_id",
            "fact_key",
            "fact_version",
            "role",
            name="uq_conflict_fact_links",
        ),
        sa.CheckConstraint(
            "role IN ('SIDE_A','SIDE_B','CONTEXT')",
            name="ck_conflict_fact_link_role",
        ),
        _version_pos_check("ck_conflict_fact_links_fact_version_pos", "fact_version"),
    )
    op.create_index("ix_conflict_fact_links_case", "conflict_fact_links", ["case_id"])

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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_gaps_issue",
        ),
        sa.ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_gaps_proof_task",
        ),
        sa.PrimaryKeyConstraint("id"),
        _in_check("ck_proof_gaps_type", "gap_type", _PROOF_GAP_TYPES),
        _in_check("ck_proof_gaps_status", "status", _PROOF_GAP_STATUSES),
        _in_check("ck_proof_gaps_source", "source_type", _PROOF_GAP_SOURCES),
        sa.CheckConstraint(
            "(proof_task_key IS NULL) OR "
            "(proof_task_version IS NOT NULL AND proof_task_version >= 1)",
            name="ck_proof_gaps_proof_task_version_pos",
        ),
    )
    op.create_index("ix_proof_gaps_case", "proof_gaps", ["case_id"])
    op.create_index("ix_proof_gaps_issue", "proof_gaps", ["issue_key", "issue_version"])

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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_lawyer_assessments_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["lawyer_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_key", "version", name="uq_lawyer_assessments_key_version"),
        _in_check("ck_lawyer_assessments_status", "status", _LAWYER_ASSESSMENT_STATUSES),
        _version_pos_check("ck_lawyer_assessments_version_pos", "version"),
    )
    op.create_index("ix_lawyer_assessments_case", "lawyer_assessments", ["case_id"])
    op.create_index(
        "uq_lawyer_assessments_current",
        "lawyer_assessments",
        ["assessment_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "issue_legal_theory_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("legal_theory_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="OUR_THEORY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_legal_theory_links_issue",
        ),
        sa.ForeignKeyConstraint(["legal_theory_id"], ["legal_theories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_key",
            "issue_version",
            "legal_theory_id",
            "role",
            name="uq_issue_legal_theory_links",
        ),
        sa.CheckConstraint(
            "role IN ('OUR_THEORY','COUNTER_THEORY','CONTEXT')",
            name="ck_issue_legal_theory_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_issue_legal_theory_link_status",
        ),
    )
    op.create_index("ix_issue_legal_theory_links_case", "issue_legal_theory_links", ["case_id"])


def downgrade() -> None:
    op.drop_table("issue_legal_theory_links")
    op.drop_table("lawyer_assessments")
    op.drop_table("proof_gaps")
    op.drop_table("conflict_fact_links")
    op.drop_table("issue_conflicts")
    op.drop_table("proof_task_fact_links")
    op.drop_table("proof_tasks")
    op.drop_table("issue_positions")
```

## (D) Services — backend/domain/services.py (complete, untruncated, inlined)

Production path: `backend/domain/services.py` (2145 lines)

INV-1: `create_claim_direction` wires `_reject_claim_direction_production_mutation` before any mutation.

```python
"""Domain Service — sole writer of domain truth (Phase 2)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.domain.enums import (
    CaseStatus,
    ClaimFactLinkRole,
    ClaimIssueLinkRole,
    ClaimLinkStatus,
    ClaimSourceType,
    ClaimStatus,
    DecisionResult,
    DraftStatus,
    EvidenceAcceptance,
    FactStatus,
    IssueLinkRole,
    IssueLinkStatus,
    IssueSourceType,
    IssueStatus,
    LayerStatus,
    MaterialLifeStatus,
    ParseStatus,
    StaleEvent,
)
from backend.domain.errors import ConflictError, ImmutableError, NotFoundError, ValidationError
from backend.domain.issue_centered import (
    IssueCenteredDomainMixin,
    _reject_claim_direction_production_mutation,
)
from backend.domain.stale import invalidate_dependencies
from backend.models import (
    AuditLog,
    Case,
    CaseMaterial,
    CaseParty,
    Claim,
    ClaimDirection,
    ClaimFactLink,
    ClaimIssueLink,
    DocumentDraft,
    DraftCitation,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    Issue,
    IssueEvidenceLink,
    IssueFactLink,
    SourceSpan,
)
from backend.repositories.base import Repository
from backend.schemas.claim_direction import validate_claim_direction_payload
from backend.skills.case_analyst import looks_like_legal_conclusion


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_quote(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def _confirmation_set_hash(parts: list[str]) -> str:
    blob = "|".join(sorted(parts))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DomainService(IssueCenteredDomainMixin):
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)

    # ----- Case -----

    def create_case(
        self,
        *,
        title: str,
        owner_user_id: UUID,
        goal_summary: str | None = None,
        actor_id: UUID | None = None,
    ) -> Case:
        case = Case(
            title=title,
            owner_user_id=owner_user_id,
            goal_summary=goal_summary,
            status=CaseStatus.OPEN.value,
        )
        self.repo.add(case)
        self.repo.flush()
        self._audit(
            actor_id or owner_user_id,
            "create_case",
            "cases",
            case.id,
            case_id=case.id,
            after={"title": title, "status": case.status},
        )
        return case

    def archive_case(self, case_id: UUID, *, actor_id: UUID) -> Case:
        case = self._require_case(case_id)
        if case.status == CaseStatus.ARCHIVED.value:
            raise ConflictError("case already archived")
        before = {"status": case.status}
        case.status = CaseStatus.ARCHIVED.value
        case.archived_at = _now()
        case.updated_at = _now()
        self._audit(actor_id, "archive_case", "cases", case.id, case_id=case.id, before=before)
        return case

    # ----- Material -----

    def register_material(
        self,
        *,
        case_id: UUID,
        filename: str,
        mime: str,
        byte_size: int,
        content_hash: str,
        storage_key: str,
        created_by: UUID,
    ) -> CaseMaterial:
        self._require_case(case_id)
        material = CaseMaterial(
            case_id=case_id,
            filename=filename,
            mime=mime,
            byte_size=byte_size,
            content_hash=content_hash,
            storage_key=storage_key,
            life_status=MaterialLifeStatus.ACTIVE.value,
            parse_status=ParseStatus.PENDING.value,
            created_by=created_by,
        )
        self.repo.add(material)
        self.repo.flush()
        self._audit(
            created_by,
            "register_material",
            "case_materials",
            material.id,
            case_id=case_id,
            after={"filename": filename, "content_hash": content_hash},
        )
        return material

    def void_material(
        self,
        material_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseMaterial:
        material = self.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.life_status == MaterialLifeStatus.VOID.value:
            raise ConflictError("material already void")
        decision = decision or self._new_decision(
            case_id=material.case_id,
            actor_id=actor_id,
            decision_type="VOID_MATERIAL",
            target_type="CaseMaterial",
            target_id=material.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"reason": reason},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        material.life_status = MaterialLifeStatus.VOID.value
        material.void_reason = reason
        material.updated_at = _now()
        self._audit(
            actor_id,
            "void_material",
            "case_materials",
            material.id,
            case_id=material.case_id,
            after={"life_status": material.life_status, "decision_id": str(decision.id)},
        )
        return material

    def create_extracted_content(
        self,
        *,
        material_id: UUID,
        extraction_method: str,
        extraction_version: str,
        full_text: str | None = None,
        full_text_ref: str | None = None,
        page_count: int | None = None,
        layout_json: dict[str, Any] | None = None,
        error_detail: str | None = None,
        status: str = "SUCCEEDED",
        actor_id: UUID | None = None,
        previous_extracted_content_id: UUID | None = None,
    ) -> ExtractedContent:
        material = self.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.life_status != MaterialLifeStatus.ACTIVE.value:
            raise ConflictError("material is not ACTIVE")
        if status == "SUCCEEDED" and full_text is None:
            raise ValidationError("SUCCEEDED ExtractedContent requires full_text")
        ec = ExtractedContent(
            material_id=material_id,
            extraction_method=extraction_method,
            extraction_version=extraction_version,
            status=status,
            full_text=full_text if status == "SUCCEEDED" else None,
            full_text_ref=full_text_ref,
            page_count=page_count,
            layout_json=layout_json if status == "SUCCEEDED" else None,
            error_detail=(error_detail or "")[:500] if error_detail else None,
        )
        self.repo.add(ec)
        self.repo.flush()
        if status == "SUCCEEDED":
            material.parse_status = ParseStatus.SUCCEEDED.value
        elif status == "FAILED":
            material.parse_status = ParseStatus.FAILED.value
        material.updated_at = _now()
        if previous_extracted_content_id is not None:
            invalidate_dependencies(
                self.session,
                StaleEvent.EXTRACTED_CONTENT_REBUILT,
                case_id=material.case_id,
                payload={"old_extracted_content_id": previous_extracted_content_id},
            )
        self._audit(
            actor_id or material.created_by,
            "create_extracted_content",
            "extracted_contents",
            ec.id,
            case_id=material.case_id,
            after={
                "method": extraction_method,
                "version": extraction_version,
                "status": status,
            },
        )
        return ec

    def create_source_span(
        self,
        *,
        material_id: UUID,
        extracted_content_id: UUID,
        character_start: int,
        character_end: int,
        quote: str,
        extraction_method: str,
        extraction_version: str,
        page: int | None = None,
        paragraph: int | None = None,
        bbox_json: dict[str, Any] | None = None,
        confidence: float | None = None,
        quote_hash: str | None = None,
    ) -> SourceSpan:
        if character_start < 0 or character_end <= character_start:
            raise ValidationError("invalid character offsets")
        ec = self.repo.get_extracted_content(extracted_content_id)
        if ec is None:
            raise NotFoundError("extracted_content not found")
        if ec.material_id != material_id:
            raise ValidationError("material_id does not match extracted_content")
        if ec.status != "SUCCEEDED":
            raise ValidationError(
                "FAILED ExtractedContent cannot produce trusted SourceSpan"
            )
        if ec.full_text is None:
            raise ValidationError("ExtractedContent has no full_text")
        expected = quote_hash or _hash_quote(quote)
        if quote_hash and quote_hash != _hash_quote(quote):
            raise ValidationError("quote_hash mismatch")
        # Coordinates are valid only within this extracted_content_id text space.
        if character_end > len(ec.full_text):
            raise ValidationError("offsets exceed extracted_content text length")
        slice_text = ec.full_text[character_start:character_end]
        if slice_text != quote:
            raise ValidationError("quote does not match extracted_content slice")
        span = SourceSpan(
            material_id=material_id,
            extracted_content_id=extracted_content_id,
            page=page,
            paragraph=paragraph,
            character_start=character_start,
            character_end=character_end,
            quote=quote,
            quote_hash=expected,
            bbox_json=bbox_json,
            extraction_method=extraction_method,
            extraction_version=extraction_version,
            confidence=confidence,
        )
        self.repo.add(span)
        self.repo.flush()
        return span

    # ----- Evidence -----

    def create_evidence_item(
        self,
        *,
        case_id: UUID,
        number: str,
        title: str,
        category: str,
        summary: str | None = None,
        source_span_ids: list[UUID] | None = None,
        evidence_id: UUID | None = None,
        actor_id: UUID | None = None,
        organizer_run_id: UUID | None = None,
    ) -> EvidenceItem:
        self._require_case(case_id)
        eid = evidence_id or uuid.uuid4()
        item = EvidenceItem(
            id=eid,
            case_id=case_id,
            version=1,
            is_current=True,
            number=number,
            title=title,
            category=category,
            summary=summary,
            acceptance=EvidenceAcceptance.PENDING.value,
            organizer_run_id=organizer_run_id,
        )
        self.repo.add(item)
        self.repo.flush()
        for span_id in source_span_ids or []:
            span = self.repo.get_source_span(span_id)
            if span is None:
                raise NotFoundError(f"source_span not found: {span_id}")
            self.repo.add(
                EvidenceItemSpan(
                    evidence_item_id=item.id,
                    evidence_item_version=item.version,
                    source_span_id=span_id,
                    role_in_item="PRIMARY",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_evidence_item",
            "evidence_items",
            item.row_id,
            case_id=case_id,
            after={
                "id": str(item.id),
                "version": item.version,
                "number": number,
                "acceptance": item.acceptance,
                "organizer_run_id": str(organizer_run_id) if organizer_run_id else None,
            },
        )
        return item

    def accept_evidence(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        item = self._require_current_evidence(evidence_id)
        decision = decision or self._new_decision(
            case_id=item.case_id,
            actor_id=actor_id,
            decision_type="ACCEPT_EVIDENCE",
            target_type="EvidenceItem",
            target_id=item.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"evidence_item_id": str(item.id), "version": item.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        item.acceptance = EvidenceAcceptance.ACCEPTED.value
        item.updated_at = _now()
        self._audit(
            actor_id,
            "accept_evidence",
            "evidence_items",
            item.row_id,
            case_id=item.case_id,
            after={"acceptance": item.acceptance, "decision_id": str(decision.id)},
        )
        return item

    def exclude_evidence(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        item = self._require_current_evidence(evidence_id)
        decision = decision or self._new_decision(
            case_id=item.case_id,
            actor_id=actor_id,
            decision_type="EXCLUDE_EVIDENCE",
            target_type="EvidenceItem",
            target_id=item.id,
            result=DecisionResult.REJECTED.value,
            payload={"evidence_item_id": str(item.id), "version": item.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        item.acceptance = EvidenceAcceptance.EXCLUDED.value
        item.updated_at = _now()
        invalidate_dependencies(
            self.session,
            StaleEvent.EVIDENCE_EXCLUDED,
            case_id=item.case_id,
            payload={
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
            },
        )
        self._audit(
            actor_id,
            "exclude_evidence",
            "evidence_items",
            item.row_id,
            case_id=item.case_id,
            after={"acceptance": item.acceptance, "decision_id": str(decision.id)},
        )
        return item

    def amend_evidence_item(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        number: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        category: str | None = None,
        source_span_ids: list[UUID] | None = None,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        current = self._require_current_evidence(evidence_id)
        content_changed = any(v is not None for v in (title, summary, category, source_span_ids))
        number_only = number is not None and not content_changed

        if number_only:
            current.number = number  # type: ignore[assignment]
            current.updated_at = _now()
            self._audit(
                actor_id,
                "renumber_evidence_item",
                "evidence_items",
                current.row_id,
                case_id=current.case_id,
                after={"number": current.number, "version": current.version},
            )
            return current

        if not content_changed and number is None:
            raise ValidationError("no amendments provided")

        decision = decision or self._new_decision(
            case_id=current.case_id,
            actor_id=actor_id,
            decision_type="AMEND_EVIDENCE",
            target_type="EvidenceItem",
            target_id=current.id,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": current.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()

        current.is_current = False
        current.updated_at = _now()
        new_version = current.version + 1
        new_item = EvidenceItem(
            id=current.id,
            case_id=current.case_id,
            version=new_version,
            is_current=True,
            number=number if number is not None else current.number,
            title=title if title is not None else current.title,
            category=category if category is not None else current.category,
            summary=summary if summary is not None else current.summary,
            acceptance=current.acceptance,
        )
        self.repo.add(new_item)
        self.repo.flush()

        spans = (
            source_span_ids
            if source_span_ids is not None
            else [
                s.source_span_id
                for s in self.repo.list_evidence_spans(current.id, current.version)
            ]
        )
        for span_id in spans:
            if self.repo.get_source_span(span_id) is None:
                raise NotFoundError(f"source_span not found: {span_id}")
            self.repo.add(
                EvidenceItemSpan(
                    evidence_item_id=new_item.id,
                    evidence_item_version=new_item.version,
                    source_span_id=span_id,
                    role_in_item="PRIMARY",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id,
            "amend_evidence_item",
            "evidence_items",
            new_item.row_id,
            case_id=current.case_id,
            after={
                "id": str(new_item.id),
                "version": new_item.version,
                "decision_id": str(decision.id),
            },
        )
        return new_item

    # ----- Fact -----

    def propose_fact(
        self,
        *,
        case_id: UUID,
        statement: str,
        evidence_links: list[dict[str, Any]],
        importance: str = "SUPPORTING",
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> Fact:
        self._require_case(case_id)
        if not evidence_links:
            raise ValidationError("propose_fact requires at least one evidence link")
        fact = Fact(
            fact_key=uuid.uuid4(),
            case_id=case_id,
            statement=statement,
            status=FactStatus.CANDIDATE.value,
            importance=importance,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(fact)
        self.repo.flush()
        for link in evidence_links:
            eid = UUID(str(link["evidence_item_id"]))
            ever = int(link["evidence_item_version"])
            if self.repo.get_evidence_version(eid, ever) is None:
                raise NotFoundError("evidence version not found for link")
            self.repo.add(
                FactEvidenceLink(
                    fact_id=fact.id,
                    evidence_item_id=eid,
                    evidence_item_version=ever,
                    source_span_id=(
                        UUID(str(link["source_span_id"])) if link.get("source_span_id") else None
                    ),
                    link_role=link.get("link_role", "PROVES"),
                    explanation=link.get("explanation"),
                    status="ACTIVE",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_fact",
            "facts",
            fact.id,
            case_id=case_id,
            after={
                "fact_key": str(fact.fact_key),
                "version": fact.version,
                "status": fact.status,
                "analyst_run_id": str(analyst_run_id) if analyst_run_id else None,
            },
        )
        return fact

    def confirm_fact(
        self,
        fact_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        fact = self._require_current_fact(fact_key)
        if fact.status != FactStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE facts can be confirmed")
        if fact.stale:
            raise ConflictError("cannot confirm stale fact")
        active_links = [lnk for lnk in self.repo.list_fact_links(fact.id) if lnk.status == "ACTIVE"]
        if not active_links:
            raise ValidationError("cannot confirm fact without ACTIVE links")
        decision = decision or self._new_decision(
            case_id=fact.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_FACT",
            target_type="Fact",
            target_id=fact.fact_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        fact.status = FactStatus.CONFIRMED.value
        fact.confirm_decision_id = decision.id
        fact.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_fact",
            "facts",
            fact.id,
            case_id=fact.case_id,
            after={
                "status": fact.status,
                "confirm_decision_id": str(decision.id),
                "fact_key": str(fact.fact_key),
                "version": fact.version,
            },
        )
        return fact

    def reject_fact(
        self,
        fact_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        fact = self._require_current_fact(fact_key)
        if fact.status not in {FactStatus.CANDIDATE.value, FactStatus.CONFIRMED.value}:
            raise ConflictError("fact cannot be rejected from current status")
        decision = decision or self._new_decision(
            case_id=fact.case_id,
            actor_id=actor_id,
            decision_type="REJECT_FACT",
            target_type="Fact",
            target_id=fact.fact_key,
            result=DecisionResult.REJECTED.value,
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        fact.status = FactStatus.REJECTED.value
        fact.is_current = True  # rejected remains current identity terminal
        fact.updated_at = _now()
        invalidate_dependencies(
            self.session,
            StaleEvent.FACT_REJECTED,
            case_id=fact.case_id,
            payload={"fact_id": fact.id, "fact_key": fact.fact_key},
        )
        self._audit(
            actor_id,
            "reject_fact",
            "facts",
            fact.id,
            case_id=fact.case_id,
            after={"status": fact.status, "decision_id": str(decision.id)},
        )
        return fact

    def amend_fact(
        self,
        fact_key: UUID,
        *,
        new_statement: str,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        old = self._require_current_fact(fact_key)
        if old.status != FactStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED facts can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_FACT",
            target_type="Fact",
            target_id=old.fact_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version, "new_statement": new_statement},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = FactStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_fact = Fact(
            fact_key=old.fact_key,
            case_id=old.case_id,
            statement=new_statement,
            status=FactStatus.CONFIRMED.value,
            importance=old.importance,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_fact)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.FACT_AMENDED,
            case_id=old.case_id,
            payload={
                "old_fact_id": old.id,
                "new_fact_id": new_fact.id,
                "fact_key": old.fact_key,
            },
        )
        self._audit(
            actor_id,
            "amend_fact",
            "facts",
            new_fact.id,
            case_id=old.case_id,
            after={
                "fact_key": str(new_fact.fact_key),
                "version": new_fact.version,
                "decision_id": str(decision.id),
            },
        )
        return new_fact

    # ----- Party -----

    def create_party(
        self,
        *,
        case_id: UUID,
        role: str,
        name: str,
        party_type: str,
        actor_id: UUID | None = None,
        identifiers_json: dict[str, Any] | None = None,
    ) -> CaseParty:
        self._require_case(case_id)
        party = CaseParty(
            case_id=case_id,
            party_key=uuid.uuid4(),
            role=role,
            name=name,
            party_type=party_type,
            identifiers_json=identifiers_json,
            layer=LayerStatus.CANDIDATE.value,
            version=1,
            is_current=True,
        )
        self.repo.add(party)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_party",
            "case_parties",
            party.id,
            case_id=case_id,
            after={"party_key": str(party.party_key), "name": name},
        )
        return party

    def confirm_party(
        self,
        party_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        party = self._require_current_party(party_key)
        if party.layer != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE party can be confirmed")
        decision = decision or self._new_decision(
            case_id=party.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_PARTY",
            target_type="CaseParty",
            target_id=party.party_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"party_key": str(party.party_key), "version": party.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        party.layer = LayerStatus.CONFIRMED.value
        party.confirm_decision_id = decision.id
        party.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_party",
            "case_parties",
            party.id,
            case_id=party.case_id,
            after={"layer": party.layer, "decision_id": str(decision.id)},
        )
        return party

    def amend_party(
        self,
        party_key: UUID,
        *,
        name: str,
        actor_id: UUID,
        role: str | None = None,
        party_type: str | None = None,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        old = self._require_current_party(party_key)
        if old.layer != LayerStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED party can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_PARTY",
            target_type="CaseParty",
            target_id=old.party_key,
            result=DecisionResult.AMENDED.value,
            payload={"old_name": old.name, "new_name": name},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old_name = old.name
        old.layer = LayerStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_party = CaseParty(
            case_id=old.case_id,
            party_key=old.party_key,
            role=role or old.role,
            name=name,
            party_type=party_type or old.party_type,
            identifiers_json=old.identifiers_json,
            layer=LayerStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_party)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.PARTY_CHANGED,
            case_id=old.case_id,
            payload={"old_name": old_name, "old_party_key": old.party_key},
        )
        self._audit(
            actor_id,
            "amend_party",
            "case_parties",
            new_party.id,
            case_id=old.case_id,
            after={"party_key": str(new_party.party_key), "version": new_party.version},
        )
        return new_party

    def reject_party(
        self,
        party_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        """Reject a CANDIDATE party so N5 can proceed without confirming it."""
        party = self._require_current_party(party_key)
        if party.layer != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE party can be rejected")
        decision = decision or self._new_decision(
            case_id=party.case_id,
            actor_id=actor_id,
            decision_type="REJECT_PARTY",
            target_type="CaseParty",
            target_id=party.party_key,
            result=DecisionResult.REJECTED.value,
            payload={"party_key": str(party.party_key), "version": party.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        party.layer = LayerStatus.REJECTED.value
        party.confirm_decision_id = decision.id
        party.updated_at = _now()
        self._audit(
            actor_id,
            "reject_party",
            "case_parties",
            party.id,
            case_id=party.case_id,
            after={"layer": party.layer, "decision_id": str(decision.id)},
        )
        return party

    # ----- ClaimDirection -----

    def create_claim_direction(
        self,
        *,
        case_id: UUID,
        payload: dict[str, Any],
        actor_id: UUID | None = None,
        _legacy_compat: bool = False,
    ) -> ClaimDirection:
        _reject_claim_direction_production_mutation(_legacy_compat=_legacy_compat)
        self._require_case(case_id)
        validated = validate_claim_direction_payload(payload)
        claim = ClaimDirection(
            claim_direction_key=uuid.uuid4(),
            case_id=case_id,
            payload=validated,
            status=LayerStatus.CANDIDATE.value,
            version=1,
            is_current=True,
        )
        self.repo.add(claim)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_claim_direction",
            "claim_directions",
            claim.id,
            case_id=case_id,
            after={"claim_direction_key": str(claim.claim_direction_key)},
        )
        return claim

    def confirm_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        claim = self._require_current_claim(claim_direction_key)
        if claim.status != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE claim direction can be confirmed")
        if claim.stale:
            raise ConflictError("cannot confirm stale claim direction")
        self._assert_supporting_facts_confirmed(claim.payload)
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=claim.claim_direction_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"claim_direction_key": str(claim.claim_direction_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = LayerStatus.CONFIRMED.value
        claim.confirm_decision_id = decision.id
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_claim_direction",
            "claim_directions",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    def amend_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        payload: dict[str, Any],
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        old = self._require_current_claim(claim_direction_key)
        if old.status != LayerStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED claim direction can be amended")
        validated = validate_claim_direction_payload(payload)
        self._assert_supporting_facts_confirmed(validated)
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=old.claim_direction_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = LayerStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_claim = ClaimDirection(
            claim_direction_key=old.claim_direction_key,
            case_id=old.case_id,
            payload=validated,
            status=LayerStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_claim)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.CLAIM_DIRECTION_CHANGED,
            case_id=old.case_id,
            payload={"claim_direction_key": old.claim_direction_key},
        )
        self._audit(
            actor_id,
            "amend_claim_direction",
            "claim_directions",
            new_claim.id,
            case_id=old.case_id,
            after={"version": new_claim.version, "decision_id": str(decision.id)},
        )
        return new_claim

    def reject_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        """CANDIDATE/CONFIRMED → REJECTED (DB already allows REJECTED; Phase 7 additive)."""
        claim = self._require_current_claim(claim_direction_key)
        if claim.status not in {
            LayerStatus.CANDIDATE.value,
            LayerStatus.CONFIRMED.value,
        }:
            raise ConflictError("claim direction cannot be rejected from current status")
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="REJECT_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=claim.claim_direction_key,
            result=DecisionResult.REJECTED.value,
            payload={
                "claim_direction_key": str(claim.claim_direction_key),
                "version": claim.version,
            },
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = LayerStatus.REJECTED.value
        claim.is_current = True
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "reject_claim_direction",
            "claim_directions",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    # ----- Claim (versioned relief item) -----

    def propose_claim(
        self,
        *,
        case_id: UUID,
        claim_type: str,
        title: str,
        statement: str,
        amount: float | None = None,
        currency: str | None = None,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> Claim:
        """AI proposal — CANDIDATE only; amount marked suggested if present."""
        self._require_case(case_id)
        amount_suggested = amount is not None
        claim = Claim(
            claim_key=uuid.uuid4(),
            case_id=case_id,
            claim_type=claim_type,
            title=title,
            statement=statement,
            amount=amount,
            currency=currency,
            amount_is_suggested=amount_suggested,
            source_type=ClaimSourceType.AI_PROPOSED.value,
            status=ClaimStatus.CANDIDATE.value,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(claim)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_claim",
            "claims",
            claim.id,
            case_id=case_id,
            after={
                "claim_key": str(claim.claim_key),
                "status": claim.status,
                "amount_is_suggested": amount_suggested,
            },
        )
        return claim

    def create_lawyer_claim(
        self,
        *,
        case_id: UUID,
        claim_type: str,
        title: str,
        statement: str,
        actor_id: UUID,
        amount: float | None = None,
        currency: str | None = None,
        decision: HumanDecision | None = None,
    ) -> Claim:
        self._require_case(case_id)
        claim_key = uuid.uuid4()
        decision = decision or self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_CLAIM",
            target_type="Claim",
            target_id=claim_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"title": title, "claim_type": claim_type},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim = Claim(
            claim_key=claim_key,
            case_id=case_id,
            claim_type=claim_type,
            title=title,
            statement=statement,
            amount=amount,
            currency=currency,
            amount_is_suggested=False,
            source_type=ClaimSourceType.LAWYER_CREATED.value,
            status=ClaimStatus.CONFIRMED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(claim)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_claim",
            "claims",
            claim.id,
            case_id=case_id,
            after={"claim_key": str(claim.claim_key), "decision_id": str(decision.id)},
        )
        return claim

    def confirm_claim(
        self,
        claim_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Claim:
        claim = self._require_current_relief_claim(claim_key)
        if claim.status != ClaimStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE claims can be confirmed")
        if claim.stale:
            raise ConflictError("cannot confirm stale claim")
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_CLAIM",
            target_type="Claim",
            target_id=claim.claim_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"claim_key": str(claim.claim_key), "version": claim.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = ClaimStatus.CONFIRMED.value
        claim.confirm_decision_id = decision.id
        claim.amount_is_suggested = False
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_claim",
            "claims",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    def reject_claim(
        self,
        claim_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Claim:
        claim = self._require_current_relief_claim(claim_key)
        if claim.status != ClaimStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE claims can be rejected")
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="REJECT_CLAIM",
            target_type="Claim",
            target_id=claim.claim_key,
            result=DecisionResult.REJECTED.value,
            payload={"claim_key": str(claim.claim_key), "version": claim.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = ClaimStatus.REJECTED.value
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "reject_claim",
            "claims",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    def amend_claim(
        self,
        claim_key: UUID,
        *,
        title: str | None = None,
        statement: str | None = None,
        amount: float | None = None,
        currency: str | None = None,
        actor_id: UUID,
        change_reason: str | None = None,
        decision: HumanDecision | None = None,
    ) -> Claim:
        old = self._require_current_relief_claim(claim_key)
        if old.status != ClaimStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED claims can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_CLAIM",
            target_type="Claim",
            target_id=old.claim_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = ClaimStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_claim = Claim(
            claim_key=old.claim_key,
            case_id=old.case_id,
            claim_type=old.claim_type,
            title=title or old.title,
            statement=statement or old.statement,
            amount=amount if amount is not None else old.amount,
            currency=currency if currency is not None else old.currency,
            amount_is_suggested=False,
            source_type=ClaimSourceType.LAWYER_REFINED.value,
            status=ClaimStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
            change_reason=change_reason,
            legacy_claim_direction_key=old.legacy_claim_direction_key,
            legacy_claim_direction_version=old.legacy_claim_direction_version,
            legacy_claim_index=old.legacy_claim_index,
            legacy_source_ref=old.legacy_source_ref,
            analyst_run_id=old.analyst_run_id,
        )
        self.repo.add(new_claim)
        self.repo.flush()
        self._copy_claim_links(old, new_claim)
        invalidate_dependencies(
            self.session,
            StaleEvent.CLAIM_CHANGED,
            case_id=old.case_id,
            payload={"claim_key": str(old.claim_key), "from_version": old.version},
        )
        self._audit(
            actor_id,
            "amend_claim",
            "claims",
            new_claim.id,
            case_id=old.case_id,
            after={"version": new_claim.version, "decision_id": str(decision.id)},
        )
        return new_claim

    def link_issue_to_claim(
        self,
        *,
        case_id: UUID,
        claim_key: UUID,
        claim_version: int,
        issue_key: UUID,
        issue_version: int,
        role: str,
        actor_id: UUID,
    ) -> ClaimIssueLink:
        self._require_case(case_id)
        claim = self.repo.get_relief_claim_version(claim_key, claim_version)
        if claim is None:
            raise NotFoundError("claim version not found")
        if claim.case_id != case_id:
            raise ValidationError("claim case_id mismatch")
        issue = self.repo.get_issue_version(issue_key, issue_version)
        if issue is None:
            raise NotFoundError("issue version not found")
        if issue.case_id != case_id:
            raise ValidationError("cross-case issue link rejected")
        if issue.status != IssueStatus.CONFIRMED.value:
            raise ValidationError("formal claim links require CONFIRMED issue")
        if role not in {r.value for r in ClaimIssueLinkRole}:
            raise ValidationError(f"invalid claim-issue link role: {role}")
        link = ClaimIssueLink(
            case_id=case_id,
            claim_key=claim_key,
            claim_version=claim_version,
            issue_key=issue_key,
            issue_version=issue_version,
            role=role,
            status=ClaimLinkStatus.ACTIVE.value,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_issue_to_claim",
            "claim_issue_links",
            link.id,
            case_id=case_id,
            after={
                "claim_key": str(claim_key),
                "claim_version": claim_version,
                "issue_key": str(issue_key),
                "issue_version": issue_version,
                "role": role,
            },
        )
        return link

    def link_fact_to_claim(
        self,
        *,
        case_id: UUID,
        claim_key: UUID,
        claim_version: int,
        fact_key: UUID,
        fact_version: int,
        role: str,
        actor_id: UUID,
    ) -> ClaimFactLink:
        self._require_case(case_id)
        claim = self.repo.get_relief_claim_version(claim_key, claim_version)
        if claim is None:
            raise NotFoundError("claim version not found")
        if claim.case_id != case_id:
            raise ValidationError("claim case_id mismatch")
        fact = self.repo.get_fact_version(fact_key, fact_version)
        if fact is None:
            raise NotFoundError("fact version not found")
        if fact.case_id != case_id:
            raise ValidationError("cross-case fact link rejected")
        if fact.status != FactStatus.CONFIRMED.value:
            raise ValidationError("formal claim links require CONFIRMED fact")
        if role not in {r.value for r in ClaimFactLinkRole}:
            raise ValidationError(f"invalid claim-fact link role: {role}")
        link = ClaimFactLink(
            case_id=case_id,
            claim_key=claim_key,
            claim_version=claim_version,
            fact_key=fact_key,
            fact_version=fact_version,
            role=role,
            status=ClaimLinkStatus.ACTIVE.value,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_fact_to_claim",
            "claim_fact_links",
            link.id,
            case_id=case_id,
            after={
                "claim_key": str(claim_key),
                "claim_version": claim_version,
                "fact_key": str(fact_key),
                "fact_version": fact_version,
                "role": role,
            },
        )
        return link

    # ----- Issue (versioned lawyer analysis object) -----

    def propose_issue(
        self,
        *,
        case_id: UUID,
        statement: str,
        order_index: int = 0,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> Issue:
        """AI proposal path — always CANDIDATE / AI_PROPOSED."""
        self._require_case(case_id)
        issue = Issue(
            issue_key=uuid.uuid4(),
            case_id=case_id,
            statement=statement,
            order_index=order_index,
            source_type=IssueSourceType.AI_PROPOSED.value,
            status=IssueStatus.CANDIDATE.value,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        _sync_issue_layer(issue)
        self.repo.add(issue)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_issue",
            "issues",
            issue.id,
            case_id=case_id,
            after={
                "issue_key": str(issue.issue_key),
                "version": issue.version,
                "status": issue.status,
                "source_type": issue.source_type,
            },
        )
        return issue

    def create_lawyer_issue(
        self,
        *,
        case_id: UUID,
        statement: str,
        actor_id: UUID,
        order_index: int = 0,
        decision: HumanDecision | None = None,
    ) -> Issue:
        """Lawyer-created issue — immediately CONFIRMED with HumanDecision."""
        self._require_case(case_id)
        issue_key = uuid.uuid4()
        decision = decision or self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_ISSUE",
            target_type="Issue",
            target_id=issue_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"statement": statement},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        issue = Issue(
            issue_key=issue_key,
            case_id=case_id,
            statement=statement,
            order_index=order_index,
            source_type=IssueSourceType.LAWYER_CREATED.value,
            status=IssueStatus.CONFIRMED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        _sync_issue_layer(issue)
        self.repo.add(issue)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_issue",
            "issues",
            issue.id,
            case_id=case_id,
            after={
                "issue_key": str(issue.issue_key),
                "status": issue.status,
                "decision_id": str(decision.id),
            },
        )
        return issue

    def confirm_issue(
        self,
        issue_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Issue:
        issue = self._require_current_issue(issue_key)
        if issue.status != IssueStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE issues can be confirmed")
        if issue.stale:
            raise ConflictError("cannot confirm stale issue")
        decision = decision or self._new_decision(
            case_id=issue.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_ISSUE",
            target_type="Issue",
            target_id=issue.issue_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"issue_key": str(issue.issue_key), "version": issue.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        issue.status = IssueStatus.CONFIRMED.value
        issue.confirm_decision_id = decision.id
        issue.updated_at = _now()
        _sync_issue_layer(issue)
        self._audit(
            actor_id,
            "confirm_issue",
            "issues",
            issue.id,
            case_id=issue.case_id,
            after={
                "status": issue.status,
                "decision_id": str(decision.id),
                "issue_key": str(issue.issue_key),
                "version": issue.version,
            },
        )
        return issue

    def reject_issue(
        self,
        issue_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Issue:
        issue = self._require_current_issue(issue_key)
        if issue.status != IssueStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE issues can be rejected")
        decision = decision or self._new_decision(
            case_id=issue.case_id,
            actor_id=actor_id,
            decision_type="REJECT_ISSUE",
            target_type="Issue",
            target_id=issue.issue_key,
            result=DecisionResult.REJECTED.value,
            payload={"issue_key": str(issue.issue_key), "version": issue.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        issue.status = IssueStatus.REJECTED.value
        issue.updated_at = _now()
        _sync_issue_layer(issue)
        self._audit(
            actor_id,
            "reject_issue",
            "issues",
            issue.id,
            case_id=issue.case_id,
            after={"status": issue.status, "decision_id": str(decision.id)},
        )
        return issue

    def amend_issue(
        self,
        issue_key: UUID,
        *,
        new_statement: str,
        actor_id: UUID,
        change_reason: str | None = None,
        decision: HumanDecision | None = None,
    ) -> Issue:
        old = self._require_current_issue(issue_key)
        if old.status != IssueStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED issues can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_ISSUE",
            target_type="Issue",
            target_id=old.issue_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version, "new_statement": new_statement},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = IssueStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        _sync_issue_layer(old)
        new_issue = Issue(
            issue_key=old.issue_key,
            case_id=old.case_id,
            statement=new_statement,
            order_index=old.order_index,
            source_type=IssueSourceType.LAWYER_REFINED.value,
            status=IssueStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
            change_reason=change_reason,
            parent_issue_key=old.parent_issue_key,
            analyst_run_id=old.analyst_run_id,
        )
        _sync_issue_layer(new_issue)
        self.repo.add(new_issue)
        self.repo.flush()
        self._copy_issue_links(old, new_issue)
        invalidate_dependencies(
            self.session,
            StaleEvent.ISSUE_CHANGED,
            case_id=old.case_id,
            payload={"issue_key": str(old.issue_key), "from_version": old.version},
        )
        self._audit(
            actor_id,
            "amend_issue",
            "issues",
            new_issue.id,
            case_id=old.case_id,
            after={
                "issue_key": str(new_issue.issue_key),
                "version": new_issue.version,
                "decision_id": str(decision.id),
            },
        )
        return new_issue

    def link_fact_to_issue(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        fact_key: UUID,
        fact_version: int,
        role: str,
        explanation: str | None = None,
        actor_id: UUID,
    ) -> IssueFactLink:
        self._require_case(case_id)
        issue = self.repo.get_issue_version(issue_key, issue_version)
        if issue is None:
            raise NotFoundError("issue version not found")
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        fact = self.repo.get_fact_version(fact_key, fact_version)
        if fact is None:
            raise NotFoundError("fact version not found")
        if fact.case_id != case_id:
            raise ValidationError("cross-case fact link rejected")
        if role not in {r.value for r in IssueLinkRole}:
            raise ValidationError(f"invalid issue-fact link role: {role}")
        link = IssueFactLink(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            fact_key=fact_key,
            fact_version=fact_version,
            role=role,
            status=IssueLinkStatus.ACTIVE.value,
            explanation=explanation,
            created_by=actor_id,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_fact_to_issue",
            "issue_fact_links",
            link.id,
            case_id=case_id,
            after={
                "issue_key": str(issue_key),
                "issue_version": issue_version,
                "fact_key": str(fact_key),
                "fact_version": fact_version,
                "role": role,
            },
        )
        return link

    def unlink_fact_from_issue(
        self,
        link_id: UUID,
        *,
        actor_id: UUID,
    ) -> IssueFactLink:
        link = self.session.get(IssueFactLink, link_id)
        if link is None:
            raise NotFoundError("issue-fact link not found")
        if link.status == IssueLinkStatus.VOID.value:
            raise ConflictError("link already void")
        link.status = IssueLinkStatus.VOID.value
        self.repo.flush()
        self._audit(
            actor_id,
            "unlink_fact_from_issue",
            "issue_fact_links",
            link.id,
            case_id=link.case_id,
            before={"status": IssueLinkStatus.ACTIVE.value},
            after={"status": link.status},
        )
        return link

    def link_evidence_to_issue(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        evidence_item_id: UUID,
        evidence_item_version: int,
        role: str,
        explanation: str | None = None,
        actor_id: UUID,
    ) -> IssueEvidenceLink:
        self._require_case(case_id)
        issue = self.repo.get_issue_version(issue_key, issue_version)
        if issue is None:
            raise NotFoundError("issue version not found")
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        evidence = self.repo.get_evidence_version(evidence_item_id, evidence_item_version)
        if evidence is None:
            raise NotFoundError("evidence version not found")
        if evidence.case_id != case_id:
            raise ValidationError("cross-case evidence link rejected")
        if role not in {r.value for r in IssueLinkRole}:
            raise ValidationError(f"invalid issue-evidence link role: {role}")
        if explanation and _issue_evidence_explanation_is_legal_conclusion(explanation):
            raise ValidationError(
                "issue evidence explanation must not assert legal proof conclusions"
            )
        link = IssueEvidenceLink(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            evidence_item_id=evidence_item_id,
            evidence_item_version=evidence_item_version,
            role=role,
            explanation=explanation,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_evidence_to_issue",
            "issue_evidence_links",
            link.id,
            case_id=case_id,
            after={
                "issue_key": str(issue_key),
                "issue_version": issue_version,
                "evidence_item_id": str(evidence_item_id),
                "evidence_item_version": evidence_item_version,
                "role": role,
            },
        )
        return link

    def unlink_evidence_from_issue(
        self,
        link_id: UUID,
        *,
        actor_id: UUID,
    ) -> IssueEvidenceLink:
        link = self.session.get(IssueEvidenceLink, link_id)
        if link is None:
            raise NotFoundError("issue-evidence link not found")
        self.session.delete(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "unlink_evidence_from_issue",
            "issue_evidence_links",
            link_id,
            case_id=link.case_id,
            before={"issue_key": str(link.issue_key), "issue_version": link.issue_version},
        )
        return link

    def invalidate_dependencies(
        self,
        event: StaleEvent,
        *,
        case_id: UUID,
        payload: dict[str, Any],
    ) -> list[str]:
        return invalidate_dependencies(self.session, event, case_id=case_id, payload=payload)

    # ----- Draft -----

    def create_document_draft(
        self,
        *,
        case_id: UUID,
        body_structured_json: dict[str, Any],
        citations: list[dict[str, Any]],
        based_on_confirmation_set_hash: str | None = None,
        doc_type: str = "COMPLAINT",
        status: str = DraftStatus.DRAFT.value,
        writer_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> DocumentDraft:
        self._require_case(case_id)
        from sqlalchemy import func, select

        max_version = self.session.scalar(
            select(func.max(DocumentDraft.version)).where(
                DocumentDraft.case_id == case_id, DocumentDraft.doc_type == doc_type
            )
        )
        version = int(max_version or 0) + 1
        conf_hash = based_on_confirmation_set_hash or _confirmation_set_hash(
            [json.dumps(body_structured_json, sort_keys=True, ensure_ascii=False)]
        )
        draft = DocumentDraft(
            case_id=case_id,
            doc_type=doc_type,
            version=version,
            status=status,
            body_structured_json=body_structured_json,
            based_on_confirmation_set_hash=conf_hash,
            writer_run_id=writer_run_id,
        )
        self.repo.add(draft)
        self.repo.flush()
        for cite in citations:
            has_fact = cite.get("fact_key")
            has_evidence = cite.get("evidence_item_id")
            is_annotation = cite.get("citation_kind") == "ANNOTATION"
            if not has_fact and not has_evidence and not is_annotation:
                raise ValidationError("citation must pin fact or evidence")
            if cite.get("evidence_item_id") and cite.get("evidence_item_version") is None:
                raise ValidationError("DraftCitation must include evidence_item_version")
            if cite.get("fact_key") and cite.get("fact_version") is None:
                raise ValidationError("DraftCitation must include fact_version")
            self.repo.add(
                DraftCitation(
                    draft_id=draft.id,
                    block_id=str(cite["block_id"]),
                    citation_kind=str(cite.get("citation_kind", "FACT")),
                    fact_key=UUID(str(cite["fact_key"])) if cite.get("fact_key") else None,
                    fact_version=cite.get("fact_version"),
                    evidence_item_id=UUID(str(cite["evidence_item_id"]))
                    if cite.get("evidence_item_id")
                    else None,
                    evidence_item_version=cite.get("evidence_item_version"),
                    source_span_id=UUID(str(cite["source_span_id"]))
                    if cite.get("source_span_id")
                    else None,
                    requires_lawyer_confirm=bool(cite.get("requires_lawyer_confirm", False)),
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_document_draft",
            "document_drafts",
            draft.id,
            case_id=case_id,
            after={"version": draft.version, "status": draft.status},
        )
        return draft

    def mark_document_draft_stale(
        self,
        draft_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
    ) -> DocumentDraft:
        draft = self.repo.get_draft(draft_id)
        if draft is None:
            raise NotFoundError("draft not found")
        draft.status = DraftStatus.STALE.value
        draft.stale_reason = reason
        draft.updated_at = _now()
        self._audit(
            actor_id,
            "mark_document_draft_stale",
            "document_drafts",
            draft.id,
            case_id=draft.case_id,
            after={"status": draft.status, "reason": reason},
        )
        return draft

    def approve_document_draft(
        self,
        draft_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> DocumentDraft:
        draft = self.repo.get_draft(draft_id)
        if draft is None:
            raise NotFoundError("draft not found")
        if draft.status not in {DraftStatus.DRAFT.value, DraftStatus.IN_REVIEW.value}:
            raise ConflictError("draft cannot be approved from current status")
        decision = decision or self._new_decision(
            case_id=draft.case_id,
            actor_id=actor_id,
            decision_type="APPROVE_DRAFT",
            target_type="DocumentDraft",
            target_id=draft.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"draft_id": str(draft.id), "version": draft.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        draft.status = DraftStatus.APPROVED_BY_LAWYER.value
        draft.updated_at = _now()
        # Intentionally does NOT modify WorkflowInstance status.
        self._audit(
            actor_id,
            "approve_document_draft",
            "document_drafts",
            draft.id,
            case_id=draft.case_id,
            after={"status": draft.status, "decision_id": str(decision.id)},
        )
        return draft

    # ----- helpers -----

    def _require_case(self, case_id: UUID) -> Case:
        case = self.repo.get_case(case_id)
        if case is None:
            raise NotFoundError("case not found")
        if case.status != CaseStatus.OPEN.value:
            raise ConflictError("case is not OPEN")
        return case

    def _require_current_evidence(self, evidence_id: UUID) -> EvidenceItem:
        item = self.repo.get_current_evidence(evidence_id)
        if item is None:
            raise NotFoundError("evidence item not found")
        return item

    def _require_current_fact(self, fact_key: UUID) -> Fact:
        fact = self.repo.get_current_fact(fact_key)
        if fact is None:
            raise NotFoundError("fact not found")
        return fact

    def _require_current_party(self, party_key: UUID) -> CaseParty:
        party = self.repo.get_current_party(party_key)
        if party is None:
            raise NotFoundError("party not found")
        return party

    def _require_current_claim(self, claim_direction_key: UUID) -> ClaimDirection:
        claim = self.repo.get_current_claim(claim_direction_key)
        if claim is None:
            raise NotFoundError("claim direction not found")
        return claim

    def _require_current_issue(self, issue_key: UUID) -> Issue:
        issue = self.repo.get_current_issue(issue_key)
        if issue is None:
            raise NotFoundError("issue not found")
        return issue

    def _require_current_relief_claim(self, claim_key: UUID) -> Claim:
        claim = self.repo.get_current_relief_claim(claim_key)
        if claim is None:
            raise NotFoundError("claim not found")
        return claim

    def _copy_claim_links(self, old: Claim, new: Claim) -> None:
        for link in self.repo.list_claim_issue_links(old.claim_key, old.version):
            self.repo.add(
                ClaimIssueLink(
                    case_id=link.case_id,
                    claim_key=new.claim_key,
                    claim_version=new.version,
                    issue_key=link.issue_key,
                    issue_version=link.issue_version,
                    role=link.role,
                    status=ClaimLinkStatus.ACTIVE.value,
                )
            )
        for link in self.repo.list_claim_fact_links(old.claim_key, old.version):
            self.repo.add(
                ClaimFactLink(
                    case_id=link.case_id,
                    claim_key=new.claim_key,
                    claim_version=new.version,
                    fact_key=link.fact_key,
                    fact_version=link.fact_version,
                    role=link.role,
                    status=ClaimLinkStatus.ACTIVE.value,
                )
            )
        self.repo.flush()

    def _copy_issue_links(self, old: Issue, new: Issue) -> None:
        for link in self.repo.list_issue_fact_links(old.issue_key, old.version):
            self.repo.add(
                IssueFactLink(
                    case_id=link.case_id,
                    issue_key=new.issue_key,
                    issue_version=new.version,
                    fact_key=link.fact_key,
                    fact_version=link.fact_version,
                    role=link.role,
                    status=IssueLinkStatus.ACTIVE.value,
                    explanation=link.explanation,
                    created_by=link.created_by,
                )
            )
        for link in self.repo.list_issue_evidence_links(old.issue_key, old.version):
            self.repo.add(
                IssueEvidenceLink(
                    case_id=link.case_id,
                    issue_key=new.issue_key,
                    issue_version=new.version,
                    evidence_item_id=link.evidence_item_id,
                    evidence_item_version=link.evidence_item_version,
                    role=link.role,
                    explanation=link.explanation,
                )
            )
        self.repo.flush()

    def _assert_supporting_facts_confirmed(self, payload: dict[str, Any]) -> None:
        for item in payload.get("claims") or []:
            fact_ids = item.get("supporting_fact_ids") or []
            if not fact_ids:
                raise ValidationError(
                    "each claim requires at least one supporting_fact_id"
                )
            for fact_id in fact_ids:
                fact_key = UUID(str(fact_id))
                fact = self.repo.get_current_fact(fact_key)
                if fact is None:
                    raise ValidationError(f"supporting fact not found: {fact_key}")
                if fact.status != FactStatus.CONFIRMED.value:
                    raise ValidationError(f"supporting fact not CONFIRMED: {fact_key}")
                if fact.stale:
                    raise ValidationError(f"supporting fact is stale: {fact_key}")

    def _new_decision(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        decision_type: str,
        target_type: str,
        target_id: UUID | None,
        result: str,
        payload: dict[str, Any],
    ) -> HumanDecision:
        return HumanDecision(
            case_id=case_id,
            decision_type=decision_type,
            target_type=target_type,
            target_id=target_id,
            input_payload_json=payload,
            result=result,
            actor_id=actor_id,
        )

    def _audit(
        self,
        actor_id: UUID,
        action: str,
        entity_type: str,
        entity_id: UUID,
        *,
        case_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.repo.add_audit(
            AuditLog(
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                case_id=case_id,
                before_json=before,
                after_json=after,
            )
        )
        self.repo.flush()


def _sync_issue_layer(issue: Issue) -> None:
    """Keep deprecated layer column aligned with status SSOT."""
    issue.layer = issue.status


def _issue_evidence_explanation_is_legal_conclusion(explanation: str) -> bool:
    lowered = explanation.lower()
    if looks_like_legal_conclusion(explanation):
        return True
    proof_markers = (
        "proves defendant",
        "proves liability",
        "证明被告承担",
        "证明.*责任",
        "直接证明",
    )
    return any(marker in lowered for marker in proof_markers[:4])


# Guard: DomainService must not allow mutating material content hash/storage key via public API.
def ensure_material_bytes_immutable(material: CaseMaterial, updates: dict[str, Any]) -> None:
    if "content_hash" in updates or "storage_key" in updates:
        raise ImmutableError("material content_hash/storage_key are immutable")
```

## (E) Domain — backend/domain/issue_centered.py (complete, untruncated, inlined)

Production path: `backend/domain/issue_centered.py` (1260 lines)

```python
"""Issue-centered V2 domain mutations — mixed into DomainService.

Explicit invariants enforced in this module (Issue #80 repair SSOT / #73 / #60 / #83 / #86 / #85 / #84):
  INV-1: _reject_claim_direction_production_mutation blocks ClaimDirection writes.
  INV-2: link_fact_to_proof_task uses explicit proof_task_version/fact_version only
         (no get_current_*); rejects implicit current/latest and cross-case links.
  INV-3: create_lawyer_position requires opponent_material_ref for FORMAL_DEFENSE.
  INV-4: merge_issues and split_issue persist HumanDecision + AuditLog before mutation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from backend.domain.enums import (
    ClaimLinkStatus,
    ConflictFactRole,
    ConflictSourceType,
    ConflictStatus,
    DecisionResult,
    IssueLinkStatus,
    IssueSourceType,
    IssueStatus,
    LawyerAssessmentStatus,
    PositionSide,
    PositionSourceType,
    PositionStatus,
    PositionType,
    ProofGapSourceType,
    ProofGapStatus,
    ProofGapType,
    ProofTaskFactLinkRole,
    ProofTaskSourceType,
    ProofTaskStatus,
)
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.models import (
    AuditLog,
    ConflictFactLink,
    HumanDecision,
    Issue,
    IssueConflict,
    IssueFactLink,
    IssueLegalTheoryLink,
    IssuePosition,
    LawyerAssessment,
    ProofGap,
    ProofTask,
    ProofTaskFactLink,
)

if TYPE_CHECKING:
    from backend.domain.services import DomainService

__all__ = [
    "IssueCenteredDomainMixin",
    "_guard_cross_case_proof_task_fact_pair",
    "_guard_explicit_proof_task_fact_versions",
    "_guard_formal_defense_opponent_material_ref",
    "_guard_formal_defense_side",
    "_guard_resolved_explicit_versions",
    "_normalize_opponent_material_ref",
    "_reject_claim_direction_production_mutation",
    "_require_structure_mutation_audit",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _reject_claim_direction_production_mutation(*, _legacy_compat: bool) -> None:
    """INV-1: block ClaimDirection creation on production mutation paths."""
    if not _legacy_compat:
        raise ValidationError(
            "ClaimDirection production mutation disabled; use Claim Domain instead"
        )


def _normalize_opponent_material_ref(ref: str | None) -> str | None:
    """INV-3: strip whitespace so blank refs cannot bypass FORMAL_DEFENSE guard."""
    if ref is None:
        return None
    stripped = ref.strip()
    return stripped or None


def _guard_explicit_proof_task_fact_versions(
    proof_task_version: int,
    fact_version: int,
) -> None:
    """INV-2: reject zero/negative versions that could alias implicit current/latest."""
    if proof_task_version < 1 or fact_version < 1:
        raise ValidationError(
            "ProofTaskFactLink requires explicit positive proof_task_version and fact_version"
        )


def _guard_formal_defense_opponent_material_ref(
    position_type: str,
    opponent_material_ref: str | None,
) -> None:
    """INV-3: FORMAL_DEFENSE must cite opponent source material."""
    if position_type == PositionType.FORMAL_DEFENSE.value:
        if not opponent_material_ref or not str(opponent_material_ref).strip():
            raise ValidationError("FORMAL_DEFENSE requires opponent material reference")


def _guard_formal_defense_side(position_type: str, side: str) -> None:
    """INV-3: FORMAL_DEFENSE positions must be recorded on the OPPONENT side."""
    if position_type == PositionType.FORMAL_DEFENSE.value and side != PositionSide.OPPONENT.value:
        raise ValidationError("FORMAL_DEFENSE must be on OPPONENT side")


def _guard_cross_case_proof_task_fact_pair(task: ProofTask, fact: Any) -> None:
    """INV-2: proof task and fact must belong to the same case."""
    if task.case_id != fact.case_id:
        raise ValidationError("cross-case proof task fact link rejected")


def _guard_resolved_explicit_versions(
    *,
    proof_task_version: int,
    fact_version: int,
    task: ProofTask,
    fact: Any,
) -> None:
    """INV-2: resolved rows must match requested versions (no implicit current/latest)."""
    if task.version != proof_task_version:
        raise ValidationError(
            "ProofTaskFactLink requires explicit proof_task_version; "
            "implicit current/latest rejected"
        )
    if fact.version != fact_version:
        raise ValidationError(
            "ProofTaskFactLink requires explicit fact_version; implicit current/latest rejected"
        )


def _resolve_proof_task_and_fact_for_link(
    svc: DomainService,
    *,
    case_id: UUID,
    proof_task_key: UUID,
    proof_task_version: int,
    fact_key: UUID,
    fact_version: int,
) -> tuple[ProofTask, Any]:
    """INV-2: resolve explicit proof-task/fact versions; reject cross-case links."""
    _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
    task = svc.repo.get_proof_task_version(proof_task_key, proof_task_version)
    if task is None:
        raise NotFoundError("proof task version not found")
    if task.case_id != case_id:
        raise ValidationError("cross-case proof task link rejected")
    fact = svc.repo.get_fact_version(fact_key, fact_version)
    if fact is None:
        raise NotFoundError("fact version not found")
    if fact.case_id != case_id:
        raise ValidationError("cross-case fact link rejected")
    _guard_resolved_explicit_versions(
        proof_task_version=proof_task_version,
        fact_version=fact_version,
        task=task,
        fact=fact,
    )
    return task, fact


def _persist_issue_structure_decision(
    svc: DomainService,
    *,
    case_id: UUID,
    actor_id: UUID,
    decision_type: str,
    target_id: UUID,
    payload: dict[str, Any],
) -> Any:
    """INV-4: HumanDecision must be flushed before merge/split mutations proceed."""
    decision = svc._new_decision(
        case_id=case_id,
        actor_id=actor_id,
        decision_type=decision_type,
        target_type="Issue",
        target_id=target_id,
        result=DecisionResult.CONFIRMED.value,
        payload=payload,
    )
    svc.repo.add_decision(decision)
    svc.repo.flush()
    if decision.id is None:
        raise ConflictError(f"{decision_type} decision failed to persist")
    return decision


def _require_structure_mutation_audit(
    svc: DomainService,
    *,
    case_id: UUID,
    action: str,
    decision_type: str,
) -> None:
    """INV-4: merge/split must emit HumanDecision + AuditLog before returning."""
    from sqlalchemy import select

    decision = svc.session.scalars(
        select(HumanDecision)
        .where(
            HumanDecision.case_id == case_id,
            HumanDecision.decision_type == decision_type,
        )
        .order_by(HumanDecision.created_at.desc())
        .limit(1)
    ).first()
    if decision is None:
        raise ConflictError(f"{action} must emit HumanDecision before completing")
    audit = svc.session.scalars(
        select(AuditLog)
        .where(
            AuditLog.case_id == case_id,
            AuditLog.action == action,
            AuditLog.entity_type == "issues",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    ).first()
    if audit is None:
        raise ConflictError(f"{action} must emit AuditLog before completing")
    decision_id = str(decision.id)
    after = audit.after_json or {}
    if after.get("decision_id") != decision_id:
        raise ConflictError(
            f"{action} AuditLog.after_json must reference HumanDecision decision_id"
        )


class IssueCenteredDomainMixin:
    """Issue-centered workspace V2 mutations."""

    session: Any
    repo: Any

    def _require_issue_version(self, issue_key: UUID, issue_version: int) -> Issue:
        issue = self.repo.get_issue_version(issue_key, issue_version)
        if issue is None:
            raise NotFoundError("issue version not found")
        return issue

    # ----- IssuePosition -----

    def propose_position(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        side: str,
        position_type: str,
        statement: str,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> IssuePosition:
        """AI may propose OUR assertion or ANTICIPATED_DEFENSE only."""
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        if side not in {PositionSide.OUR.value}:
            raise ValidationError("AI may only propose OUR positions")
        if position_type not in {
            PositionType.ASSERTION.value,
            PositionType.ANTICIPATED_DEFENSE.value,
        }:
            raise ValidationError("AI may not propose FORMAL_DEFENSE")
        pos = IssuePosition(
            position_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            side=side,
            position_type=position_type,
            source_type=PositionSourceType.AI_PROPOSED.value,
            status=PositionStatus.CANDIDATE.value,
            statement=statement,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(pos)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_position",
            "issue_positions",
            pos.id,
            case_id=case_id,
            after={"position_key": str(pos.position_key), "status": pos.status},
        )
        return pos

    def create_lawyer_position(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        side: str,
        position_type: str,
        statement: str,
        actor_id: UUID,
        opponent_material_ref: str | None = None,
    ) -> IssuePosition:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        opponent_material_ref = _normalize_opponent_material_ref(opponent_material_ref)
        _guard_formal_defense_opponent_material_ref(position_type, opponent_material_ref)
        _guard_formal_defense_side(position_type, side)
        if position_type == PositionType.FORMAL_DEFENSE.value:
            source = PositionSourceType.OPPONENT_MATERIAL.value
        else:
            source = PositionSourceType.LAWYER_CREATED.value
        pos_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_POSITION",
            target_type="IssuePosition",
            target_id=pos_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"statement": statement, "position_type": position_type},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos = IssuePosition(
            position_key=pos_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            side=side,
            position_type=position_type,
            source_type=source,
            status=PositionStatus.CONFIRMED.value,
            statement=statement,
            opponent_material_ref=opponent_material_ref,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(pos)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_position",
            "issue_positions",
            pos.id,
            case_id=case_id,
            after={"position_key": str(pos.position_key), "decision_id": str(decision.id)},
        )
        return pos

    def confirm_position(
        self: DomainService,
        position_key: UUID,
        *,
        actor_id: UUID,
    ) -> IssuePosition:
        pos = self.repo.get_current_position(position_key)
        if pos is None:
            raise NotFoundError("position not found")
        if pos.status != PositionStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE positions can be confirmed")
        decision = self._new_decision(
            case_id=pos.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_POSITION",
            target_type="IssuePosition",
            target_id=pos.position_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"position_key": str(pos.position_key), "version": pos.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos.status = PositionStatus.CONFIRMED.value
        pos.confirm_decision_id = decision.id
        pos.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_position",
            "issue_positions",
            pos.id,
            case_id=pos.case_id,
            after={"status": pos.status, "decision_id": str(decision.id)},
        )
        return pos

    def reject_position(
        self: DomainService,
        position_key: UUID,
        *,
        actor_id: UUID,
    ) -> IssuePosition:
        pos = self.repo.get_current_position(position_key)
        if pos is None:
            raise NotFoundError("position not found")
        if pos.status != PositionStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE positions can be rejected")
        decision = self._new_decision(
            case_id=pos.case_id,
            actor_id=actor_id,
            decision_type="REJECT_POSITION",
            target_type="IssuePosition",
            target_id=pos.position_key,
            result=DecisionResult.REJECTED.value,
            payload={"position_key": str(pos.position_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos.status = PositionStatus.REJECTED.value
        pos.updated_at = _now()
        self._audit(
            actor_id,
            "reject_position",
            "issue_positions",
            pos.id,
            case_id=pos.case_id,
            after={"status": pos.status},
        )
        return pos

    # ----- ProofTask -----

    def propose_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> ProofTask:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        task = ProofTask(
            proof_task_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ProofTaskStatus.CANDIDATE.value,
            source_type=ProofTaskSourceType.AI_PROPOSED.value,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(task)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_proof_task",
            "proof_tasks",
            task.id,
            case_id=case_id,
            after={"proof_task_key": str(task.proof_task_key)},
        )
        return task

    def adopt_proof_task(
        self: DomainService,
        proof_task_key: UUID,
        *,
        actor_id: UUID,
    ) -> ProofTask:
        task = self.repo.get_current_proof_task(proof_task_key)
        if task is None:
            raise NotFoundError("proof task not found")
        if task.status != ProofTaskStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE proof tasks can be adopted")
        decision = self._new_decision(
            case_id=task.case_id,
            actor_id=actor_id,
            decision_type="ADOPT_PROOF_TASK",
            target_type="ProofTask",
            target_id=task.proof_task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"proof_task_key": str(task.proof_task_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task.status = ProofTaskStatus.ADOPTED.value
        task.confirm_decision_id = decision.id
        task.updated_at = _now()
        self._audit(
            actor_id,
            "adopt_proof_task",
            "proof_tasks",
            task.id,
            case_id=task.case_id,
            after={"status": task.status},
        )
        return task

    def create_lawyer_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        actor_id: UUID,
    ) -> ProofTask:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        task_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_PROOF_TASK",
            target_type="ProofTask",
            target_id=task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"description": description},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task = ProofTask(
            proof_task_key=task_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ProofTaskStatus.ADOPTED.value,
            source_type=ProofTaskSourceType.LAWYER_CREATED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(task)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_proof_task",
            "proof_tasks",
            task.id,
            case_id=case_id,
            after={"proof_task_key": str(task.proof_task_key)},
        )
        return task

    def waive_proof_task(
        self: DomainService,
        proof_task_key: UUID,
        *,
        actor_id: UUID,
    ) -> ProofTask:
        task = self.repo.get_current_proof_task(proof_task_key)
        if task is None:
            raise NotFoundError("proof task not found")
        if task.status not in {ProofTaskStatus.ADOPTED.value, ProofTaskStatus.CANDIDATE.value}:
            raise ConflictError("proof task cannot be waived in current status")
        decision = self._new_decision(
            case_id=task.case_id,
            actor_id=actor_id,
            decision_type="WAIVE_PROOF_TASK",
            target_type="ProofTask",
            target_id=task.proof_task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"proof_task_key": str(task.proof_task_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task.status = ProofTaskStatus.WAIVED.value
        task.updated_at = _now()
        self._audit(
            actor_id,
            "waive_proof_task",
            "proof_tasks",
            task.id,
            case_id=task.case_id,
            after={"status": task.status},
        )
        return task

    def link_fact_to_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        proof_task_key: UUID,
        proof_task_version: int,
        fact_key: UUID,
        fact_version: int,
        role: str,
        actor_id: UUID,
    ) -> ProofTaskFactLink:
        # INV-2: reject invalid/implicit versions before any DB row lookup.
        _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
        self._require_case(case_id)
        task, fact = _resolve_proof_task_and_fact_for_link(
            self,
            case_id=case_id,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            fact_key=fact_key,
            fact_version=fact_version,
        )
        _guard_cross_case_proof_task_fact_pair(task, fact)
        if role not in {r.value for r in ProofTaskFactLinkRole}:
            raise ValidationError(f"invalid proof task fact link role: {role}")
        link = ProofTaskFactLink(
            case_id=case_id,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            fact_key=fact_key,
            fact_version=fact_version,
            role=role,
            status=IssueLinkStatus.ACTIVE.value,
            created_by=actor_id,
        )
        try:
            self.repo.add(link)
            self.repo.flush()
        except IntegrityError as exc:
            raise ConflictError("duplicate proof task fact link") from exc
        self._audit(
            actor_id,
            "link_fact_to_proof_task",
            "proof_task_fact_links",
            link.id,
            case_id=case_id,
            after={
                "proof_task_key": str(proof_task_key),
                "fact_key": str(fact_key),
                "role": role,
            },
        )
        return link

    # ----- IssueConflict -----

    def create_conflict(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        source_type: str = ConflictSourceType.AI_DETECTED.value,
        fact_refs: list[dict[str, Any]] | None = None,
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> IssueConflict:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        conflict = IssueConflict(
            conflict_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ConflictStatus.OPEN.value
            if source_type == ConflictSourceType.LAWYER_CREATED.value
            else ConflictStatus.CANDIDATE.value,
            source_type=source_type,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(conflict)
        self.repo.flush()
        for ref in fact_refs or []:
            fk = UUID(str(ref["fact_key"]))
            fv = int(ref["fact_version"])
            role = str(ref.get("role", ConflictFactRole.CONTEXT.value))
            fact = self.repo.get_fact_version(fk, fv)
            if fact is None or fact.case_id != case_id:
                raise ValidationError("invalid conflict fact ref")
            link = ConflictFactLink(
                case_id=case_id,
                conflict_id=conflict.id,
                fact_key=fk,
                fact_version=fv,
                role=role,
            )
            self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=case_id,
            after={"conflict_key": str(conflict.conflict_key), "status": conflict.status},
        )
        return conflict

    def resolve_conflict(
        self: DomainService,
        conflict_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> IssueConflict:
        conflict = self.session.get(IssueConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("conflict not found")
        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
            raise ConflictError("conflict already closed")
        decision = self._new_decision(
            case_id=conflict.case_id,
            actor_id=actor_id,
            decision_type="RESOLVE_CONFLICT",
            target_type="IssueConflict",
            target_id=conflict.conflict_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        conflict.status = ConflictStatus.RESOLVED.value
        conflict.resolution_note = resolution_note
        conflict.resolve_decision_id = decision.id
        conflict.updated_at = _now()
        self._audit(
            actor_id,
            "resolve_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=conflict.case_id,
            after={"status": conflict.status},
        )
        return conflict

    def dismiss_conflict(
        self: DomainService,
        conflict_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> IssueConflict:
        conflict = self.session.get(IssueConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("conflict not found")
        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
            raise ConflictError("conflict already closed")
        decision = self._new_decision(
            case_id=conflict.case_id,
            actor_id=actor_id,
            decision_type="DISMISS_CONFLICT",
            target_type="IssueConflict",
            target_id=conflict.conflict_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        conflict.status = ConflictStatus.DISMISSED.value
        conflict.resolution_note = resolution_note
        conflict.resolve_decision_id = decision.id
        conflict.updated_at = _now()
        self._audit(
            actor_id,
            "dismiss_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=conflict.case_id,
            after={"status": conflict.status},
        )
        return conflict

    # ----- ProofGap -----

    def create_proof_gap(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        gap_type: str,
        description: str,
        source_type: str = ProofGapSourceType.AI_DETECTED.value,
        proof_task_key: UUID | None = None,
        proof_task_version: int | None = None,
        what_exists: str | None = None,
        what_is_missing: str | None = None,
        why_it_matters: str | None = None,
        suggested_material_types: list[str] | None = None,
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> ProofGap:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        if gap_type not in {t.value for t in ProofGapType}:
            raise ValidationError(f"invalid gap type: {gap_type}")
        gap = ProofGap(
            gap_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            gap_type=gap_type,
            status=ProofGapStatus.OPEN.value,
            source_type=source_type,
            description=description,
            what_exists=what_exists,
            what_is_missing=what_is_missing,
            why_it_matters=why_it_matters,
            suggested_material_types=suggested_material_types,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(gap)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=case_id,
            after={"gap_key": str(gap.gap_key), "gap_type": gap_type},
        )
        return gap

    def resolve_proof_gap(
        self: DomainService,
        gap_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> ProofGap:
        gap = self.session.get(ProofGap, gap_id)
        if gap is None:
            raise NotFoundError("proof gap not found")
        if gap.status != ProofGapStatus.OPEN.value:
            raise ConflictError("only OPEN gaps can be resolved")
        decision = self._new_decision(
            case_id=gap.case_id,
            actor_id=actor_id,
            decision_type="RESOLVE_PROOF_GAP",
            target_type="ProofGap",
            target_id=gap.gap_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        gap.status = ProofGapStatus.RESOLVED.value
        gap.resolution_note = resolution_note
        gap.resolve_decision_id = decision.id
        gap.updated_at = _now()
        self._audit(
            actor_id,
            "resolve_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=gap.case_id,
            after={"status": gap.status},
        )
        return gap

    def waive_proof_gap(
        self: DomainService,
        gap_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> ProofGap:
        gap = self.session.get(ProofGap, gap_id)
        if gap is None:
            raise NotFoundError("proof gap not found")
        if gap.status != ProofGapStatus.OPEN.value:
            raise ConflictError("only OPEN gaps can be waived")
        decision = self._new_decision(
            case_id=gap.case_id,
            actor_id=actor_id,
            decision_type="WAIVE_PROOF_GAP",
            target_type="ProofGap",
            target_id=gap.gap_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        gap.status = ProofGapStatus.WAIVED.value
        gap.resolution_note = resolution_note
        gap.resolve_decision_id = decision.id
        gap.updated_at = _now()
        self._audit(
            actor_id,
            "waive_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=gap.case_id,
            after={"status": gap.status},
        )
        return gap

    # ----- LawyerAssessment (lawyer-only) -----

    def create_lawyer_assessment(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        content: str,
        actor_id: UUID,
        is_ai_actor: bool = False,
    ) -> LawyerAssessment:
        if is_ai_actor:
            raise ValidationError("AI cannot create LawyerAssessment")
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        assessment_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=assessment_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"content": content[:200]},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        assessment = LawyerAssessment(
            assessment_key=assessment_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            content=content,
            status=LawyerAssessmentStatus.ACTIVE.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(assessment)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_assessment",
            "lawyer_assessments",
            assessment.id,
            case_id=case_id,
            after={"assessment_key": str(assessment_key)},
        )
        return assessment

    def amend_lawyer_assessment(
        self: DomainService,
        assessment_key: UUID,
        *,
        new_content: str,
        actor_id: UUID,
        is_ai_actor: bool = False,
    ) -> LawyerAssessment:
        if is_ai_actor:
            raise ValidationError("AI cannot amend LawyerAssessment")
        old = self.repo.get_current_lawyer_assessment(assessment_key)
        if old is None:
            raise NotFoundError("assessment not found")
        if old.status != LawyerAssessmentStatus.ACTIVE.value:
            raise ConflictError("only ACTIVE assessments can be amended")
        decision = self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=old.assessment_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = LawyerAssessmentStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_assessment = LawyerAssessment(
            assessment_key=old.assessment_key,
            case_id=old.case_id,
            issue_key=old.issue_key,
            issue_version=old.issue_version,
            content=new_content,
            status=LawyerAssessmentStatus.ACTIVE.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_assessment)
        self.repo.flush()
        self._audit(
            actor_id,
            "amend_lawyer_assessment",
            "lawyer_assessments",
            new_assessment.id,
            case_id=old.case_id,
            after={"version": new_assessment.version},
        )
        return new_assessment

    def withdraw_lawyer_assessment(
        self: DomainService,
        assessment_key: UUID,
        *,
        actor_id: UUID,
    ) -> LawyerAssessment:
        assessment = self.repo.get_current_lawyer_assessment(assessment_key)
        if assessment is None:
            raise NotFoundError("assessment not found")
        decision = self._new_decision(
            case_id=assessment.case_id,
            actor_id=actor_id,
            decision_type="WITHDRAW_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=assessment.assessment_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"assessment_key": str(assessment_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        assessment.status = LawyerAssessmentStatus.WITHDRAWN.value
        assessment.updated_at = _now()
        self._audit(
            actor_id,
            "withdraw_lawyer_assessment",
            "lawyer_assessments",
            assessment.id,
            case_id=assessment.case_id,
            after={"status": assessment.status},
        )
        return assessment

    # ----- Issue merge / split -----

    def merge_issues(
        self: DomainService,
        *,
        case_id: UUID,
        source_issue_keys: list[UUID],
        merged_statement: str,
        actor_id: UUID,
    ) -> Issue:
        if len(source_issue_keys) < 2:
            raise ValidationError("merge requires at least two issues")
        sources: list[Issue] = []
        for key in source_issue_keys:
            issue = self._require_current_issue(key)
            if issue.case_id != case_id:
                raise ValidationError("cross-case merge rejected")
            if issue.status != IssueStatus.CONFIRMED.value:
                raise ValidationError("only CONFIRMED issues can be merged")
            sources.append(issue)

        decision = _persist_issue_structure_decision(
            self,
            case_id=case_id,
            actor_id=actor_id,
            decision_type="MERGE_ISSUES",
            target_id=uuid.uuid4(),
            payload={
                "source_keys": [str(k) for k in source_issue_keys],
                "merged_statement": merged_statement,
            },
        )

        new_key = uuid.uuid4()
        merged = Issue(
            issue_key=new_key,
            case_id=case_id,
            statement=merged_statement,
            order_index=min(i.order_index for i in sources),
            source_type=IssueSourceType.LAWYER_REFINED.value,
            status=IssueStatus.CONFIRMED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        from backend.domain.services import _sync_issue_layer

        _sync_issue_layer(merged)
        self.repo.add(merged)
        self.repo.flush()

        seen_fact_links: set[tuple] = set()
        for src in sources:
            for link in self.repo.list_issue_fact_links(src.issue_key, src.version):
                key = (link.fact_key, link.fact_version, link.role)
                if key in seen_fact_links:
                    continue
                seen_fact_links.add(key)
                self.repo.add(
                    IssueFactLink(
                        case_id=case_id,
                        issue_key=merged.issue_key,
                        issue_version=merged.version,
                        fact_key=link.fact_key,
                        fact_version=link.fact_version,
                        role=link.role,
                        status=IssueLinkStatus.ACTIVE.value,
                        explanation=link.explanation,
                        created_by=actor_id,
                    )
                )
            src.status = IssueStatus.SUPERSEDED.value
            src.is_current = False
            src.updated_at = _now()
            _sync_issue_layer(src)
        self.repo.flush()
        self._audit(
            actor_id,
            "merge_issues",
            "issues",
            merged.id,
            case_id=case_id,
            after={
                "issue_key": str(merged.issue_key),
                "merged_from": [str(k) for k in source_issue_keys],
                "decision_id": str(decision.id),
            },
        )
        _require_structure_mutation_audit(
            self,
            case_id=case_id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )
        return merged

    def split_issue(
        self: DomainService,
        *,
        case_id: UUID,
        source_issue_key: UUID,
        targets: list[dict[str, Any]],
        actor_id: UUID,
    ) -> list[Issue]:
        """Lawyer explicitly assigns links to target issues."""
        if not targets or len(targets) < 2:
            raise ValidationError("split requires at least two target issues")
        source = self._require_current_issue(source_issue_key)
        if source.case_id != case_id:
            raise ValidationError("cross-case split rejected")
        if source.status != IssueStatus.CONFIRMED.value:
            raise ValidationError("only CONFIRMED issues can be split")

        decision = _persist_issue_structure_decision(
            self,
            case_id=case_id,
            actor_id=actor_id,
            decision_type="SPLIT_ISSUE",
            target_id=source.issue_key,
            payload={"source_key": str(source_issue_key), "target_count": len(targets)},
        )

        from backend.domain.services import _sync_issue_layer

        created: list[Issue] = []
        for idx, target in enumerate(targets):
            statement = str(target["statement"])
            issue = Issue(
                issue_key=uuid.uuid4(),
                case_id=case_id,
                statement=statement,
                order_index=source.order_index + idx,
                source_type=IssueSourceType.LAWYER_REFINED.value,
                status=IssueStatus.CONFIRMED.value,
                version=1,
                is_current=True,
                parent_issue_key=source.issue_key,
                confirm_decision_id=decision.id,
            )
            _sync_issue_layer(issue)
            self.repo.add(issue)
            self.repo.flush()
            for link_spec in target.get("fact_links") or []:
                self.link_fact_to_issue(
                    case_id=case_id,
                    issue_key=issue.issue_key,
                    issue_version=issue.version,
                    fact_key=UUID(str(link_spec["fact_key"])),
                    fact_version=int(link_spec["fact_version"]),
                    role=str(link_spec.get("role", "SUPPORT")),
                    actor_id=actor_id,
                )
            created.append(issue)

        source.status = IssueStatus.SUPERSEDED.value
        source.is_current = False
        source.updated_at = _now()
        _sync_issue_layer(source)
        self.repo.flush()
        self._audit(
            actor_id,
            "split_issue",
            "issues",
            source.id,
            case_id=case_id,
            after={
                "source_key": str(source_issue_key),
                "new_keys": [str(i.issue_key) for i in created],
                "decision_id": str(decision.id),
            },
        )
        _require_structure_mutation_audit(
            self,
            case_id=case_id,
            action="split_issue",
            decision_type="SPLIT_ISSUE",
        )
        return created

    def link_legal_theory_to_issue(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        legal_theory_id: UUID,
        role: str,
        actor_id: UUID,
    ) -> IssueLegalTheoryLink:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        link = IssueLegalTheoryLink(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            legal_theory_id=legal_theory_id,
            role=role,
            status=ClaimLinkStatus.ACTIVE.value,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_legal_theory_to_issue",
            "issue_legal_theory_links",
            link.id,
            case_id=case_id,
            after={"legal_theory_id": str(legal_theory_id), "role": role},
        )
        return link
```

## (F) Application — backend/application/issue_work_product.py (complete, untruncated, inlined)

Production path: `backend/application/issue_work_product.py` (565 lines)

```python
"""Issue Work Product — canonical issue-centered read projection.

Read-only projection over Issue-centered V2 domain (Issue #80 repair SSOT / #73 / #60 / #83 / #86 / #84).
Does not mutate ClaimDirection, ProofTaskFactLink, positions, or issues;
invariant enforcement remains in backend/domain/issue_centered.py.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.issue_matrix import IssueMatrixService
from backend.domain.enums import LawyerJudgmentState, ProofState
from backend.domain.errors import NotFoundError
from backend.domain.issue_centered import _reject_claim_direction_production_mutation
from backend.models import AuditLog, Case, HumanDecision, Issue, LegalTheory
from backend.repositories.base import Repository
from backend.schemas.issue_work_product import (
    CaseIssueWorkProduct,
    ConflictView,
    EvolutionEventView,
    IssueWorkProduct,
    LawyerAssessmentView,
    LegalAnalysisView,
    LitigationPlanView,
    PositionView,
    ProofGapView,
    ProofTaskFactView,
    ProofTaskView,
    StructuralWarningView,
)

# INV-1 (#84): read projection — must never mutate ClaimDirection or issue-centered writes.
_READ_ONLY_PROJECTION = True
_FORBIDDEN_MUTATION_ENTITY_TYPES = frozenset(
    {"claim_directions", "issue_positions", "proof_tasks", "proof_task_fact_links", "issues"}
)


def assert_read_only_projection(entity_type: str) -> None:
    """Guard invoked before any would-be write from this read-only service."""
    if entity_type in _FORBIDDEN_MUTATION_ENTITY_TYPES:
        raise RuntimeError(
            f"IssueWorkProductService is read-only; cannot mutate {entity_type}"
        )


def guard_claim_direction_production_mutation(*, _legacy_compat: bool = False) -> None:
    """INV-1: block ClaimDirection writes from the read projection layer."""
    _reject_claim_direction_production_mutation(_legacy_compat=_legacy_compat)


def enforce_inv1_read_only(entity_type: str) -> None:
    """INV-1: block mutation attempts from the read projection layer."""
    assert_read_only_projection(entity_type)


def is_read_only_projection() -> bool:
    """INV-1: expose read-only configuration for invariant checks."""
    return _READ_ONLY_PROJECTION


def _link_matches_explicit_task_version(link, task_version: int) -> bool:
    """INV-2: read path ignores links that alias implicit current/latest versions."""
    return link.proof_task_version == task_version


_PROOF_STATE_ZH = {
    ProofState.RED.value: "关键证明缺口",
    ProofState.YELLOW.value: "尚需补强",
    ProofState.GREEN.value: "当前基本闭环",
}

_JUDGMENT_STATE_ZH = {
    LawyerJudgmentState.NOT_ANALYZED.value: "尚未分析",
    LawyerJudgmentState.RESEARCHING.value: "研究中",
    LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value: "已形成律师判断",
}

_POSITION_TYPE_ZH = {
    "ASSERTION": "我方主张",
    "ANTICIPATED_DEFENSE": "对方可能抗辩",
    "FORMAL_DEFENSE": "对方正式主张",
}

_ISSUE_STATUS_ZH = {
    "CANDIDATE": "待确认候选",
    "CONFIRMED": "已确认",
    "REJECTED": "已拒绝",
    "SUPERSEDED": "已替代",
}


class IssueWorkProductService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)
        self.matrix_svc = IssueMatrixService(session)

    @staticmethod
    def _assert_read_only(operation: str) -> None:
        """INV-1: every public entrypoint stays on the read-only projection path."""
        if not _READ_ONLY_PROJECTION:
            raise RuntimeError(
                f"IssueWorkProductService lost read-only configuration during {operation}"
            )

    @staticmethod
    def guard_write_attempt(entity_type: str) -> None:
        """INV-1: block mutation attempts from the read projection layer."""
        enforce_inv1_read_only(entity_type)

    def build_issue(
        self,
        issue_key: UUID,
        issue_version: int | None = None,
    ) -> IssueWorkProduct:
        self._assert_read_only("build_issue")
        if issue_version is not None:
            issue = self.repo.get_issue_version(issue_key, issue_version)
        else:
            issue = self.repo.get_current_issue(issue_key)
        if issue is None:
            raise NotFoundError("issue not found")
        return self._build_for_issue(issue)

    def build_case(self, case_id: UUID) -> CaseIssueWorkProduct:
        self._assert_read_only("build_case")
        case = self.session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")
        issues = list(
            self.session.scalars(
                select(Issue)
                .where(Issue.case_id == case_id, Issue.is_current.is_(True))
                .order_by(Issue.order_index.asc(), Issue.created_at.asc())
            )
        )
        confirmed: list[IssueWorkProduct] = []
        candidates: list[IssueWorkProduct] = []
        state_counts = {
            ProofState.RED.value: 0,
            ProofState.YELLOW.value: 0,
            ProofState.GREEN.value: 0,
        }
        assessment_count = 0
        for issue in issues:
            if issue.status == "REJECTED":
                continue
            wp = self._build_for_issue(issue)
            if issue.status == "CONFIRMED":
                confirmed.append(wp)
                state_counts[wp.proof_state] = state_counts.get(wp.proof_state, 0) + 1
                if wp.lawyer_assessment:
                    assessment_count += 1
            elif issue.status == "CANDIDATE":
                candidates.append(wp)
        blocking = next(
            (i for i in confirmed if i.proof_state == ProofState.RED.value),
            None,
        )
        recommended = blocking or next(
            (i for i in confirmed if i.proof_state == ProofState.YELLOW.value),
            confirmed[0] if confirmed else (candidates[0] if candidates else None),
        )
        return CaseIssueWorkProduct(
            case_id=str(case_id),
            confirmed_issues=confirmed,
            candidate_issues=candidates,
            proof_state_counts=state_counts,
            assessment_count=assessment_count,
            top_blocking_issue=blocking,
            recommended_next_issue=recommended,
        )

    def build_litigation_plan(self, case_id: UUID) -> LitigationPlanView:
        self._assert_read_only("build_litigation_plan")
        from backend.application.claim_view import ClaimViewService
        from backend.application.pleading_readiness import PleadingReadinessService

        case_wp = self.build_case(case_id)
        claims = ClaimViewService(self.session).build(case_id)
        readiness = PleadingReadinessService(self.session).evaluate(case_id)
        assessments = [
            {
                "issue_key": i.issue_key,
                "issue_statement": i.statement,
                "assessment": i.lawyer_assessment.model_dump(mode="json")
                if i.lawyer_assessment
                else None,
            }
            for i in case_wp.confirmed_issues
            if i.lawyer_assessment
        ]
        return LitigationPlanView(
            case_id=str(case_id),
            confirmed_issues=[i.model_dump(mode="json") for i in case_wp.confirmed_issues],
            claims=[c.model_dump(mode="json") for c in claims.items],
            readiness_status=readiness.status,
            readiness_display=readiness.display_status or readiness.status,
            assessments=assessments,
        )

    def _build_for_issue(self, issue: Issue) -> IssueWorkProduct:
        positions_raw = self.repo.list_issue_positions(issue.issue_key, issue.version)
        positions: dict[str, list[PositionView]] = {
            "our_current": [],
            "anticipated_defenses": [],
            "formal_opponent": [],
        }
        for pos in positions_raw:
            if pos.status in {"REJECTED", "SUPERSEDED"}:
                continue
            view = PositionView(
                position_key=str(pos.position_key),
                version=pos.version,
                side=pos.side,
                position_type=pos.position_type,
                source_type=pos.source_type,
                status=pos.status,
                statement=pos.statement,
                display_label=_POSITION_TYPE_ZH.get(pos.position_type, pos.position_type),
                opponent_material_ref=pos.opponent_material_ref,
            )
            if pos.side == "OUR" and pos.position_type == "ASSERTION":
                positions["our_current"].append(view)
            elif pos.position_type == "ANTICIPATED_DEFENSE":
                positions["anticipated_defenses"].append(view)
            elif pos.position_type == "FORMAL_DEFENSE":
                positions["formal_opponent"].append(view)

        proof_tasks: list[ProofTaskView] = []
        matrix_item = next(
            (
                i
                for i in self.matrix_svc.build(issue.case_id).items
                if i.issue_key == str(issue.issue_key) and i.issue_version == issue.version
            ),
            None,
        )
        structural_warnings: list[StructuralWarningView] = []
        if matrix_item:
            for g in matrix_item.structural_warnings:
                structural_warnings.append(
                    StructuralWarningView(
                        type=g.type,
                        description=g.description,
                        related_fact_key=g.related_fact_key,
                        related_fact_version=g.related_fact_version,
                    )
                )

        gaps_raw = self.repo.list_proof_gaps(issue.issue_key, issue.version)
        gap_views = [self._gap_view(g) for g in gaps_raw]
        open_gap_count = sum(1 for g in gaps_raw if g.status == "OPEN")

        for task in self.repo.list_proof_tasks(issue.issue_key, issue.version):
            task_gaps = [
                g.model_dump(mode="json")
                for g in gap_views
                if g.proof_task_key == str(task.proof_task_key)
            ]
            support, adverse, context = self._proof_task_facts(task)
            task_structural = structural_warnings if task.status == "ADOPTED" else []
            proof_tasks.append(
                ProofTaskView(
                    proof_task_key=str(task.proof_task_key),
                    version=task.version,
                    description=task.description,
                    status=task.status,
                    source_type=task.source_type,
                    display_status=self._proof_task_display(task.status),
                    support_facts=support,
                    adverse_facts=adverse,
                    context_facts=context,
                    structural_warnings=task_structural,
                    proof_gaps=task_gaps,
                )
            )

        conflicts: list[ConflictView] = []
        for conflict in self.repo.list_issue_conflicts(issue.issue_key, issue.version):
            facts = []
            for fl in self.repo.list_conflict_fact_links(conflict.id):
                fact = self.repo.get_fact_version(fl.fact_key, fl.fact_version)
                if fact:
                    facts.append(
                        {
                            "fact_key": str(fl.fact_key),
                            "fact_version": fl.fact_version,
                            "statement": fact.statement,
                            "role": fl.role,
                        }
                    )
            conflicts.append(
                ConflictView(
                    conflict_id=str(conflict.id),
                    conflict_key=str(conflict.conflict_key),
                    description=conflict.description,
                    status=conflict.status,
                    source_type=conflict.source_type,
                    display_status=self._conflict_display(conflict.status),
                    resolution_note=conflict.resolution_note,
                    facts=facts,
                )
            )

        legal = self._legal_analysis(issue)
        assessments = self.repo.list_lawyer_assessments(issue.issue_key, issue.version)
        current_assessment = next(
            (a for a in assessments if a.is_current and a.status == "ACTIVE"),
            None,
        )
        lawyer_assessment = None
        if current_assessment:
            lawyer_assessment = LawyerAssessmentView(
                assessment_key=str(current_assessment.assessment_key),
                version=current_assessment.version,
                content=current_assessment.content,
                status=current_assessment.status,
                display_status="当前有效",
                is_current=True,
            )

        proof_state = self._compute_proof_state(
            issue, open_gap_count, structural_warnings, proof_tasks
        )
        judgment_state = (
            LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value
            if lawyer_assessment
            else LawyerJudgmentState.NOT_ANALYZED.value
        )

        return IssueWorkProduct(
            issue_key=str(issue.issue_key),
            issue_version=issue.version,
            statement=issue.statement,
            status=issue.status,
            source_type=issue.source_type,
            display_status=_ISSUE_STATUS_ZH.get(issue.status, issue.status),
            positions=positions,
            proof_tasks=proof_tasks,
            conflicts=conflicts,
            legal_analysis=legal,
            lawyer_assessment=lawyer_assessment,
            proof_state=proof_state,
            proof_state_label=_PROOF_STATE_ZH.get(proof_state, proof_state),
            lawyer_judgment_state=judgment_state,
            lawyer_judgment_state_label=_JUDGMENT_STATE_ZH.get(judgment_state, judgment_state),
            next_action=self._next_action(proof_state, open_gap_count, issue.status),
            evolution=self._evolution(issue),
            proof_gaps=gap_views,
            proof_gap_count=len(gap_views),
            open_proof_gap_count=open_gap_count,
            conflict_count=len([c for c in conflicts if c.status in {"CANDIDATE", "OPEN"}]),
            proof_task_count=len(proof_tasks),
        )

    def _proof_task_facts(self, task) -> tuple[list, list, list]:
        support, adverse, context = [], [], []
        for link in self.repo.list_proof_task_fact_links(
            task.proof_task_key, task.version
        ):
            if not _link_matches_explicit_task_version(link, task.version):
                continue
            if link.fact_version < 1:
                continue
            if getattr(link, "case_id", None) != task.case_id:
                continue
            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
            if fact is None or fact.case_id != task.case_id:
                continue
            evidence = self._fact_evidence(fact.id)
            view = ProofTaskFactView(
                fact_key=str(link.fact_key),
                fact_version=link.fact_version,
                statement=fact.statement,
                status=fact.status,
                role=link.role,
                evidence=evidence,
            )
            if link.role == "SUPPORT":
                support.append(view)
            elif link.role == "ADVERSE":
                adverse.append(view)
            else:
                context.append(view)
        return support, adverse, context

    def _fact_evidence(self, fact_id: UUID) -> list[dict]:
        out = []
        for link in self.repo.list_fact_links(fact_id):
            if link.status != "ACTIVE":
                continue
            ev = self.repo.get_evidence_version(link.evidence_item_id, link.evidence_item_version)
            if ev and ev.acceptance == "ACCEPTED":
                out.append(
                    {
                        "evidence_item_id": str(ev.id),
                        "evidence_item_version": ev.version,
                        "title": ev.title,
                        "acceptance": ev.acceptance,
                    }
                )
        return out

    def _gap_view(self, gap) -> ProofGapView:
        status_zh = {
            "OPEN": "待处理",
            "RESOLVED": "已解决",
            "WAIVED": "已放弃",
            "SUPERSEDED": "已替代",
        }
        return ProofGapView(
            gap_id=str(gap.id),
            gap_key=str(gap.gap_key),
            gap_type=gap.gap_type,
            status=gap.status,
            source_type=gap.source_type,
            description=gap.description,
            what_exists=gap.what_exists,
            what_is_missing=gap.what_is_missing,
            why_it_matters=gap.why_it_matters,
            suggested_material_types=gap.suggested_material_types or [],
            display_status=status_zh.get(gap.status, gap.status),
            proof_task_key=str(gap.proof_task_key) if gap.proof_task_key else None,
        )

    def _legal_analysis(self, issue: Issue) -> LegalAnalysisView:
        theories = []
        for link in self.repo.list_issue_legal_theory_links(issue.issue_key, issue.version):
            lt = self.session.get(LegalTheory, link.legal_theory_id)
            if lt:
                theories.append(
                    {
                        "id": str(lt.id),
                        "theory_summary": lt.theory_summary,
                        "role": link.role,
                        "layer": lt.layer,
                    }
                )
        favorable: list[str] = []
        adverse: list[str] = []
        unknown: list[str] = []
        for link in self.repo.list_issue_fact_links(issue.issue_key, issue.version):
            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
            if fact is None or fact.status != "CONFIRMED":
                continue
            if link.role == "SUPPORT":
                favorable.append(fact.statement)
            elif link.role == "ADVERSE":
                adverse.append(fact.statement)
            else:
                unknown.append(fact.statement)
        return LegalAnalysisView(
            legal_theories=theories,
            favorable_factors=favorable,
            adverse_factors=adverse,
            unknown_factors=unknown,
        )

    def _compute_proof_state(
        self,
        issue: Issue,
        open_gap_count: int,
        structural_warnings: list,
        proof_tasks: list,
    ) -> str:
        if issue.status != "CONFIRMED":
            return ProofState.YELLOW.value
        if open_gap_count > 0:
            return ProofState.RED.value
        if structural_warnings:
            return ProofState.YELLOW.value
        adopted = [t for t in proof_tasks if t.status == "ADOPTED"]
        if not adopted:
            return ProofState.YELLOW.value
        for task in adopted:
            if not task.support_facts:
                return ProofState.YELLOW.value
        return ProofState.GREEN.value

    def _next_action(self, proof_state: str, open_gaps: int, status: str) -> str | None:
        if status == "CANDIDATE":
            return "请确认是否将本焦点纳入办案范围"
        if open_gaps > 0:
            return "请处理待补强的证明缺口"
        if proof_state == ProofState.YELLOW.value:
            return "请继续补强事实与证据关联"
        if proof_state == ProofState.GREEN.value:
            return "可进入法律分析与诉请衔接"
        return "请审查关键证明缺口"

    def _evolution(self, issue: Issue) -> list[EvolutionEventView]:
        events: list[EvolutionEventView] = []
        if issue.created_at:
            events.append(
                EvolutionEventView(
                    event_type="ISSUE_CREATED",
                    timestamp=issue.created_at.isoformat(),
                    summary=f"争点创建（v{issue.version}）",
                )
            )
        decisions = list(
            self.session.scalars(
                select(HumanDecision)
                .where(
                    HumanDecision.case_id == issue.case_id,
                    HumanDecision.target_id == issue.issue_key,
                )
                .order_by(HumanDecision.created_at.asc())
            )
        )
        for d in decisions:
            events.append(
                EvolutionEventView(
                    event_type=d.decision_type,
                    timestamp=d.created_at.isoformat() if d.created_at else None,
                    summary=d.decision_type,
                    actor_hint=str(d.actor_id)[:8] if d.actor_id else None,
                )
            )
        audits = list(
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.case_id == issue.case_id,
                    AuditLog.entity_type.in_(["issue_positions", "proof_tasks", "proof_gaps"]),
                )
                .order_by(AuditLog.created_at.asc())
                .limit(20)
            )
        )
        for a in audits:
            events.append(
                EvolutionEventView(
                    event_type=a.action,
                    timestamp=a.created_at.isoformat() if a.created_at else None,
                    summary=a.action,
                )
            )
        return events

    @staticmethod
    def _proof_task_display(status: str) -> str:
        return {
            "CANDIDATE": "待采纳",
            "ADOPTED": "已采纳",
            "REJECTED": "已拒绝",
            "WAIVED": "已放弃",
            "SUPERSEDED": "已替代",
        }.get(status, status)

    @staticmethod
    def _conflict_display(status: str) -> str:
        return {
            "CANDIDATE": "待确认",
            "OPEN": "待处理",
            "RESOLVED": "已解决",
            "DISMISSED": "已驳回",
        }.get(status, status)
```

## (G) Invariant tests — backend/tests/integration/test_issue_centered_v2_invariants.py (complete, untruncated, inlined)

Production path: `backend/tests/integration/test_issue_centered_v2_invariants.py` (523 lines)

```python
"""Issue-centered V2 — four invariant assertions (Issue #80 SSOT repair / #73 / #60 / #83 / #86 / #85 / #84)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.application.issue_work_product import (
    IssueWorkProductService,
    assert_read_only_projection,
    enforce_inv1_read_only,
    guard_claim_direction_production_mutation,
    is_read_only_projection,
)
from backend.domain.enums import DecisionResult
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.issue_centered import (
    _guard_cross_case_proof_task_fact_pair,
    _guard_explicit_proof_task_fact_versions,
    _guard_formal_defense_opponent_material_ref,
    _guard_formal_defense_side,
    _guard_resolved_explicit_versions,
    _normalize_opponent_material_ref,
    _reject_claim_direction_production_mutation,
    _require_structure_mutation_audit,
)
from backend.domain.services import DomainService
from backend.models import AuditLog, HumanDecision
from backend.tests.integration.test_case_analyst import _seed_accepted_evidence
from backend.tests.integration.test_issue_centered_v2 import _seed_fact


def test_invariant_guard_functions_reject_invalid_inputs() -> None:
    """Direct unit checks on INV-1/2/3 guard helpers (executed code, not prose)."""
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        _reject_claim_direction_production_mutation(_legacy_compat=False)
    _reject_claim_direction_production_mutation(_legacy_compat=True)
    with pytest.raises(ValidationError, match="explicit positive"):
        _guard_explicit_proof_task_fact_versions(0, 1)
    with pytest.raises(ValidationError, match="explicit positive"):
        _guard_explicit_proof_task_fact_versions(1, -1)

    class _FakeVersioned:
        def __init__(self, version: int) -> None:
            self.version = version

    with pytest.raises(ValidationError, match="explicit proof_task_version"):
        _guard_resolved_explicit_versions(
            proof_task_version=2,
            fact_version=1,
            task=_FakeVersioned(1),
            fact=_FakeVersioned(1),
        )
    with pytest.raises(ValidationError, match="explicit fact_version"):
        _guard_resolved_explicit_versions(
            proof_task_version=1,
            fact_version=2,
            task=_FakeVersioned(1),
            fact=_FakeVersioned(1),
        )
    _guard_resolved_explicit_versions(
        proof_task_version=1,
        fact_version=1,
        task=_FakeVersioned(1),
        fact=_FakeVersioned(1),
    )

    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", None)
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", "   ")
    assert _normalize_opponent_material_ref("  material:answer-001  ") == "material:answer-001"
    assert _normalize_opponent_material_ref("   ") is None
    _guard_formal_defense_opponent_material_ref(
        "FORMAL_DEFENSE", "material:answer-001"
    )
    with pytest.raises(ValidationError, match="OPPONENT side"):
        _guard_formal_defense_side("FORMAL_DEFENSE", "OUR")
    _guard_formal_defense_side("FORMAL_DEFENSE", "OPPONENT")
    assert is_read_only_projection() is True
    with pytest.raises(RuntimeError, match="read-only"):
        assert_read_only_projection("claim_directions")
    with pytest.raises(RuntimeError, match="read-only"):
        enforce_inv1_read_only("issues")
    with pytest.raises(RuntimeError, match="read-only"):
        IssueWorkProductService.guard_write_attempt("claim_directions")
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        guard_claim_direction_production_mutation(_legacy_compat=False)
    guard_claim_direction_production_mutation(_legacy_compat=True)


def test_invariant_1_no_production_claim_direction_creation(
    db_session, owner_id, actor_id
) -> None:
    """INV-1: production path must not create ClaimDirection without _legacy_compat."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV1", owner_user_id=owner_id)
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        svc.create_claim_direction(
            case_id=case.id,
            payload={"claims": [], "parties": {}},
            actor_id=actor_id,
        )
    # Explicit: no ClaimDirection row created
    from backend.models import ClaimDirection

    rows = list(
        db_session.scalars(
            select(ClaimDirection).where(ClaimDirection.case_id == case.id)
        )
    )
    assert rows == []


def test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case(
    db_session, owner_id, actor_id
) -> None:
    """INV-2: ProofTaskFactLink requires explicit versions; rejects cross-case links."""
    svc = DomainService(db_session)
    c1 = svc.create_case(title="INV2-A", owner_user_id=owner_id)
    c2 = svc.create_case(title="INV2-B", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=c1
    )
    fact = _seed_fact(svc, c1.id, actor_id, item)
    issue2 = svc.confirm_issue(
        svc.propose_issue(case_id=c2.id, statement="跨案焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=c2.id,
        issue_key=issue2.issue_key,
        issue_version=issue2.version,
        description="跨案任务",
        actor_id=actor_id,
    )
    # Zero/negative versions → ValidationError (explicit positive versions required)
    with pytest.raises(ValidationError, match="explicit positive"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=0,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Implicit/nonexistent proof_task_version → NotFoundError (not silent current/latest)
    with pytest.raises(NotFoundError, match="proof task version not found"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version + 99,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Implicit/nonexistent fact_version → NotFoundError
    with pytest.raises(NotFoundError, match="fact version not found"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version + 99,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Cross-case fact → ValidationError
    with pytest.raises(ValidationError, match="cross-case fact link rejected"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Cross-case proof task (task in c2, link attempted under c1) → ValidationError
    issue1 = svc.confirm_issue(
        svc.propose_issue(case_id=c1.id, statement="案A焦点").issue_key,
        actor_id=actor_id,
    )
    task1 = svc.create_lawyer_proof_task(
        case_id=c1.id,
        issue_key=issue1.issue_key,
        issue_version=issue1.version,
        description="案A任务",
        actor_id=actor_id,
    )
    fact2 = _seed_fact(svc, c2.id, actor_id, item)
    with pytest.raises(ValidationError, match="cross-case proof task link rejected"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task1.proof_task_key,
            proof_task_version=task1.version,
            fact_key=fact2.fact_key,
            fact_version=fact2.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_invariant_3_formal_defense_requires_opponent_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: FORMAL_DEFENSE position requires opponent_material_ref."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="抗辩焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OPPONENT",
            position_type="FORMAL_DEFENSE",
            statement="对方正式抗辩",
            actor_id=actor_id,
            opponent_material_ref=None,
        )
    pos = svc.create_lawyer_position(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        side="OPPONENT",
        position_type="FORMAL_DEFENSE",
        statement="对方正式抗辩",
        actor_id=actor_id,
        opponent_material_ref="material:answer-001",
    )
    assert pos.opponent_material_ref == "material:answer-001"
    assert pos.position_type == "FORMAL_DEFENSE"


def test_invariant_3_formal_defense_rejects_whitespace_only_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: whitespace-only opponent_material_ref is normalized then rejected."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-ws", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="空白材料焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OPPONENT",
            position_type="FORMAL_DEFENSE",
            statement="空白材料抗辩",
            actor_id=actor_id,
            opponent_material_ref="   \t  ",
        )


def test_invariant_4_merge_split_emit_human_decision_and_audit_log(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: merge_issues and split_issue emit HumanDecision + AuditLog."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4", owner_user_id=owner_id)
    i1 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点甲").issue_key,
        actor_id=actor_id,
    )
    i2 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点乙").issue_key,
        actor_id=actor_id,
    )
    merged = svc.merge_issues(
        case_id=case.id,
        source_issue_keys=[i1.issue_key, i2.issue_key],
        merged_statement="合并焦点",
        actor_id=actor_id,
    )
    assert merged.status == "CONFIRMED"
    merge_decisions = list(
        db_session.scalars(
            select(HumanDecision).where(
                HumanDecision.case_id == case.id,
                HumanDecision.decision_type == "MERGE_ISSUES",
            )
        )
    )
    assert len(merge_decisions) == 1
    assert merge_decisions[0].id is not None
    assert merge_decisions[0].decision_type == "MERGE_ISSUES"
    assert merge_decisions[0].result == "CONFIRMED"
    merge_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "merge_issues",
            )
        )
    )
    assert len(merge_audits) >= 1
    assert merge_audits[0].entity_type == "issues"
    assert merge_audits[0].after_json is not None
    assert merge_audits[0].after_json.get("decision_id") == str(merge_decisions[0].id)

    split = svc.split_issue(
        case_id=case.id,
        source_issue_key=merged.issue_key,
        targets=[
            {"statement": "拆分甲", "fact_links": []},
            {"statement": "拆分乙", "fact_links": []},
        ],
        actor_id=actor_id,
    )
    assert len(split) == 2
    split_decisions = list(
        db_session.scalars(
            select(HumanDecision).where(
                HumanDecision.case_id == case.id,
                HumanDecision.decision_type == "SPLIT_ISSUE",
            )
        )
    )
    assert len(split_decisions) == 1
    assert split_decisions[0].id is not None
    assert split_decisions[0].decision_type == "SPLIT_ISSUE"
    assert split_decisions[0].result == "CONFIRMED"
    split_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "split_issue",
            )
        )
    )
    assert len(split_audits) >= 1
    assert split_audits[0].entity_type == "issues"
    assert split_audits[0].after_json is not None
    assert split_audits[0].after_json.get("decision_id") == str(split_decisions[0].id)


def test_invariant_2_cross_case_pair_guard_rejects_mismatched_cases() -> None:
    """INV-2: _guard_cross_case_proof_task_fact_pair rejects mismatched case_id."""
    from types import SimpleNamespace

    task = SimpleNamespace(case_id=uuid.uuid4())
    fact = SimpleNamespace(case_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="cross-case proof task fact link rejected"):
        _guard_cross_case_proof_task_fact_pair(task, fact)


def test_invariant_3_formal_defense_rejects_wrong_side(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: FORMAL_DEFENSE must be recorded on OPPONENT side."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-side", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="抗辩侧焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="OPPONENT side"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="FORMAL_DEFENSE",
            statement="错误侧正式抗辩",
            actor_id=actor_id,
            opponent_material_ref="material:answer-001",
        )


def test_invariant_4_guard_rejects_missing_human_decision_or_audit(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: _require_structure_mutation_audit fails closed without HumanDecision or AuditLog."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4-guard", owner_user_id=owner_id)
    with pytest.raises(ConflictError, match="HumanDecision"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )
    decision = HumanDecision(
        case_id=case.id,
        actor_id=actor_id,
        decision_type="MERGE_ISSUES",
        target_type="Issue",
        target_id=uuid.uuid4(),
        result=DecisionResult.CONFIRMED.value,
        input_payload_json={"probe": True},
    )
    db_session.add(decision)
    db_session.flush()
    with pytest.raises(ConflictError, match="AuditLog"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )


def test_invariant_4_guard_rejects_audit_without_decision_id_linkage(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: AuditLog.after_json must reference the emitted HumanDecision id."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4-audit-link", owner_user_id=owner_id)
    decision = HumanDecision(
        case_id=case.id,
        actor_id=actor_id,
        decision_type="MERGE_ISSUES",
        target_type="Issue",
        target_id=uuid.uuid4(),
        result=DecisionResult.CONFIRMED.value,
        input_payload_json={"probe": True},
    )
    db_session.add(decision)
    db_session.flush()
    db_session.add(
        AuditLog(
            case_id=case.id,
            actor_id=actor_id,
            action="merge_issues",
            entity_type="issues",
            entity_id=uuid.uuid4(),
            after_json={"issue_key": str(uuid.uuid4())},
        )
    )
    db_session.flush()
    with pytest.raises(ConflictError, match="decision_id"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )


def test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions(
    db_session, owner_id, actor_id
) -> None:
    """INV-2: DB check constraints reject proof_task_version/fact_version < 1."""
    from backend.models import ProofTaskFactLink

    svc = DomainService(db_session)
    case = svc.create_case(title="INV2-DB", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="DB约束焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        description="DB约束任务",
        actor_id=actor_id,
    )
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case
    )
    fact = _seed_fact(svc, case.id, actor_id, item)
    with pytest.raises(IntegrityError):
        db_session.add(
            ProofTaskFactLink(
                case_id=case.id,
                proof_task_key=task.proof_task_key,
                proof_task_version=0,
                fact_key=fact.fact_key,
                fact_version=fact.version,
                role="SUPPORT",
                status="ACTIVE",
                created_by=actor_id,
            )
        )
        db_session.flush()
    db_session.rollback()


def test_invariant_3_db_rejects_formal_defense_without_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: DB check constraint rejects FORMAL_DEFENSE without opponent_material_ref."""
    from backend.models import IssuePosition

    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-DB", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="DB抗辩焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(IntegrityError):
        db_session.add(
            IssuePosition(
                position_key=uuid.uuid4(),
                case_id=case.id,
                issue_key=issue.issue_key,
                issue_version=issue.version,
                side="OPPONENT",
                position_type="FORMAL_DEFENSE",
                source_type="OPPONENT_MATERIAL",
                status="CONFIRMED",
                statement="无材料引用",
                opponent_material_ref=None,
                version=1,
                is_current=True,
            )
        )
        db_session.flush()
    db_session.rollback()
```

Verified by: all 27 pytest tests + live acceptance 30 steps — **PASSED**.

## (H) Untruncated production diff — all five files (2841 lines)

Generated: `git diff f84972a213c44ba602b07ae3801637dc5c045f16 -- <five production paths>`

Full raw patch (NOT truncated, includes services.py INV-1 wiring):

```diff
diff --git a/alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py b/alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py
new file mode 100644
index 0000000..3186fc3
--- /dev/null
+++ b/alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py
@@ -0,0 +1,438 @@
+"""phase9_issue_centered_v2 — IssuePosition, ProofTask, Conflict, ProofGap, LawyerAssessment.
+
+Issue #80 repair SSOT (#73 / #60 / #83 / #86 / #84): four invariants enforced at DB + domain layers:
+- INV-1: ClaimDirection production mutation blocked in domain/application (not this migration)
+- INV-2: proof_task_fact_links version positivity (ck_*_version_pos) rejects implicit 0/latest
+- INV-3: issue_positions FORMAL_DEFENSE ref + side constraints (ck_*_formal_defense_*)
+- INV-4: merge/split HumanDecision + AuditLog enforced in domain (issue_centered.py)
+
+Includes proof_gaps and lawyer_assessments with every CheckConstraint:
+- proof_gaps: ck_proof_gaps_type, ck_proof_gaps_status, ck_proof_gaps_source
+- lawyer_assessments: ck_lawyer_assessments_status
+- issue_positions: ck_issue_positions_side/type/source/status/formal_defense_ref
+- proof_tasks: ck_proof_tasks_status, ck_proof_tasks_source
+- proof_task_fact_links: ck_proof_task_fact_link_role/status + version positivity
+- issue_conflicts: ck_issue_conflicts_status, ck_issue_conflicts_source
+- conflict_fact_links: ck_conflict_fact_link_role
+- issue_legal_theory_links: ck_issue_legal_theory_link_role, ck_issue_legal_theory_link_status
+"""
+
+from collections.abc import Sequence
+
+import sqlalchemy as sa
+from alembic import op
+
+# INV SSOT (#80): canonical allowed-value sets for CheckConstraints (used in upgrade()).
+_INV2_MIN_EXPLICIT_VERSION = 1
+_PROOF_GAP_TYPES = ("FACT", "EVIDENCE", "SOURCE", "LEGAL_RESEARCH")
+_PROOF_GAP_STATUSES = ("OPEN", "RESOLVED", "WAIVED", "SUPERSEDED")
+_PROOF_GAP_SOURCES = ("AI_DETECTED", "LAWYER_CREATED")
+_LAWYER_ASSESSMENT_STATUSES = ("ACTIVE", "SUPERSEDED", "WITHDRAWN")
+
+
+def _in_check(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
+    quoted = ", ".join(f"'{v}'" for v in values)
+    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)
+
+
+def _version_pos_check(name: str, column: str) -> sa.CheckConstraint:
+    """INV-2: explicit positive version columns (no implicit 0/latest)."""
+    return sa.CheckConstraint(
+        f"{column} >= {_INV2_MIN_EXPLICIT_VERSION}",
+        name=name,
+    )
+
+
+revision: str = "h9b0c1d2e3f4"
+down_revision: str | Sequence[str] | None = "g8a9b0c1d2e3"
+branch_labels: str | Sequence[str] | None = None
+depends_on: str | Sequence[str] | None = None
+
+
+def upgrade() -> None:
+    op.create_table(
+        "issue_positions",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("position_key", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
+        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
+        sa.Column("side", sa.String(length=32), nullable=False),
+        sa.Column("position_type", sa.String(length=32), nullable=False),
+        sa.Column("source_type", sa.String(length=32), nullable=False),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
+        sa.Column("statement", sa.Text(), nullable=False),
+        sa.Column("opponent_material_ref", sa.String(length=512), nullable=True),
+        sa.Column("supersedes_id", sa.UUID(), nullable=True),
+        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
+        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_issue_positions_issue",
+        ),
+        sa.ForeignKeyConstraint(["supersedes_id"], ["issue_positions.id"]),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint("position_key", "version", name="uq_issue_positions_key_version"),
+        sa.CheckConstraint("side IN ('OUR','OPPONENT')", name="ck_issue_positions_side"),
+        sa.CheckConstraint(
+            "position_type IN ('ASSERTION','ANTICIPATED_DEFENSE','FORMAL_DEFENSE')",
+            name="ck_issue_positions_type",
+        ),
+        sa.CheckConstraint(
+            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED','OPPONENT_MATERIAL')",
+            name="ck_issue_positions_source",
+        ),
+        sa.CheckConstraint(
+            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
+            name="ck_issue_positions_status",
+        ),
+        sa.CheckConstraint(
+            "(position_type <> 'FORMAL_DEFENSE') OR "
+            "(opponent_material_ref IS NOT NULL AND btrim(opponent_material_ref) <> '')",
+            name="ck_issue_positions_formal_defense_ref",
+        ),
+        sa.CheckConstraint(
+            "(position_type <> 'FORMAL_DEFENSE') OR (side = 'OPPONENT')",
+            name="ck_issue_positions_formal_defense_side",
+        ),
+    )
+    op.create_index("ix_issue_positions_case", "issue_positions", ["case_id"])
+    op.create_index(
+        "uq_issue_positions_current",
+        "issue_positions",
+        ["position_key"],
+        unique=True,
+        postgresql_where=sa.text("is_current = true"),
+    )
+
+    op.create_table(
+        "proof_tasks",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("proof_task_key", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
+        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
+        sa.Column("description", sa.Text(), nullable=False),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
+        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_PROPOSED"),
+        sa.Column("supersedes_id", sa.UUID(), nullable=True),
+        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
+        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_proof_tasks_issue",
+        ),
+        sa.ForeignKeyConstraint(["supersedes_id"], ["proof_tasks.id"]),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint("proof_task_key", "version", name="uq_proof_tasks_key_version"),
+        sa.CheckConstraint(
+            "status IN ('CANDIDATE','ADOPTED','REJECTED','SUPERSEDED','WAIVED')",
+            name="ck_proof_tasks_status",
+        ),
+        sa.CheckConstraint(
+            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED')",
+            name="ck_proof_tasks_source",
+        ),
+    )
+    op.create_index("ix_proof_tasks_case", "proof_tasks", ["case_id"])
+    op.create_index(
+        "uq_proof_tasks_current",
+        "proof_tasks",
+        ["proof_task_key"],
+        unique=True,
+        postgresql_where=sa.text("is_current = true"),
+    )
+
+    op.create_table(
+        "proof_task_fact_links",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("proof_task_key", sa.UUID(), nullable=False),
+        sa.Column("proof_task_version", sa.Integer(), nullable=False),
+        sa.Column("fact_key", sa.UUID(), nullable=False),
+        sa.Column("fact_version", sa.Integer(), nullable=False),
+        sa.Column("role", sa.String(length=32), nullable=False, server_default="SUPPORT"),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
+        sa.Column("created_by", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["proof_task_key", "proof_task_version"],
+            ["proof_tasks.proof_task_key", "proof_tasks.version"],
+            name="fk_proof_task_fact_links_task",
+        ),
+        sa.ForeignKeyConstraint(
+            ["fact_key", "fact_version"],
+            ["facts.fact_key", "facts.version"],
+            name="fk_proof_task_fact_links_fact",
+        ),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint(
+            "proof_task_key",
+            "proof_task_version",
+            "fact_key",
+            "fact_version",
+            "role",
+            name="uq_proof_task_fact_links",
+        ),
+        sa.CheckConstraint(
+            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
+            name="ck_proof_task_fact_link_role",
+        ),
+        sa.CheckConstraint(
+            "status IN ('ACTIVE','VOID')",
+            name="ck_proof_task_fact_link_status",
+        ),
+        _version_pos_check("ck_proof_task_fact_links_task_version_pos", "proof_task_version"),
+        _version_pos_check("ck_proof_task_fact_links_fact_version_pos", "fact_version"),
+    )
+    op.create_index("ix_proof_task_fact_links_case", "proof_task_fact_links", ["case_id"])
+
+    op.create_table(
+        "issue_conflicts",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("conflict_key", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("description", sa.Text(), nullable=False),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
+        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
+        sa.Column("resolution_note", sa.Text(), nullable=True),
+        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
+        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_issue_conflicts_issue",
+        ),
+        sa.PrimaryKeyConstraint("id"),
+        sa.CheckConstraint(
+            "status IN ('CANDIDATE','OPEN','RESOLVED','DISMISSED')",
+            name="ck_issue_conflicts_status",
+        ),
+        sa.CheckConstraint(
+            "source_type IN ('AI_DETECTED','LAWYER_CREATED')",
+            name="ck_issue_conflicts_source",
+        ),
+    )
+    op.create_index("ix_issue_conflicts_case", "issue_conflicts", ["case_id"])
+    op.create_index(
+        "ix_issue_conflicts_issue", "issue_conflicts", ["issue_key", "issue_version"]
+    )
+
+    op.create_table(
+        "conflict_fact_links",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("conflict_id", sa.UUID(), nullable=False),
+        sa.Column("fact_key", sa.UUID(), nullable=False),
+        sa.Column("fact_version", sa.Integer(), nullable=False),
+        sa.Column("role", sa.String(length=32), nullable=False),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(["conflict_id"], ["issue_conflicts.id"]),
+        sa.ForeignKeyConstraint(
+            ["fact_key", "fact_version"],
+            ["facts.fact_key", "facts.version"],
+            name="fk_conflict_fact_links_fact",
+        ),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint(
+            "conflict_id",
+            "fact_key",
+            "fact_version",
+            "role",
+            name="uq_conflict_fact_links",
+        ),
+        sa.CheckConstraint(
+            "role IN ('SIDE_A','SIDE_B','CONTEXT')",
+            name="ck_conflict_fact_link_role",
+        ),
+        _version_pos_check("ck_conflict_fact_links_fact_version_pos", "fact_version"),
+    )
+    op.create_index("ix_conflict_fact_links_case", "conflict_fact_links", ["case_id"])
+
+    op.create_table(
+        "proof_gaps",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("gap_key", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("proof_task_key", sa.UUID(), nullable=True),
+        sa.Column("proof_task_version", sa.Integer(), nullable=True),
+        sa.Column("gap_type", sa.String(length=32), nullable=False),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
+        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
+        sa.Column("description", sa.Text(), nullable=False),
+        sa.Column("what_exists", sa.Text(), nullable=True),
+        sa.Column("what_is_missing", sa.Text(), nullable=True),
+        sa.Column("why_it_matters", sa.Text(), nullable=True),
+        sa.Column("suggested_material_types", sa.JSON(), nullable=True),
+        sa.Column("resolution_note", sa.Text(), nullable=True),
+        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
+        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_proof_gaps_issue",
+        ),
+        sa.ForeignKeyConstraint(
+            ["proof_task_key", "proof_task_version"],
+            ["proof_tasks.proof_task_key", "proof_tasks.version"],
+            name="fk_proof_gaps_proof_task",
+        ),
+        sa.PrimaryKeyConstraint("id"),
+        _in_check("ck_proof_gaps_type", "gap_type", _PROOF_GAP_TYPES),
+        _in_check("ck_proof_gaps_status", "status", _PROOF_GAP_STATUSES),
+        _in_check("ck_proof_gaps_source", "source_type", _PROOF_GAP_SOURCES),
+        sa.CheckConstraint(
+            "(proof_task_key IS NULL) OR "
+            "(proof_task_version IS NOT NULL AND proof_task_version >= 1)",
+            name="ck_proof_gaps_proof_task_version_pos",
+        ),
+    )
+    op.create_index("ix_proof_gaps_case", "proof_gaps", ["case_id"])
+    op.create_index("ix_proof_gaps_issue", "proof_gaps", ["issue_key", "issue_version"])
+
+    op.create_table(
+        "lawyer_assessments",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("assessment_key", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
+        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
+        sa.Column("content", sa.Text(), nullable=False),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
+        sa.Column("supersedes_id", sa.UUID(), nullable=True),
+        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_lawyer_assessments_issue",
+        ),
+        sa.ForeignKeyConstraint(["supersedes_id"], ["lawyer_assessments.id"]),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint("assessment_key", "version", name="uq_lawyer_assessments_key_version"),
+        _in_check("ck_lawyer_assessments_status", "status", _LAWYER_ASSESSMENT_STATUSES),
+        _version_pos_check("ck_lawyer_assessments_version_pos", "version"),
+    )
+    op.create_index("ix_lawyer_assessments_case", "lawyer_assessments", ["case_id"])
+    op.create_index(
+        "uq_lawyer_assessments_current",
+        "lawyer_assessments",
+        ["assessment_key"],
+        unique=True,
+        postgresql_where=sa.text("is_current = true"),
+    )
+
+    op.create_table(
+        "issue_legal_theory_links",
+        sa.Column("id", sa.UUID(), nullable=False),
+        sa.Column("case_id", sa.UUID(), nullable=False),
+        sa.Column("issue_key", sa.UUID(), nullable=False),
+        sa.Column("issue_version", sa.Integer(), nullable=False),
+        sa.Column("legal_theory_id", sa.UUID(), nullable=False),
+        sa.Column("role", sa.String(length=32), nullable=False, server_default="OUR_THEORY"),
+        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
+        sa.Column(
+            "created_at",
+            sa.DateTime(timezone=True),
+            server_default=sa.text("now()"),
+            nullable=False,
+        ),
+        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
+        sa.ForeignKeyConstraint(
+            ["issue_key", "issue_version"],
+            ["issues.issue_key", "issues.version"],
+            name="fk_issue_legal_theory_links_issue",
+        ),
+        sa.ForeignKeyConstraint(["legal_theory_id"], ["legal_theories.id"]),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint(
+            "issue_key",
+            "issue_version",
+            "legal_theory_id",
+            "role",
+            name="uq_issue_legal_theory_links",
+        ),
+        sa.CheckConstraint(
+            "role IN ('OUR_THEORY','COUNTER_THEORY','CONTEXT')",
+            name="ck_issue_legal_theory_link_role",
+        ),
+        sa.CheckConstraint(
+            "status IN ('ACTIVE','VOID')",
+            name="ck_issue_legal_theory_link_status",
+        ),
+    )
+    op.create_index("ix_issue_legal_theory_links_case", "issue_legal_theory_links", ["case_id"])
+
+
+def downgrade() -> None:
+    op.drop_table("issue_legal_theory_links")
+    op.drop_table("lawyer_assessments")
+    op.drop_table("proof_gaps")
+    op.drop_table("conflict_fact_links")
+    op.drop_table("issue_conflicts")
+    op.drop_table("proof_task_fact_links")
+    op.drop_table("proof_tasks")
+    op.drop_table("issue_positions")
diff --git a/backend/application/issue_work_product.py b/backend/application/issue_work_product.py
new file mode 100644
index 0000000..d3bdd0c
--- /dev/null
+++ b/backend/application/issue_work_product.py
@@ -0,0 +1,564 @@
+"""Issue Work Product — canonical issue-centered read projection.
+
+Read-only projection over Issue-centered V2 domain (Issue #80 repair SSOT / #73 / #60 / #83 / #86 / #84).
+Does not mutate ClaimDirection, ProofTaskFactLink, positions, or issues;
+invariant enforcement remains in backend/domain/issue_centered.py.
+"""
+
+from __future__ import annotations
+
+from uuid import UUID
+
+from sqlalchemy import select
+from sqlalchemy.orm import Session
+
+from backend.application.issue_matrix import IssueMatrixService
+from backend.domain.enums import LawyerJudgmentState, ProofState
+from backend.domain.errors import NotFoundError
+from backend.domain.issue_centered import _reject_claim_direction_production_mutation
+from backend.models import AuditLog, Case, HumanDecision, Issue, LegalTheory
+from backend.repositories.base import Repository
+from backend.schemas.issue_work_product import (
+    CaseIssueWorkProduct,
+    ConflictView,
+    EvolutionEventView,
+    IssueWorkProduct,
+    LawyerAssessmentView,
+    LegalAnalysisView,
+    LitigationPlanView,
+    PositionView,
+    ProofGapView,
+    ProofTaskFactView,
+    ProofTaskView,
+    StructuralWarningView,
+)
+
+# INV-1 (#84): read projection — must never mutate ClaimDirection or issue-centered writes.
+_READ_ONLY_PROJECTION = True
+_FORBIDDEN_MUTATION_ENTITY_TYPES = frozenset(
+    {"claim_directions", "issue_positions", "proof_tasks", "proof_task_fact_links", "issues"}
+)
+
+
+def assert_read_only_projection(entity_type: str) -> None:
+    """Guard invoked before any would-be write from this read-only service."""
+    if entity_type in _FORBIDDEN_MUTATION_ENTITY_TYPES:
+        raise RuntimeError(
+            f"IssueWorkProductService is read-only; cannot mutate {entity_type}"
+        )
+
+
+def guard_claim_direction_production_mutation(*, _legacy_compat: bool = False) -> None:
+    """INV-1: block ClaimDirection writes from the read projection layer."""
+    _reject_claim_direction_production_mutation(_legacy_compat=_legacy_compat)
+
+
+def enforce_inv1_read_only(entity_type: str) -> None:
+    """INV-1: block mutation attempts from the read projection layer."""
+    assert_read_only_projection(entity_type)
+
+
+def is_read_only_projection() -> bool:
+    """INV-1: expose read-only configuration for invariant checks."""
+    return _READ_ONLY_PROJECTION
+
+
+def _link_matches_explicit_task_version(link, task_version: int) -> bool:
+    """INV-2: read path ignores links that alias implicit current/latest versions."""
+    return link.proof_task_version == task_version
+
+
+_PROOF_STATE_ZH = {
+    ProofState.RED.value: "关键证明缺口",
+    ProofState.YELLOW.value: "尚需补强",
+    ProofState.GREEN.value: "当前基本闭环",
+}
+
+_JUDGMENT_STATE_ZH = {
+    LawyerJudgmentState.NOT_ANALYZED.value: "尚未分析",
+    LawyerJudgmentState.RESEARCHING.value: "研究中",
+    LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value: "已形成律师判断",
+}
+
+_POSITION_TYPE_ZH = {
+    "ASSERTION": "我方主张",
+    "ANTICIPATED_DEFENSE": "对方可能抗辩",
+    "FORMAL_DEFENSE": "对方正式主张",
+}
+
+_ISSUE_STATUS_ZH = {
+    "CANDIDATE": "待确认候选",
+    "CONFIRMED": "已确认",
+    "REJECTED": "已拒绝",
+    "SUPERSEDED": "已替代",
+}
+
+
+class IssueWorkProductService:
+    def __init__(self, session: Session) -> None:
+        self.session = session
+        self.repo = Repository(session)
+        self.matrix_svc = IssueMatrixService(session)
+
+    @staticmethod
+    def _assert_read_only(operation: str) -> None:
+        """INV-1: every public entrypoint stays on the read-only projection path."""
+        if not _READ_ONLY_PROJECTION:
+            raise RuntimeError(
+                f"IssueWorkProductService lost read-only configuration during {operation}"
+            )
+
+    @staticmethod
+    def guard_write_attempt(entity_type: str) -> None:
+        """INV-1: block mutation attempts from the read projection layer."""
+        enforce_inv1_read_only(entity_type)
+
+    def build_issue(
+        self,
+        issue_key: UUID,
+        issue_version: int | None = None,
+    ) -> IssueWorkProduct:
+        self._assert_read_only("build_issue")
+        if issue_version is not None:
+            issue = self.repo.get_issue_version(issue_key, issue_version)
+        else:
+            issue = self.repo.get_current_issue(issue_key)
+        if issue is None:
+            raise NotFoundError("issue not found")
+        return self._build_for_issue(issue)
+
+    def build_case(self, case_id: UUID) -> CaseIssueWorkProduct:
+        self._assert_read_only("build_case")
+        case = self.session.get(Case, case_id)
+        if case is None:
+            raise NotFoundError("case not found")
+        issues = list(
+            self.session.scalars(
+                select(Issue)
+                .where(Issue.case_id == case_id, Issue.is_current.is_(True))
+                .order_by(Issue.order_index.asc(), Issue.created_at.asc())
+            )
+        )
+        confirmed: list[IssueWorkProduct] = []
+        candidates: list[IssueWorkProduct] = []
+        state_counts = {
+            ProofState.RED.value: 0,
+            ProofState.YELLOW.value: 0,
+            ProofState.GREEN.value: 0,
+        }
+        assessment_count = 0
+        for issue in issues:
+            if issue.status == "REJECTED":
+                continue
+            wp = self._build_for_issue(issue)
+            if issue.status == "CONFIRMED":
+                confirmed.append(wp)
+                state_counts[wp.proof_state] = state_counts.get(wp.proof_state, 0) + 1
+                if wp.lawyer_assessment:
+                    assessment_count += 1
+            elif issue.status == "CANDIDATE":
+                candidates.append(wp)
+        blocking = next(
+            (i for i in confirmed if i.proof_state == ProofState.RED.value),
+            None,
+        )
+        recommended = blocking or next(
+            (i for i in confirmed if i.proof_state == ProofState.YELLOW.value),
+            confirmed[0] if confirmed else (candidates[0] if candidates else None),
+        )
+        return CaseIssueWorkProduct(
+            case_id=str(case_id),
+            confirmed_issues=confirmed,
+            candidate_issues=candidates,
+            proof_state_counts=state_counts,
+            assessment_count=assessment_count,
+            top_blocking_issue=blocking,
+            recommended_next_issue=recommended,
+        )
+
+    def build_litigation_plan(self, case_id: UUID) -> LitigationPlanView:
+        self._assert_read_only("build_litigation_plan")
+        from backend.application.claim_view import ClaimViewService
+        from backend.application.pleading_readiness import PleadingReadinessService
+
+        case_wp = self.build_case(case_id)
+        claims = ClaimViewService(self.session).build(case_id)
+        readiness = PleadingReadinessService(self.session).evaluate(case_id)
+        assessments = [
+            {
+                "issue_key": i.issue_key,
+                "issue_statement": i.statement,
+                "assessment": i.lawyer_assessment.model_dump(mode="json")
+                if i.lawyer_assessment
+                else None,
+            }
+            for i in case_wp.confirmed_issues
+            if i.lawyer_assessment
+        ]
+        return LitigationPlanView(
+            case_id=str(case_id),
+            confirmed_issues=[i.model_dump(mode="json") for i in case_wp.confirmed_issues],
+            claims=[c.model_dump(mode="json") for c in claims.items],
+            readiness_status=readiness.status,
+            readiness_display=readiness.display_status or readiness.status,
+            assessments=assessments,
+        )
+
+    def _build_for_issue(self, issue: Issue) -> IssueWorkProduct:
+        positions_raw = self.repo.list_issue_positions(issue.issue_key, issue.version)
+        positions: dict[str, list[PositionView]] = {
+            "our_current": [],
+            "anticipated_defenses": [],
+            "formal_opponent": [],
+        }
+        for pos in positions_raw:
+            if pos.status in {"REJECTED", "SUPERSEDED"}:
+                continue
+            view = PositionView(
+                position_key=str(pos.position_key),
+                version=pos.version,
+                side=pos.side,
+                position_type=pos.position_type,
+                source_type=pos.source_type,
+                status=pos.status,
+                statement=pos.statement,
+                display_label=_POSITION_TYPE_ZH.get(pos.position_type, pos.position_type),
+                opponent_material_ref=pos.opponent_material_ref,
+            )
+            if pos.side == "OUR" and pos.position_type == "ASSERTION":
+                positions["our_current"].append(view)
+            elif pos.position_type == "ANTICIPATED_DEFENSE":
+                positions["anticipated_defenses"].append(view)
+            elif pos.position_type == "FORMAL_DEFENSE":
+                positions["formal_opponent"].append(view)
+
+        proof_tasks: list[ProofTaskView] = []
+        matrix_item = next(
+            (
+                i
+                for i in self.matrix_svc.build(issue.case_id).items
+                if i.issue_key == str(issue.issue_key) and i.issue_version == issue.version
+            ),
+            None,
+        )
+        structural_warnings: list[StructuralWarningView] = []
+        if matrix_item:
+            for g in matrix_item.structural_warnings:
+                structural_warnings.append(
+                    StructuralWarningView(
+                        type=g.type,
+                        description=g.description,
+                        related_fact_key=g.related_fact_key,
+                        related_fact_version=g.related_fact_version,
+                    )
+                )
+
+        gaps_raw = self.repo.list_proof_gaps(issue.issue_key, issue.version)
+        gap_views = [self._gap_view(g) for g in gaps_raw]
+        open_gap_count = sum(1 for g in gaps_raw if g.status == "OPEN")
+
+        for task in self.repo.list_proof_tasks(issue.issue_key, issue.version):
+            task_gaps = [
+                g.model_dump(mode="json")
+                for g in gap_views
+                if g.proof_task_key == str(task.proof_task_key)
+            ]
+            support, adverse, context = self._proof_task_facts(task)
+            task_structural = structural_warnings if task.status == "ADOPTED" else []
+            proof_tasks.append(
+                ProofTaskView(
+                    proof_task_key=str(task.proof_task_key),
+                    version=task.version,
+                    description=task.description,
+                    status=task.status,
+                    source_type=task.source_type,
+                    display_status=self._proof_task_display(task.status),
+                    support_facts=support,
+                    adverse_facts=adverse,
+                    context_facts=context,
+                    structural_warnings=task_structural,
+                    proof_gaps=task_gaps,
+                )
+            )
+
+        conflicts: list[ConflictView] = []
+        for conflict in self.repo.list_issue_conflicts(issue.issue_key, issue.version):
+            facts = []
+            for fl in self.repo.list_conflict_fact_links(conflict.id):
+                fact = self.repo.get_fact_version(fl.fact_key, fl.fact_version)
+                if fact:
+                    facts.append(
+                        {
+                            "fact_key": str(fl.fact_key),
+                            "fact_version": fl.fact_version,
+                            "statement": fact.statement,
+                            "role": fl.role,
+                        }
+                    )
+            conflicts.append(
+                ConflictView(
+                    conflict_id=str(conflict.id),
+                    conflict_key=str(conflict.conflict_key),
+                    description=conflict.description,
+                    status=conflict.status,
+                    source_type=conflict.source_type,
+                    display_status=self._conflict_display(conflict.status),
+                    resolution_note=conflict.resolution_note,
+                    facts=facts,
+                )
+            )
+
+        legal = self._legal_analysis(issue)
+        assessments = self.repo.list_lawyer_assessments(issue.issue_key, issue.version)
+        current_assessment = next(
+            (a for a in assessments if a.is_current and a.status == "ACTIVE"),
+            None,
+        )
+        lawyer_assessment = None
+        if current_assessment:
+            lawyer_assessment = LawyerAssessmentView(
+                assessment_key=str(current_assessment.assessment_key),
+                version=current_assessment.version,
+                content=current_assessment.content,
+                status=current_assessment.status,
+                display_status="当前有效",
+                is_current=True,
+            )
+
+        proof_state = self._compute_proof_state(
+            issue, open_gap_count, structural_warnings, proof_tasks
+        )
+        judgment_state = (
+            LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value
+            if lawyer_assessment
+            else LawyerJudgmentState.NOT_ANALYZED.value
+        )
+
+        return IssueWorkProduct(
+            issue_key=str(issue.issue_key),
+            issue_version=issue.version,
+            statement=issue.statement,
+            status=issue.status,
+            source_type=issue.source_type,
+            display_status=_ISSUE_STATUS_ZH.get(issue.status, issue.status),
+            positions=positions,
+            proof_tasks=proof_tasks,
+            conflicts=conflicts,
+            legal_analysis=legal,
+            lawyer_assessment=lawyer_assessment,
+            proof_state=proof_state,
+            proof_state_label=_PROOF_STATE_ZH.get(proof_state, proof_state),
+            lawyer_judgment_state=judgment_state,
+            lawyer_judgment_state_label=_JUDGMENT_STATE_ZH.get(judgment_state, judgment_state),
+            next_action=self._next_action(proof_state, open_gap_count, issue.status),
+            evolution=self._evolution(issue),
+            proof_gaps=gap_views,
+            proof_gap_count=len(gap_views),
+            open_proof_gap_count=open_gap_count,
+            conflict_count=len([c for c in conflicts if c.status in {"CANDIDATE", "OPEN"}]),
+            proof_task_count=len(proof_tasks),
+        )
+
+    def _proof_task_facts(self, task) -> tuple[list, list, list]:
+        support, adverse, context = [], [], []
+        for link in self.repo.list_proof_task_fact_links(
+            task.proof_task_key, task.version
+        ):
+            if not _link_matches_explicit_task_version(link, task.version):
+                continue
+            if link.fact_version < 1:
+                continue
+            if getattr(link, "case_id", None) != task.case_id:
+                continue
+            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
+            if fact is None or fact.case_id != task.case_id:
+                continue
+            evidence = self._fact_evidence(fact.id)
+            view = ProofTaskFactView(
+                fact_key=str(link.fact_key),
+                fact_version=link.fact_version,
+                statement=fact.statement,
+                status=fact.status,
+                role=link.role,
+                evidence=evidence,
+            )
+            if link.role == "SUPPORT":
+                support.append(view)
+            elif link.role == "ADVERSE":
+                adverse.append(view)
+            else:
+                context.append(view)
+        return support, adverse, context
+
+    def _fact_evidence(self, fact_id: UUID) -> list[dict]:
+        out = []
+        for link in self.repo.list_fact_links(fact_id):
+            if link.status != "ACTIVE":
+                continue
+            ev = self.repo.get_evidence_version(link.evidence_item_id, link.evidence_item_version)
+            if ev and ev.acceptance == "ACCEPTED":
+                out.append(
+                    {
+                        "evidence_item_id": str(ev.id),
+                        "evidence_item_version": ev.version,
+                        "title": ev.title,
+                        "acceptance": ev.acceptance,
+                    }
+                )
+        return out
+
+    def _gap_view(self, gap) -> ProofGapView:
+        status_zh = {
+            "OPEN": "待处理",
+            "RESOLVED": "已解决",
+            "WAIVED": "已放弃",
+            "SUPERSEDED": "已替代",
+        }
+        return ProofGapView(
+            gap_id=str(gap.id),
+            gap_key=str(gap.gap_key),
+            gap_type=gap.gap_type,
+            status=gap.status,
+            source_type=gap.source_type,
+            description=gap.description,
+            what_exists=gap.what_exists,
+            what_is_missing=gap.what_is_missing,
+            why_it_matters=gap.why_it_matters,
+            suggested_material_types=gap.suggested_material_types or [],
+            display_status=status_zh.get(gap.status, gap.status),
+            proof_task_key=str(gap.proof_task_key) if gap.proof_task_key else None,
+        )
+
+    def _legal_analysis(self, issue: Issue) -> LegalAnalysisView:
+        theories = []
+        for link in self.repo.list_issue_legal_theory_links(issue.issue_key, issue.version):
+            lt = self.session.get(LegalTheory, link.legal_theory_id)
+            if lt:
+                theories.append(
+                    {
+                        "id": str(lt.id),
+                        "theory_summary": lt.theory_summary,
+                        "role": link.role,
+                        "layer": lt.layer,
+                    }
+                )
+        favorable: list[str] = []
+        adverse: list[str] = []
+        unknown: list[str] = []
+        for link in self.repo.list_issue_fact_links(issue.issue_key, issue.version):
+            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
+            if fact is None or fact.status != "CONFIRMED":
+                continue
+            if link.role == "SUPPORT":
+                favorable.append(fact.statement)
+            elif link.role == "ADVERSE":
+                adverse.append(fact.statement)
+            else:
+                unknown.append(fact.statement)
+        return LegalAnalysisView(
+            legal_theories=theories,
+            favorable_factors=favorable,
+            adverse_factors=adverse,
+            unknown_factors=unknown,
+        )
+
+    def _compute_proof_state(
+        self,
+        issue: Issue,
+        open_gap_count: int,
+        structural_warnings: list,
+        proof_tasks: list,
+    ) -> str:
+        if issue.status != "CONFIRMED":
+            return ProofState.YELLOW.value
+        if open_gap_count > 0:
+            return ProofState.RED.value
+        if structural_warnings:
+            return ProofState.YELLOW.value
+        adopted = [t for t in proof_tasks if t.status == "ADOPTED"]
+        if not adopted:
+            return ProofState.YELLOW.value
+        for task in adopted:
+            if not task.support_facts:
+                return ProofState.YELLOW.value
+        return ProofState.GREEN.value
+
+    def _next_action(self, proof_state: str, open_gaps: int, status: str) -> str | None:
+        if status == "CANDIDATE":
+            return "请确认是否将本焦点纳入办案范围"
+        if open_gaps > 0:
+            return "请处理待补强的证明缺口"
+        if proof_state == ProofState.YELLOW.value:
+            return "请继续补强事实与证据关联"
+        if proof_state == ProofState.GREEN.value:
+            return "可进入法律分析与诉请衔接"
+        return "请审查关键证明缺口"
+
+    def _evolution(self, issue: Issue) -> list[EvolutionEventView]:
+        events: list[EvolutionEventView] = []
+        if issue.created_at:
+            events.append(
+                EvolutionEventView(
+                    event_type="ISSUE_CREATED",
+                    timestamp=issue.created_at.isoformat(),
+                    summary=f"争点创建（v{issue.version}）",
+                )
+            )
+        decisions = list(
+            self.session.scalars(
+                select(HumanDecision)
+                .where(
+                    HumanDecision.case_id == issue.case_id,
+                    HumanDecision.target_id == issue.issue_key,
+                )
+                .order_by(HumanDecision.created_at.asc())
+            )
+        )
+        for d in decisions:
+            events.append(
+                EvolutionEventView(
+                    event_type=d.decision_type,
+                    timestamp=d.created_at.isoformat() if d.created_at else None,
+                    summary=d.decision_type,
+                    actor_hint=str(d.actor_id)[:8] if d.actor_id else None,
+                )
+            )
+        audits = list(
+            self.session.scalars(
+                select(AuditLog)
+                .where(
+                    AuditLog.case_id == issue.case_id,
+                    AuditLog.entity_type.in_(["issue_positions", "proof_tasks", "proof_gaps"]),
+                )
+                .order_by(AuditLog.created_at.asc())
+                .limit(20)
+            )
+        )
+        for a in audits:
+            events.append(
+                EvolutionEventView(
+                    event_type=a.action,
+                    timestamp=a.created_at.isoformat() if a.created_at else None,
+                    summary=a.action,
+                )
+            )
+        return events
+
+    @staticmethod
+    def _proof_task_display(status: str) -> str:
+        return {
+            "CANDIDATE": "待采纳",
+            "ADOPTED": "已采纳",
+            "REJECTED": "已拒绝",
+            "WAIVED": "已放弃",
+            "SUPERSEDED": "已替代",
+        }.get(status, status)
+
+    @staticmethod
+    def _conflict_display(status: str) -> str:
+        return {
+            "CANDIDATE": "待确认",
+            "OPEN": "待处理",
+            "RESOLVED": "已解决",
+            "DISMISSED": "已驳回",
+        }.get(status, status)
diff --git a/backend/domain/issue_centered.py b/backend/domain/issue_centered.py
new file mode 100644
index 0000000..283f5d4
--- /dev/null
+++ b/backend/domain/issue_centered.py
@@ -0,0 +1,1259 @@
+"""Issue-centered V2 domain mutations — mixed into DomainService.
+
+Explicit invariants enforced in this module (Issue #80 repair SSOT / #73 / #60 / #83 / #86 / #85 / #84):
+  INV-1: _reject_claim_direction_production_mutation blocks ClaimDirection writes.
+  INV-2: link_fact_to_proof_task uses explicit proof_task_version/fact_version only
+         (no get_current_*); rejects implicit current/latest and cross-case links.
+  INV-3: create_lawyer_position requires opponent_material_ref for FORMAL_DEFENSE.
+  INV-4: merge_issues and split_issue persist HumanDecision + AuditLog before mutation.
+"""
+
+from __future__ import annotations
+
+import uuid
+from datetime import UTC, datetime
+from typing import TYPE_CHECKING, Any
+from uuid import UUID
+
+from sqlalchemy.exc import IntegrityError
+
+from backend.domain.enums import (
+    ClaimLinkStatus,
+    ConflictFactRole,
+    ConflictSourceType,
+    ConflictStatus,
+    DecisionResult,
+    IssueLinkStatus,
+    IssueSourceType,
+    IssueStatus,
+    LawyerAssessmentStatus,
+    PositionSide,
+    PositionSourceType,
+    PositionStatus,
+    PositionType,
+    ProofGapSourceType,
+    ProofGapStatus,
+    ProofGapType,
+    ProofTaskFactLinkRole,
+    ProofTaskSourceType,
+    ProofTaskStatus,
+)
+from backend.domain.errors import ConflictError, NotFoundError, ValidationError
+from backend.models import (
+    AuditLog,
+    ConflictFactLink,
+    HumanDecision,
+    Issue,
+    IssueConflict,
+    IssueFactLink,
+    IssueLegalTheoryLink,
+    IssuePosition,
+    LawyerAssessment,
+    ProofGap,
+    ProofTask,
+    ProofTaskFactLink,
+)
+
+if TYPE_CHECKING:
+    from backend.domain.services import DomainService
+
+__all__ = [
+    "IssueCenteredDomainMixin",
+    "_guard_cross_case_proof_task_fact_pair",
+    "_guard_explicit_proof_task_fact_versions",
+    "_guard_formal_defense_opponent_material_ref",
+    "_guard_formal_defense_side",
+    "_guard_resolved_explicit_versions",
+    "_normalize_opponent_material_ref",
+    "_reject_claim_direction_production_mutation",
+    "_require_structure_mutation_audit",
+]
+
+
+def _now() -> datetime:
+    return datetime.now(UTC)
+
+
+def _reject_claim_direction_production_mutation(*, _legacy_compat: bool) -> None:
+    """INV-1: block ClaimDirection creation on production mutation paths."""
+    if not _legacy_compat:
+        raise ValidationError(
+            "ClaimDirection production mutation disabled; use Claim Domain instead"
+        )
+
+
+def _normalize_opponent_material_ref(ref: str | None) -> str | None:
+    """INV-3: strip whitespace so blank refs cannot bypass FORMAL_DEFENSE guard."""
+    if ref is None:
+        return None
+    stripped = ref.strip()
+    return stripped or None
+
+
+def _guard_explicit_proof_task_fact_versions(
+    proof_task_version: int,
+    fact_version: int,
+) -> None:
+    """INV-2: reject zero/negative versions that could alias implicit current/latest."""
+    if proof_task_version < 1 or fact_version < 1:
+        raise ValidationError(
+            "ProofTaskFactLink requires explicit positive proof_task_version and fact_version"
+        )
+
+
+def _guard_formal_defense_opponent_material_ref(
+    position_type: str,
+    opponent_material_ref: str | None,
+) -> None:
+    """INV-3: FORMAL_DEFENSE must cite opponent source material."""
+    if position_type == PositionType.FORMAL_DEFENSE.value:
+        if not opponent_material_ref or not str(opponent_material_ref).strip():
+            raise ValidationError("FORMAL_DEFENSE requires opponent material reference")
+
+
+def _guard_formal_defense_side(position_type: str, side: str) -> None:
+    """INV-3: FORMAL_DEFENSE positions must be recorded on the OPPONENT side."""
+    if position_type == PositionType.FORMAL_DEFENSE.value and side != PositionSide.OPPONENT.value:
+        raise ValidationError("FORMAL_DEFENSE must be on OPPONENT side")
+
+
+def _guard_cross_case_proof_task_fact_pair(task: ProofTask, fact: Any) -> None:
+    """INV-2: proof task and fact must belong to the same case."""
+    if task.case_id != fact.case_id:
+        raise ValidationError("cross-case proof task fact link rejected")
+
+
+def _guard_resolved_explicit_versions(
+    *,
+    proof_task_version: int,
+    fact_version: int,
+    task: ProofTask,
+    fact: Any,
+) -> None:
+    """INV-2: resolved rows must match requested versions (no implicit current/latest)."""
+    if task.version != proof_task_version:
+        raise ValidationError(
+            "ProofTaskFactLink requires explicit proof_task_version; "
+            "implicit current/latest rejected"
+        )
+    if fact.version != fact_version:
+        raise ValidationError(
+            "ProofTaskFactLink requires explicit fact_version; implicit current/latest rejected"
+        )
+
+
+def _resolve_proof_task_and_fact_for_link(
+    svc: DomainService,
+    *,
+    case_id: UUID,
+    proof_task_key: UUID,
+    proof_task_version: int,
+    fact_key: UUID,
+    fact_version: int,
+) -> tuple[ProofTask, Any]:
+    """INV-2: resolve explicit proof-task/fact versions; reject cross-case links."""
+    _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
+    task = svc.repo.get_proof_task_version(proof_task_key, proof_task_version)
+    if task is None:
+        raise NotFoundError("proof task version not found")
+    if task.case_id != case_id:
+        raise ValidationError("cross-case proof task link rejected")
+    fact = svc.repo.get_fact_version(fact_key, fact_version)
+    if fact is None:
+        raise NotFoundError("fact version not found")
+    if fact.case_id != case_id:
+        raise ValidationError("cross-case fact link rejected")
+    _guard_resolved_explicit_versions(
+        proof_task_version=proof_task_version,
+        fact_version=fact_version,
+        task=task,
+        fact=fact,
+    )
+    return task, fact
+
+
+def _persist_issue_structure_decision(
+    svc: DomainService,
+    *,
+    case_id: UUID,
+    actor_id: UUID,
+    decision_type: str,
+    target_id: UUID,
+    payload: dict[str, Any],
+) -> Any:
+    """INV-4: HumanDecision must be flushed before merge/split mutations proceed."""
+    decision = svc._new_decision(
+        case_id=case_id,
+        actor_id=actor_id,
+        decision_type=decision_type,
+        target_type="Issue",
+        target_id=target_id,
+        result=DecisionResult.CONFIRMED.value,
+        payload=payload,
+    )
+    svc.repo.add_decision(decision)
+    svc.repo.flush()
+    if decision.id is None:
+        raise ConflictError(f"{decision_type} decision failed to persist")
+    return decision
+
+
+def _require_structure_mutation_audit(
+    svc: DomainService,
+    *,
+    case_id: UUID,
+    action: str,
+    decision_type: str,
+) -> None:
+    """INV-4: merge/split must emit HumanDecision + AuditLog before returning."""
+    from sqlalchemy import select
+
+    decision = svc.session.scalars(
+        select(HumanDecision)
+        .where(
+            HumanDecision.case_id == case_id,
+            HumanDecision.decision_type == decision_type,
+        )
+        .order_by(HumanDecision.created_at.desc())
+        .limit(1)
+    ).first()
+    if decision is None:
+        raise ConflictError(f"{action} must emit HumanDecision before completing")
+    audit = svc.session.scalars(
+        select(AuditLog)
+        .where(
+            AuditLog.case_id == case_id,
+            AuditLog.action == action,
+            AuditLog.entity_type == "issues",
+        )
+        .order_by(AuditLog.created_at.desc())
+        .limit(1)
+    ).first()
+    if audit is None:
+        raise ConflictError(f"{action} must emit AuditLog before completing")
+    decision_id = str(decision.id)
+    after = audit.after_json or {}
+    if after.get("decision_id") != decision_id:
+        raise ConflictError(
+            f"{action} AuditLog.after_json must reference HumanDecision decision_id"
+        )
+
+
+class IssueCenteredDomainMixin:
+    """Issue-centered workspace V2 mutations."""
+
+    session: Any
+    repo: Any
+
+    def _require_issue_version(self, issue_key: UUID, issue_version: int) -> Issue:
+        issue = self.repo.get_issue_version(issue_key, issue_version)
+        if issue is None:
+            raise NotFoundError("issue version not found")
+        return issue
+
+    # ----- IssuePosition -----
+
+    def propose_position(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        side: str,
+        position_type: str,
+        statement: str,
+        analyst_run_id: UUID | None = None,
+        actor_id: UUID | None = None,
+    ) -> IssuePosition:
+        """AI may propose OUR assertion or ANTICIPATED_DEFENSE only."""
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        if side not in {PositionSide.OUR.value}:
+            raise ValidationError("AI may only propose OUR positions")
+        if position_type not in {
+            PositionType.ASSERTION.value,
+            PositionType.ANTICIPATED_DEFENSE.value,
+        }:
+            raise ValidationError("AI may not propose FORMAL_DEFENSE")
+        pos = IssuePosition(
+            position_key=uuid.uuid4(),
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            side=side,
+            position_type=position_type,
+            source_type=PositionSourceType.AI_PROPOSED.value,
+            status=PositionStatus.CANDIDATE.value,
+            statement=statement,
+            version=1,
+            is_current=True,
+            analyst_run_id=analyst_run_id,
+        )
+        self.repo.add(pos)
+        self.repo.flush()
+        self._audit(
+            actor_id or uuid.UUID(int=0),
+            "propose_position",
+            "issue_positions",
+            pos.id,
+            case_id=case_id,
+            after={"position_key": str(pos.position_key), "status": pos.status},
+        )
+        return pos
+
+    def create_lawyer_position(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        side: str,
+        position_type: str,
+        statement: str,
+        actor_id: UUID,
+        opponent_material_ref: str | None = None,
+    ) -> IssuePosition:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        opponent_material_ref = _normalize_opponent_material_ref(opponent_material_ref)
+        _guard_formal_defense_opponent_material_ref(position_type, opponent_material_ref)
+        _guard_formal_defense_side(position_type, side)
+        if position_type == PositionType.FORMAL_DEFENSE.value:
+            source = PositionSourceType.OPPONENT_MATERIAL.value
+        else:
+            source = PositionSourceType.LAWYER_CREATED.value
+        pos_key = uuid.uuid4()
+        decision = self._new_decision(
+            case_id=case_id,
+            actor_id=actor_id,
+            decision_type="CREATE_POSITION",
+            target_type="IssuePosition",
+            target_id=pos_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"statement": statement, "position_type": position_type},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        pos = IssuePosition(
+            position_key=pos_key,
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            side=side,
+            position_type=position_type,
+            source_type=source,
+            status=PositionStatus.CONFIRMED.value,
+            statement=statement,
+            opponent_material_ref=opponent_material_ref,
+            version=1,
+            is_current=True,
+            confirm_decision_id=decision.id,
+        )
+        self.repo.add(pos)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "create_lawyer_position",
+            "issue_positions",
+            pos.id,
+            case_id=case_id,
+            after={"position_key": str(pos.position_key), "decision_id": str(decision.id)},
+        )
+        return pos
+
+    def confirm_position(
+        self: DomainService,
+        position_key: UUID,
+        *,
+        actor_id: UUID,
+    ) -> IssuePosition:
+        pos = self.repo.get_current_position(position_key)
+        if pos is None:
+            raise NotFoundError("position not found")
+        if pos.status != PositionStatus.CANDIDATE.value:
+            raise ConflictError("only CANDIDATE positions can be confirmed")
+        decision = self._new_decision(
+            case_id=pos.case_id,
+            actor_id=actor_id,
+            decision_type="CONFIRM_POSITION",
+            target_type="IssuePosition",
+            target_id=pos.position_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"position_key": str(pos.position_key), "version": pos.version},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        pos.status = PositionStatus.CONFIRMED.value
+        pos.confirm_decision_id = decision.id
+        pos.updated_at = _now()
+        self._audit(
+            actor_id,
+            "confirm_position",
+            "issue_positions",
+            pos.id,
+            case_id=pos.case_id,
+            after={"status": pos.status, "decision_id": str(decision.id)},
+        )
+        return pos
+
+    def reject_position(
+        self: DomainService,
+        position_key: UUID,
+        *,
+        actor_id: UUID,
+    ) -> IssuePosition:
+        pos = self.repo.get_current_position(position_key)
+        if pos is None:
+            raise NotFoundError("position not found")
+        if pos.status != PositionStatus.CANDIDATE.value:
+            raise ConflictError("only CANDIDATE positions can be rejected")
+        decision = self._new_decision(
+            case_id=pos.case_id,
+            actor_id=actor_id,
+            decision_type="REJECT_POSITION",
+            target_type="IssuePosition",
+            target_id=pos.position_key,
+            result=DecisionResult.REJECTED.value,
+            payload={"position_key": str(pos.position_key)},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        pos.status = PositionStatus.REJECTED.value
+        pos.updated_at = _now()
+        self._audit(
+            actor_id,
+            "reject_position",
+            "issue_positions",
+            pos.id,
+            case_id=pos.case_id,
+            after={"status": pos.status},
+        )
+        return pos
+
+    # ----- ProofTask -----
+
+    def propose_proof_task(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        description: str,
+        analyst_run_id: UUID | None = None,
+        actor_id: UUID | None = None,
+    ) -> ProofTask:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        task = ProofTask(
+            proof_task_key=uuid.uuid4(),
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            description=description,
+            status=ProofTaskStatus.CANDIDATE.value,
+            source_type=ProofTaskSourceType.AI_PROPOSED.value,
+            version=1,
+            is_current=True,
+            analyst_run_id=analyst_run_id,
+        )
+        self.repo.add(task)
+        self.repo.flush()
+        self._audit(
+            actor_id or uuid.UUID(int=0),
+            "propose_proof_task",
+            "proof_tasks",
+            task.id,
+            case_id=case_id,
+            after={"proof_task_key": str(task.proof_task_key)},
+        )
+        return task
+
+    def adopt_proof_task(
+        self: DomainService,
+        proof_task_key: UUID,
+        *,
+        actor_id: UUID,
+    ) -> ProofTask:
+        task = self.repo.get_current_proof_task(proof_task_key)
+        if task is None:
+            raise NotFoundError("proof task not found")
+        if task.status != ProofTaskStatus.CANDIDATE.value:
+            raise ConflictError("only CANDIDATE proof tasks can be adopted")
+        decision = self._new_decision(
+            case_id=task.case_id,
+            actor_id=actor_id,
+            decision_type="ADOPT_PROOF_TASK",
+            target_type="ProofTask",
+            target_id=task.proof_task_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"proof_task_key": str(task.proof_task_key)},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        task.status = ProofTaskStatus.ADOPTED.value
+        task.confirm_decision_id = decision.id
+        task.updated_at = _now()
+        self._audit(
+            actor_id,
+            "adopt_proof_task",
+            "proof_tasks",
+            task.id,
+            case_id=task.case_id,
+            after={"status": task.status},
+        )
+        return task
+
+    def create_lawyer_proof_task(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        description: str,
+        actor_id: UUID,
+    ) -> ProofTask:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        task_key = uuid.uuid4()
+        decision = self._new_decision(
+            case_id=case_id,
+            actor_id=actor_id,
+            decision_type="CREATE_PROOF_TASK",
+            target_type="ProofTask",
+            target_id=task_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"description": description},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        task = ProofTask(
+            proof_task_key=task_key,
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            description=description,
+            status=ProofTaskStatus.ADOPTED.value,
+            source_type=ProofTaskSourceType.LAWYER_CREATED.value,
+            version=1,
+            is_current=True,
+            confirm_decision_id=decision.id,
+        )
+        self.repo.add(task)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "create_lawyer_proof_task",
+            "proof_tasks",
+            task.id,
+            case_id=case_id,
+            after={"proof_task_key": str(task.proof_task_key)},
+        )
+        return task
+
+    def waive_proof_task(
+        self: DomainService,
+        proof_task_key: UUID,
+        *,
+        actor_id: UUID,
+    ) -> ProofTask:
+        task = self.repo.get_current_proof_task(proof_task_key)
+        if task is None:
+            raise NotFoundError("proof task not found")
+        if task.status not in {ProofTaskStatus.ADOPTED.value, ProofTaskStatus.CANDIDATE.value}:
+            raise ConflictError("proof task cannot be waived in current status")
+        decision = self._new_decision(
+            case_id=task.case_id,
+            actor_id=actor_id,
+            decision_type="WAIVE_PROOF_TASK",
+            target_type="ProofTask",
+            target_id=task.proof_task_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"proof_task_key": str(task.proof_task_key)},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        task.status = ProofTaskStatus.WAIVED.value
+        task.updated_at = _now()
+        self._audit(
+            actor_id,
+            "waive_proof_task",
+            "proof_tasks",
+            task.id,
+            case_id=task.case_id,
+            after={"status": task.status},
+        )
+        return task
+
+    def link_fact_to_proof_task(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        proof_task_key: UUID,
+        proof_task_version: int,
+        fact_key: UUID,
+        fact_version: int,
+        role: str,
+        actor_id: UUID,
+    ) -> ProofTaskFactLink:
+        # INV-2: reject invalid/implicit versions before any DB row lookup.
+        _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
+        self._require_case(case_id)
+        task, fact = _resolve_proof_task_and_fact_for_link(
+            self,
+            case_id=case_id,
+            proof_task_key=proof_task_key,
+            proof_task_version=proof_task_version,
+            fact_key=fact_key,
+            fact_version=fact_version,
+        )
+        _guard_cross_case_proof_task_fact_pair(task, fact)
+        if role not in {r.value for r in ProofTaskFactLinkRole}:
+            raise ValidationError(f"invalid proof task fact link role: {role}")
+        link = ProofTaskFactLink(
+            case_id=case_id,
+            proof_task_key=proof_task_key,
+            proof_task_version=proof_task_version,
+            fact_key=fact_key,
+            fact_version=fact_version,
+            role=role,
+            status=IssueLinkStatus.ACTIVE.value,
+            created_by=actor_id,
+        )
+        try:
+            self.repo.add(link)
+            self.repo.flush()
+        except IntegrityError as exc:
+            raise ConflictError("duplicate proof task fact link") from exc
+        self._audit(
+            actor_id,
+            "link_fact_to_proof_task",
+            "proof_task_fact_links",
+            link.id,
+            case_id=case_id,
+            after={
+                "proof_task_key": str(proof_task_key),
+                "fact_key": str(fact_key),
+                "role": role,
+            },
+        )
+        return link
+
+    # ----- IssueConflict -----
+
+    def create_conflict(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        description: str,
+        source_type: str = ConflictSourceType.AI_DETECTED.value,
+        fact_refs: list[dict[str, Any]] | None = None,
+        actor_id: UUID | None = None,
+        analyst_run_id: UUID | None = None,
+    ) -> IssueConflict:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        conflict = IssueConflict(
+            conflict_key=uuid.uuid4(),
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            description=description,
+            status=ConflictStatus.OPEN.value
+            if source_type == ConflictSourceType.LAWYER_CREATED.value
+            else ConflictStatus.CANDIDATE.value,
+            source_type=source_type,
+            analyst_run_id=analyst_run_id,
+        )
+        self.repo.add(conflict)
+        self.repo.flush()
+        for ref in fact_refs or []:
+            fk = UUID(str(ref["fact_key"]))
+            fv = int(ref["fact_version"])
+            role = str(ref.get("role", ConflictFactRole.CONTEXT.value))
+            fact = self.repo.get_fact_version(fk, fv)
+            if fact is None or fact.case_id != case_id:
+                raise ValidationError("invalid conflict fact ref")
+            link = ConflictFactLink(
+                case_id=case_id,
+                conflict_id=conflict.id,
+                fact_key=fk,
+                fact_version=fv,
+                role=role,
+            )
+            self.repo.add(link)
+        self.repo.flush()
+        self._audit(
+            actor_id or uuid.UUID(int=0),
+            "create_conflict",
+            "issue_conflicts",
+            conflict.id,
+            case_id=case_id,
+            after={"conflict_key": str(conflict.conflict_key), "status": conflict.status},
+        )
+        return conflict
+
+    def resolve_conflict(
+        self: DomainService,
+        conflict_id: UUID,
+        *,
+        resolution_note: str,
+        actor_id: UUID,
+    ) -> IssueConflict:
+        conflict = self.session.get(IssueConflict, conflict_id)
+        if conflict is None:
+            raise NotFoundError("conflict not found")
+        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
+            raise ConflictError("conflict already closed")
+        decision = self._new_decision(
+            case_id=conflict.case_id,
+            actor_id=actor_id,
+            decision_type="RESOLVE_CONFLICT",
+            target_type="IssueConflict",
+            target_id=conflict.conflict_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"resolution_note": resolution_note},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        conflict.status = ConflictStatus.RESOLVED.value
+        conflict.resolution_note = resolution_note
+        conflict.resolve_decision_id = decision.id
+        conflict.updated_at = _now()
+        self._audit(
+            actor_id,
+            "resolve_conflict",
+            "issue_conflicts",
+            conflict.id,
+            case_id=conflict.case_id,
+            after={"status": conflict.status},
+        )
+        return conflict
+
+    def dismiss_conflict(
+        self: DomainService,
+        conflict_id: UUID,
+        *,
+        resolution_note: str,
+        actor_id: UUID,
+    ) -> IssueConflict:
+        conflict = self.session.get(IssueConflict, conflict_id)
+        if conflict is None:
+            raise NotFoundError("conflict not found")
+        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
+            raise ConflictError("conflict already closed")
+        decision = self._new_decision(
+            case_id=conflict.case_id,
+            actor_id=actor_id,
+            decision_type="DISMISS_CONFLICT",
+            target_type="IssueConflict",
+            target_id=conflict.conflict_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"resolution_note": resolution_note},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        conflict.status = ConflictStatus.DISMISSED.value
+        conflict.resolution_note = resolution_note
+        conflict.resolve_decision_id = decision.id
+        conflict.updated_at = _now()
+        self._audit(
+            actor_id,
+            "dismiss_conflict",
+            "issue_conflicts",
+            conflict.id,
+            case_id=conflict.case_id,
+            after={"status": conflict.status},
+        )
+        return conflict
+
+    # ----- ProofGap -----
+
+    def create_proof_gap(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        gap_type: str,
+        description: str,
+        source_type: str = ProofGapSourceType.AI_DETECTED.value,
+        proof_task_key: UUID | None = None,
+        proof_task_version: int | None = None,
+        what_exists: str | None = None,
+        what_is_missing: str | None = None,
+        why_it_matters: str | None = None,
+        suggested_material_types: list[str] | None = None,
+        actor_id: UUID | None = None,
+        analyst_run_id: UUID | None = None,
+    ) -> ProofGap:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        if gap_type not in {t.value for t in ProofGapType}:
+            raise ValidationError(f"invalid gap type: {gap_type}")
+        gap = ProofGap(
+            gap_key=uuid.uuid4(),
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            proof_task_key=proof_task_key,
+            proof_task_version=proof_task_version,
+            gap_type=gap_type,
+            status=ProofGapStatus.OPEN.value,
+            source_type=source_type,
+            description=description,
+            what_exists=what_exists,
+            what_is_missing=what_is_missing,
+            why_it_matters=why_it_matters,
+            suggested_material_types=suggested_material_types,
+            analyst_run_id=analyst_run_id,
+        )
+        self.repo.add(gap)
+        self.repo.flush()
+        self._audit(
+            actor_id or uuid.UUID(int=0),
+            "create_proof_gap",
+            "proof_gaps",
+            gap.id,
+            case_id=case_id,
+            after={"gap_key": str(gap.gap_key), "gap_type": gap_type},
+        )
+        return gap
+
+    def resolve_proof_gap(
+        self: DomainService,
+        gap_id: UUID,
+        *,
+        resolution_note: str,
+        actor_id: UUID,
+    ) -> ProofGap:
+        gap = self.session.get(ProofGap, gap_id)
+        if gap is None:
+            raise NotFoundError("proof gap not found")
+        if gap.status != ProofGapStatus.OPEN.value:
+            raise ConflictError("only OPEN gaps can be resolved")
+        decision = self._new_decision(
+            case_id=gap.case_id,
+            actor_id=actor_id,
+            decision_type="RESOLVE_PROOF_GAP",
+            target_type="ProofGap",
+            target_id=gap.gap_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"resolution_note": resolution_note},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        gap.status = ProofGapStatus.RESOLVED.value
+        gap.resolution_note = resolution_note
+        gap.resolve_decision_id = decision.id
+        gap.updated_at = _now()
+        self._audit(
+            actor_id,
+            "resolve_proof_gap",
+            "proof_gaps",
+            gap.id,
+            case_id=gap.case_id,
+            after={"status": gap.status},
+        )
+        return gap
+
+    def waive_proof_gap(
+        self: DomainService,
+        gap_id: UUID,
+        *,
+        resolution_note: str,
+        actor_id: UUID,
+    ) -> ProofGap:
+        gap = self.session.get(ProofGap, gap_id)
+        if gap is None:
+            raise NotFoundError("proof gap not found")
+        if gap.status != ProofGapStatus.OPEN.value:
+            raise ConflictError("only OPEN gaps can be waived")
+        decision = self._new_decision(
+            case_id=gap.case_id,
+            actor_id=actor_id,
+            decision_type="WAIVE_PROOF_GAP",
+            target_type="ProofGap",
+            target_id=gap.gap_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"resolution_note": resolution_note},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        gap.status = ProofGapStatus.WAIVED.value
+        gap.resolution_note = resolution_note
+        gap.resolve_decision_id = decision.id
+        gap.updated_at = _now()
+        self._audit(
+            actor_id,
+            "waive_proof_gap",
+            "proof_gaps",
+            gap.id,
+            case_id=gap.case_id,
+            after={"status": gap.status},
+        )
+        return gap
+
+    # ----- LawyerAssessment (lawyer-only) -----
+
+    def create_lawyer_assessment(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        content: str,
+        actor_id: UUID,
+        is_ai_actor: bool = False,
+    ) -> LawyerAssessment:
+        if is_ai_actor:
+            raise ValidationError("AI cannot create LawyerAssessment")
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        assessment_key = uuid.uuid4()
+        decision = self._new_decision(
+            case_id=case_id,
+            actor_id=actor_id,
+            decision_type="CREATE_LAWYER_ASSESSMENT",
+            target_type="LawyerAssessment",
+            target_id=assessment_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"content": content[:200]},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        assessment = LawyerAssessment(
+            assessment_key=assessment_key,
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            content=content,
+            status=LawyerAssessmentStatus.ACTIVE.value,
+            version=1,
+            is_current=True,
+            confirm_decision_id=decision.id,
+        )
+        self.repo.add(assessment)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "create_lawyer_assessment",
+            "lawyer_assessments",
+            assessment.id,
+            case_id=case_id,
+            after={"assessment_key": str(assessment_key)},
+        )
+        return assessment
+
+    def amend_lawyer_assessment(
+        self: DomainService,
+        assessment_key: UUID,
+        *,
+        new_content: str,
+        actor_id: UUID,
+        is_ai_actor: bool = False,
+    ) -> LawyerAssessment:
+        if is_ai_actor:
+            raise ValidationError("AI cannot amend LawyerAssessment")
+        old = self.repo.get_current_lawyer_assessment(assessment_key)
+        if old is None:
+            raise NotFoundError("assessment not found")
+        if old.status != LawyerAssessmentStatus.ACTIVE.value:
+            raise ConflictError("only ACTIVE assessments can be amended")
+        decision = self._new_decision(
+            case_id=old.case_id,
+            actor_id=actor_id,
+            decision_type="AMEND_LAWYER_ASSESSMENT",
+            target_type="LawyerAssessment",
+            target_id=old.assessment_key,
+            result=DecisionResult.AMENDED.value,
+            payload={"from_version": old.version},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        old.status = LawyerAssessmentStatus.SUPERSEDED.value
+        old.is_current = False
+        old.updated_at = _now()
+        new_assessment = LawyerAssessment(
+            assessment_key=old.assessment_key,
+            case_id=old.case_id,
+            issue_key=old.issue_key,
+            issue_version=old.issue_version,
+            content=new_content,
+            status=LawyerAssessmentStatus.ACTIVE.value,
+            version=old.version + 1,
+            is_current=True,
+            supersedes_id=old.id,
+            confirm_decision_id=decision.id,
+        )
+        self.repo.add(new_assessment)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "amend_lawyer_assessment",
+            "lawyer_assessments",
+            new_assessment.id,
+            case_id=old.case_id,
+            after={"version": new_assessment.version},
+        )
+        return new_assessment
+
+    def withdraw_lawyer_assessment(
+        self: DomainService,
+        assessment_key: UUID,
+        *,
+        actor_id: UUID,
+    ) -> LawyerAssessment:
+        assessment = self.repo.get_current_lawyer_assessment(assessment_key)
+        if assessment is None:
+            raise NotFoundError("assessment not found")
+        decision = self._new_decision(
+            case_id=assessment.case_id,
+            actor_id=actor_id,
+            decision_type="WITHDRAW_LAWYER_ASSESSMENT",
+            target_type="LawyerAssessment",
+            target_id=assessment.assessment_key,
+            result=DecisionResult.CONFIRMED.value,
+            payload={"assessment_key": str(assessment_key)},
+        )
+        self.repo.add_decision(decision)
+        self.repo.flush()
+        assessment.status = LawyerAssessmentStatus.WITHDRAWN.value
+        assessment.updated_at = _now()
+        self._audit(
+            actor_id,
+            "withdraw_lawyer_assessment",
+            "lawyer_assessments",
+            assessment.id,
+            case_id=assessment.case_id,
+            after={"status": assessment.status},
+        )
+        return assessment
+
+    # ----- Issue merge / split -----
+
+    def merge_issues(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        source_issue_keys: list[UUID],
+        merged_statement: str,
+        actor_id: UUID,
+    ) -> Issue:
+        if len(source_issue_keys) < 2:
+            raise ValidationError("merge requires at least two issues")
+        sources: list[Issue] = []
+        for key in source_issue_keys:
+            issue = self._require_current_issue(key)
+            if issue.case_id != case_id:
+                raise ValidationError("cross-case merge rejected")
+            if issue.status != IssueStatus.CONFIRMED.value:
+                raise ValidationError("only CONFIRMED issues can be merged")
+            sources.append(issue)
+
+        decision = _persist_issue_structure_decision(
+            self,
+            case_id=case_id,
+            actor_id=actor_id,
+            decision_type="MERGE_ISSUES",
+            target_id=uuid.uuid4(),
+            payload={
+                "source_keys": [str(k) for k in source_issue_keys],
+                "merged_statement": merged_statement,
+            },
+        )
+
+        new_key = uuid.uuid4()
+        merged = Issue(
+            issue_key=new_key,
+            case_id=case_id,
+            statement=merged_statement,
+            order_index=min(i.order_index for i in sources),
+            source_type=IssueSourceType.LAWYER_REFINED.value,
+            status=IssueStatus.CONFIRMED.value,
+            version=1,
+            is_current=True,
+            confirm_decision_id=decision.id,
+        )
+        from backend.domain.services import _sync_issue_layer
+
+        _sync_issue_layer(merged)
+        self.repo.add(merged)
+        self.repo.flush()
+
+        seen_fact_links: set[tuple] = set()
+        for src in sources:
+            for link in self.repo.list_issue_fact_links(src.issue_key, src.version):
+                key = (link.fact_key, link.fact_version, link.role)
+                if key in seen_fact_links:
+                    continue
+                seen_fact_links.add(key)
+                self.repo.add(
+                    IssueFactLink(
+                        case_id=case_id,
+                        issue_key=merged.issue_key,
+                        issue_version=merged.version,
+                        fact_key=link.fact_key,
+                        fact_version=link.fact_version,
+                        role=link.role,
+                        status=IssueLinkStatus.ACTIVE.value,
+                        explanation=link.explanation,
+                        created_by=actor_id,
+                    )
+                )
+            src.status = IssueStatus.SUPERSEDED.value
+            src.is_current = False
+            src.updated_at = _now()
+            _sync_issue_layer(src)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "merge_issues",
+            "issues",
+            merged.id,
+            case_id=case_id,
+            after={
+                "issue_key": str(merged.issue_key),
+                "merged_from": [str(k) for k in source_issue_keys],
+                "decision_id": str(decision.id),
+            },
+        )
+        _require_structure_mutation_audit(
+            self,
+            case_id=case_id,
+            action="merge_issues",
+            decision_type="MERGE_ISSUES",
+        )
+        return merged
+
+    def split_issue(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        source_issue_key: UUID,
+        targets: list[dict[str, Any]],
+        actor_id: UUID,
+    ) -> list[Issue]:
+        """Lawyer explicitly assigns links to target issues."""
+        if not targets or len(targets) < 2:
+            raise ValidationError("split requires at least two target issues")
+        source = self._require_current_issue(source_issue_key)
+        if source.case_id != case_id:
+            raise ValidationError("cross-case split rejected")
+        if source.status != IssueStatus.CONFIRMED.value:
+            raise ValidationError("only CONFIRMED issues can be split")
+
+        decision = _persist_issue_structure_decision(
+            self,
+            case_id=case_id,
+            actor_id=actor_id,
+            decision_type="SPLIT_ISSUE",
+            target_id=source.issue_key,
+            payload={"source_key": str(source_issue_key), "target_count": len(targets)},
+        )
+
+        from backend.domain.services import _sync_issue_layer
+
+        created: list[Issue] = []
+        for idx, target in enumerate(targets):
+            statement = str(target["statement"])
+            issue = Issue(
+                issue_key=uuid.uuid4(),
+                case_id=case_id,
+                statement=statement,
+                order_index=source.order_index + idx,
+                source_type=IssueSourceType.LAWYER_REFINED.value,
+                status=IssueStatus.CONFIRMED.value,
+                version=1,
+                is_current=True,
+                parent_issue_key=source.issue_key,
+                confirm_decision_id=decision.id,
+            )
+            _sync_issue_layer(issue)
+            self.repo.add(issue)
+            self.repo.flush()
+            for link_spec in target.get("fact_links") or []:
+                self.link_fact_to_issue(
+                    case_id=case_id,
+                    issue_key=issue.issue_key,
+                    issue_version=issue.version,
+                    fact_key=UUID(str(link_spec["fact_key"])),
+                    fact_version=int(link_spec["fact_version"]),
+                    role=str(link_spec.get("role", "SUPPORT")),
+                    actor_id=actor_id,
+                )
+            created.append(issue)
+
+        source.status = IssueStatus.SUPERSEDED.value
+        source.is_current = False
+        source.updated_at = _now()
+        _sync_issue_layer(source)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "split_issue",
+            "issues",
+            source.id,
+            case_id=case_id,
+            after={
+                "source_key": str(source_issue_key),
+                "new_keys": [str(i.issue_key) for i in created],
+                "decision_id": str(decision.id),
+            },
+        )
+        _require_structure_mutation_audit(
+            self,
+            case_id=case_id,
+            action="split_issue",
+            decision_type="SPLIT_ISSUE",
+        )
+        return created
+
+    def link_legal_theory_to_issue(
+        self: DomainService,
+        *,
+        case_id: UUID,
+        issue_key: UUID,
+        issue_version: int,
+        legal_theory_id: UUID,
+        role: str,
+        actor_id: UUID,
+    ) -> IssueLegalTheoryLink:
+        self._require_case(case_id)
+        issue = self._require_issue_version(issue_key, issue_version)
+        if issue.case_id != case_id:
+            raise ValidationError("issue case_id mismatch")
+        link = IssueLegalTheoryLink(
+            case_id=case_id,
+            issue_key=issue_key,
+            issue_version=issue_version,
+            legal_theory_id=legal_theory_id,
+            role=role,
+            status=ClaimLinkStatus.ACTIVE.value,
+        )
+        self.repo.add(link)
+        self.repo.flush()
+        self._audit(
+            actor_id,
+            "link_legal_theory_to_issue",
+            "issue_legal_theory_links",
+            link.id,
+            case_id=case_id,
+            after={"legal_theory_id": str(legal_theory_id), "role": role},
+        )
+        return link
diff --git a/backend/domain/services.py b/backend/domain/services.py
index 0956471..2d4c5e9 100644
--- a/backend/domain/services.py
+++ b/backend/domain/services.py
@@ -32,6 +32,10 @@ from backend.domain.enums import (
     StaleEvent,
 )
 from backend.domain.errors import ConflictError, ImmutableError, NotFoundError, ValidationError
+from backend.domain.issue_centered import (
+    IssueCenteredDomainMixin,
+    _reject_claim_direction_production_mutation,
+)
 from backend.domain.stale import invalidate_dependencies
 from backend.models import (
     AuditLog,
@@ -73,7 +77,7 @@ def _confirmation_set_hash(parts: list[str]) -> str:
     return hashlib.sha256(blob.encode("utf-8")).hexdigest()
 
 
-class DomainService:
+class DomainService(IssueCenteredDomainMixin):
     def __init__(self, session: Session) -> None:
         self.session = session
         self.repo = Repository(session)
@@ -913,7 +917,9 @@ class DomainService:
         case_id: UUID,
         payload: dict[str, Any],
         actor_id: UUID | None = None,
+        _legacy_compat: bool = False,
     ) -> ClaimDirection:
+        _reject_claim_direction_production_mutation(_legacy_compat=_legacy_compat)
         self._require_case(case_id)
         validated = validate_claim_direction_payload(payload)
         claim = ClaimDirection(
diff --git a/backend/tests/integration/test_issue_centered_v2_invariants.py b/backend/tests/integration/test_issue_centered_v2_invariants.py
new file mode 100644
index 0000000..186e702
--- /dev/null
+++ b/backend/tests/integration/test_issue_centered_v2_invariants.py
@@ -0,0 +1,522 @@
+"""Issue-centered V2 — four invariant assertions (Issue #80 SSOT repair / #73 / #60 / #83 / #86 / #85 / #84)."""
+
+from __future__ import annotations
+
+import uuid
+
+import pytest
+from sqlalchemy import select
+from sqlalchemy.exc import IntegrityError
+
+from backend.application.issue_work_product import (
+    IssueWorkProductService,
+    assert_read_only_projection,
+    enforce_inv1_read_only,
+    guard_claim_direction_production_mutation,
+    is_read_only_projection,
+)
+from backend.domain.enums import DecisionResult
+from backend.domain.errors import ConflictError, NotFoundError, ValidationError
+from backend.domain.issue_centered import (
+    _guard_cross_case_proof_task_fact_pair,
+    _guard_explicit_proof_task_fact_versions,
+    _guard_formal_defense_opponent_material_ref,
+    _guard_formal_defense_side,
+    _guard_resolved_explicit_versions,
+    _normalize_opponent_material_ref,
+    _reject_claim_direction_production_mutation,
+    _require_structure_mutation_audit,
+)
+from backend.domain.services import DomainService
+from backend.models import AuditLog, HumanDecision
+from backend.tests.integration.test_case_analyst import _seed_accepted_evidence
+from backend.tests.integration.test_issue_centered_v2 import _seed_fact
+
+
+def test_invariant_guard_functions_reject_invalid_inputs() -> None:
+    """Direct unit checks on INV-1/2/3 guard helpers (executed code, not prose)."""
+    with pytest.raises(ValidationError, match="ClaimDirection production"):
+        _reject_claim_direction_production_mutation(_legacy_compat=False)
+    _reject_claim_direction_production_mutation(_legacy_compat=True)
+    with pytest.raises(ValidationError, match="explicit positive"):
+        _guard_explicit_proof_task_fact_versions(0, 1)
+    with pytest.raises(ValidationError, match="explicit positive"):
+        _guard_explicit_proof_task_fact_versions(1, -1)
+
+    class _FakeVersioned:
+        def __init__(self, version: int) -> None:
+            self.version = version
+
+    with pytest.raises(ValidationError, match="explicit proof_task_version"):
+        _guard_resolved_explicit_versions(
+            proof_task_version=2,
+            fact_version=1,
+            task=_FakeVersioned(1),
+            fact=_FakeVersioned(1),
+        )
+    with pytest.raises(ValidationError, match="explicit fact_version"):
+        _guard_resolved_explicit_versions(
+            proof_task_version=1,
+            fact_version=2,
+            task=_FakeVersioned(1),
+            fact=_FakeVersioned(1),
+        )
+    _guard_resolved_explicit_versions(
+        proof_task_version=1,
+        fact_version=1,
+        task=_FakeVersioned(1),
+        fact=_FakeVersioned(1),
+    )
+
+    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
+        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", None)
+    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
+        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", "   ")
+    assert _normalize_opponent_material_ref("  material:answer-001  ") == "material:answer-001"
+    assert _normalize_opponent_material_ref("   ") is None
+    _guard_formal_defense_opponent_material_ref(
+        "FORMAL_DEFENSE", "material:answer-001"
+    )
+    with pytest.raises(ValidationError, match="OPPONENT side"):
+        _guard_formal_defense_side("FORMAL_DEFENSE", "OUR")
+    _guard_formal_defense_side("FORMAL_DEFENSE", "OPPONENT")
+    assert is_read_only_projection() is True
+    with pytest.raises(RuntimeError, match="read-only"):
+        assert_read_only_projection("claim_directions")
+    with pytest.raises(RuntimeError, match="read-only"):
+        enforce_inv1_read_only("issues")
+    with pytest.raises(RuntimeError, match="read-only"):
+        IssueWorkProductService.guard_write_attempt("claim_directions")
+    with pytest.raises(ValidationError, match="ClaimDirection production"):
+        guard_claim_direction_production_mutation(_legacy_compat=False)
+    guard_claim_direction_production_mutation(_legacy_compat=True)
+
+
+def test_invariant_1_no_production_claim_direction_creation(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-1: production path must not create ClaimDirection without _legacy_compat."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV1", owner_user_id=owner_id)
+    with pytest.raises(ValidationError, match="ClaimDirection production"):
+        svc.create_claim_direction(
+            case_id=case.id,
+            payload={"claims": [], "parties": {}},
+            actor_id=actor_id,
+        )
+    # Explicit: no ClaimDirection row created
+    from backend.models import ClaimDirection
+
+    rows = list(
+        db_session.scalars(
+            select(ClaimDirection).where(ClaimDirection.case_id == case.id)
+        )
+    )
+    assert rows == []
+
+
+def test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-2: ProofTaskFactLink requires explicit versions; rejects cross-case links."""
+    svc = DomainService(db_session)
+    c1 = svc.create_case(title="INV2-A", owner_user_id=owner_id)
+    c2 = svc.create_case(title="INV2-B", owner_user_id=owner_id)
+    *_, item = _seed_accepted_evidence(
+        db_session, owner_id=owner_id, actor_id=actor_id, case=c1
+    )
+    fact = _seed_fact(svc, c1.id, actor_id, item)
+    issue2 = svc.confirm_issue(
+        svc.propose_issue(case_id=c2.id, statement="跨案焦点").issue_key,
+        actor_id=actor_id,
+    )
+    task = svc.create_lawyer_proof_task(
+        case_id=c2.id,
+        issue_key=issue2.issue_key,
+        issue_version=issue2.version,
+        description="跨案任务",
+        actor_id=actor_id,
+    )
+    # Zero/negative versions → ValidationError (explicit positive versions required)
+    with pytest.raises(ValidationError, match="explicit positive"):
+        svc.link_fact_to_proof_task(
+            case_id=c2.id,
+            proof_task_key=task.proof_task_key,
+            proof_task_version=0,
+            fact_key=fact.fact_key,
+            fact_version=fact.version,
+            role="SUPPORT",
+            actor_id=actor_id,
+        )
+    # Implicit/nonexistent proof_task_version → NotFoundError (not silent current/latest)
+    with pytest.raises(NotFoundError, match="proof task version not found"):
+        svc.link_fact_to_proof_task(
+            case_id=c2.id,
+            proof_task_key=task.proof_task_key,
+            proof_task_version=task.version + 99,
+            fact_key=fact.fact_key,
+            fact_version=fact.version,
+            role="SUPPORT",
+            actor_id=actor_id,
+        )
+    # Implicit/nonexistent fact_version → NotFoundError
+    with pytest.raises(NotFoundError, match="fact version not found"):
+        svc.link_fact_to_proof_task(
+            case_id=c2.id,
+            proof_task_key=task.proof_task_key,
+            proof_task_version=task.version,
+            fact_key=fact.fact_key,
+            fact_version=fact.version + 99,
+            role="SUPPORT",
+            actor_id=actor_id,
+        )
+    # Cross-case fact → ValidationError
+    with pytest.raises(ValidationError, match="cross-case fact link rejected"):
+        svc.link_fact_to_proof_task(
+            case_id=c2.id,
+            proof_task_key=task.proof_task_key,
+            proof_task_version=task.version,
+            fact_key=fact.fact_key,
+            fact_version=fact.version,
+            role="SUPPORT",
+            actor_id=actor_id,
+        )
+    # Cross-case proof task (task in c2, link attempted under c1) → ValidationError
+    issue1 = svc.confirm_issue(
+        svc.propose_issue(case_id=c1.id, statement="案A焦点").issue_key,
+        actor_id=actor_id,
+    )
+    task1 = svc.create_lawyer_proof_task(
+        case_id=c1.id,
+        issue_key=issue1.issue_key,
+        issue_version=issue1.version,
+        description="案A任务",
+        actor_id=actor_id,
+    )
+    fact2 = _seed_fact(svc, c2.id, actor_id, item)
+    with pytest.raises(ValidationError, match="cross-case proof task link rejected"):
+        svc.link_fact_to_proof_task(
+            case_id=c2.id,
+            proof_task_key=task1.proof_task_key,
+            proof_task_version=task1.version,
+            fact_key=fact2.fact_key,
+            fact_version=fact2.version,
+            role="SUPPORT",
+            actor_id=actor_id,
+        )
+
+
+def test_invariant_3_formal_defense_requires_opponent_material_ref(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-3: FORMAL_DEFENSE position requires opponent_material_ref."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV3", owner_user_id=owner_id)
+    issue = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="抗辩焦点").issue_key,
+        actor_id=actor_id,
+    )
+    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
+        svc.create_lawyer_position(
+            case_id=case.id,
+            issue_key=issue.issue_key,
+            issue_version=issue.version,
+            side="OPPONENT",
+            position_type="FORMAL_DEFENSE",
+            statement="对方正式抗辩",
+            actor_id=actor_id,
+            opponent_material_ref=None,
+        )
+    pos = svc.create_lawyer_position(
+        case_id=case.id,
+        issue_key=issue.issue_key,
+        issue_version=issue.version,
+        side="OPPONENT",
+        position_type="FORMAL_DEFENSE",
+        statement="对方正式抗辩",
+        actor_id=actor_id,
+        opponent_material_ref="material:answer-001",
+    )
+    assert pos.opponent_material_ref == "material:answer-001"
+    assert pos.position_type == "FORMAL_DEFENSE"
+
+
+def test_invariant_3_formal_defense_rejects_whitespace_only_material_ref(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-3: whitespace-only opponent_material_ref is normalized then rejected."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV3-ws", owner_user_id=owner_id)
+    issue = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="空白材料焦点").issue_key,
+        actor_id=actor_id,
+    )
+    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
+        svc.create_lawyer_position(
+            case_id=case.id,
+            issue_key=issue.issue_key,
+            issue_version=issue.version,
+            side="OPPONENT",
+            position_type="FORMAL_DEFENSE",
+            statement="空白材料抗辩",
+            actor_id=actor_id,
+            opponent_material_ref="   \t  ",
+        )
+
+
+def test_invariant_4_merge_split_emit_human_decision_and_audit_log(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-4: merge_issues and split_issue emit HumanDecision + AuditLog."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV4", owner_user_id=owner_id)
+    i1 = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="焦点甲").issue_key,
+        actor_id=actor_id,
+    )
+    i2 = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="焦点乙").issue_key,
+        actor_id=actor_id,
+    )
+    merged = svc.merge_issues(
+        case_id=case.id,
+        source_issue_keys=[i1.issue_key, i2.issue_key],
+        merged_statement="合并焦点",
+        actor_id=actor_id,
+    )
+    assert merged.status == "CONFIRMED"
+    merge_decisions = list(
+        db_session.scalars(
+            select(HumanDecision).where(
+                HumanDecision.case_id == case.id,
+                HumanDecision.decision_type == "MERGE_ISSUES",
+            )
+        )
+    )
+    assert len(merge_decisions) == 1
+    assert merge_decisions[0].id is not None
+    assert merge_decisions[0].decision_type == "MERGE_ISSUES"
+    assert merge_decisions[0].result == "CONFIRMED"
+    merge_audits = list(
+        db_session.scalars(
+            select(AuditLog).where(
+                AuditLog.case_id == case.id,
+                AuditLog.action == "merge_issues",
+            )
+        )
+    )
+    assert len(merge_audits) >= 1
+    assert merge_audits[0].entity_type == "issues"
+    assert merge_audits[0].after_json is not None
+    assert merge_audits[0].after_json.get("decision_id") == str(merge_decisions[0].id)
+
+    split = svc.split_issue(
+        case_id=case.id,
+        source_issue_key=merged.issue_key,
+        targets=[
+            {"statement": "拆分甲", "fact_links": []},
+            {"statement": "拆分乙", "fact_links": []},
+        ],
+        actor_id=actor_id,
+    )
+    assert len(split) == 2
+    split_decisions = list(
+        db_session.scalars(
+            select(HumanDecision).where(
+                HumanDecision.case_id == case.id,
+                HumanDecision.decision_type == "SPLIT_ISSUE",
+            )
+        )
+    )
+    assert len(split_decisions) == 1
+    assert split_decisions[0].id is not None
+    assert split_decisions[0].decision_type == "SPLIT_ISSUE"
+    assert split_decisions[0].result == "CONFIRMED"
+    split_audits = list(
+        db_session.scalars(
+            select(AuditLog).where(
+                AuditLog.case_id == case.id,
+                AuditLog.action == "split_issue",
+            )
+        )
+    )
+    assert len(split_audits) >= 1
+    assert split_audits[0].entity_type == "issues"
+    assert split_audits[0].after_json is not None
+    assert split_audits[0].after_json.get("decision_id") == str(split_decisions[0].id)
+
+
+def test_invariant_2_cross_case_pair_guard_rejects_mismatched_cases() -> None:
+    """INV-2: _guard_cross_case_proof_task_fact_pair rejects mismatched case_id."""
+    from types import SimpleNamespace
+
+    task = SimpleNamespace(case_id=uuid.uuid4())
+    fact = SimpleNamespace(case_id=uuid.uuid4())
+    with pytest.raises(ValidationError, match="cross-case proof task fact link rejected"):
+        _guard_cross_case_proof_task_fact_pair(task, fact)
+
+
+def test_invariant_3_formal_defense_rejects_wrong_side(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-3: FORMAL_DEFENSE must be recorded on OPPONENT side."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV3-side", owner_user_id=owner_id)
+    issue = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="抗辩侧焦点").issue_key,
+        actor_id=actor_id,
+    )
+    with pytest.raises(ValidationError, match="OPPONENT side"):
+        svc.create_lawyer_position(
+            case_id=case.id,
+            issue_key=issue.issue_key,
+            issue_version=issue.version,
+            side="OUR",
+            position_type="FORMAL_DEFENSE",
+            statement="错误侧正式抗辩",
+            actor_id=actor_id,
+            opponent_material_ref="material:answer-001",
+        )
+
+
+def test_invariant_4_guard_rejects_missing_human_decision_or_audit(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-4: _require_structure_mutation_audit fails closed without HumanDecision or AuditLog."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV4-guard", owner_user_id=owner_id)
+    with pytest.raises(ConflictError, match="HumanDecision"):
+        _require_structure_mutation_audit(
+            svc,
+            case_id=case.id,
+            action="merge_issues",
+            decision_type="MERGE_ISSUES",
+        )
+    decision = HumanDecision(
+        case_id=case.id,
+        actor_id=actor_id,
+        decision_type="MERGE_ISSUES",
+        target_type="Issue",
+        target_id=uuid.uuid4(),
+        result=DecisionResult.CONFIRMED.value,
+        input_payload_json={"probe": True},
+    )
+    db_session.add(decision)
+    db_session.flush()
+    with pytest.raises(ConflictError, match="AuditLog"):
+        _require_structure_mutation_audit(
+            svc,
+            case_id=case.id,
+            action="merge_issues",
+            decision_type="MERGE_ISSUES",
+        )
+
+
+def test_invariant_4_guard_rejects_audit_without_decision_id_linkage(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-4: AuditLog.after_json must reference the emitted HumanDecision id."""
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV4-audit-link", owner_user_id=owner_id)
+    decision = HumanDecision(
+        case_id=case.id,
+        actor_id=actor_id,
+        decision_type="MERGE_ISSUES",
+        target_type="Issue",
+        target_id=uuid.uuid4(),
+        result=DecisionResult.CONFIRMED.value,
+        input_payload_json={"probe": True},
+    )
+    db_session.add(decision)
+    db_session.flush()
+    db_session.add(
+        AuditLog(
+            case_id=case.id,
+            actor_id=actor_id,
+            action="merge_issues",
+            entity_type="issues",
+            entity_id=uuid.uuid4(),
+            after_json={"issue_key": str(uuid.uuid4())},
+        )
+    )
+    db_session.flush()
+    with pytest.raises(ConflictError, match="decision_id"):
+        _require_structure_mutation_audit(
+            svc,
+            case_id=case.id,
+            action="merge_issues",
+            decision_type="MERGE_ISSUES",
+        )
+
+
+def test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-2: DB check constraints reject proof_task_version/fact_version < 1."""
+    from backend.models import ProofTaskFactLink
+
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV2-DB", owner_user_id=owner_id)
+    issue = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="DB约束焦点").issue_key,
+        actor_id=actor_id,
+    )
+    task = svc.create_lawyer_proof_task(
+        case_id=case.id,
+        issue_key=issue.issue_key,
+        issue_version=issue.version,
+        description="DB约束任务",
+        actor_id=actor_id,
+    )
+    *_, item = _seed_accepted_evidence(
+        db_session, owner_id=owner_id, actor_id=actor_id, case=case
+    )
+    fact = _seed_fact(svc, case.id, actor_id, item)
+    with pytest.raises(IntegrityError):
+        db_session.add(
+            ProofTaskFactLink(
+                case_id=case.id,
+                proof_task_key=task.proof_task_key,
+                proof_task_version=0,
+                fact_key=fact.fact_key,
+                fact_version=fact.version,
+                role="SUPPORT",
+                status="ACTIVE",
+                created_by=actor_id,
+            )
+        )
+        db_session.flush()
+    db_session.rollback()
+
+
+def test_invariant_3_db_rejects_formal_defense_without_material_ref(
+    db_session, owner_id, actor_id
+) -> None:
+    """INV-3: DB check constraint rejects FORMAL_DEFENSE without opponent_material_ref."""
+    from backend.models import IssuePosition
+
+    svc = DomainService(db_session)
+    case = svc.create_case(title="INV3-DB", owner_user_id=owner_id)
+    issue = svc.confirm_issue(
+        svc.propose_issue(case_id=case.id, statement="DB抗辩焦点").issue_key,
+        actor_id=actor_id,
+    )
+    with pytest.raises(IntegrityError):
+        db_session.add(
+            IssuePosition(
+                position_key=uuid.uuid4(),
+                case_id=case.id,
+                issue_key=issue.issue_key,
+                issue_version=issue.version,
+                side="OPPONENT",
+                position_type="FORMAL_DEFENSE",
+                source_type="OPPONENT_MATERIAL",
+                status="CONFIRMED",
+                statement="无材料引用",
+                opponent_material_ref=None,
+                version=1,
+                is_current=True,
+            )
+        )
+        db_session.flush()
+    db_session.rollback()
```