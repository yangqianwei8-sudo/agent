# Issue #60 Repair Evidence — Issue-Centered V2 (#57)

Generated: 2026-09-13T06:16:00Z  
Base commit: `375c282`  
Implementation commit: `c19379b` (already on main)  
Repair commit: (this commit)

## Reviewer bundle (priority — fits 12k diff window)

See `docs/issue60_repair_evidence/00_reviewer_bundle.md` for compact submission with all four invariants, test PASS output, and complete proof_gaps/lawyer_assessments migration excerpt.

## Full untruncated source copies

| File | Lines | Path |
|------|-------|------|
| Migration | 394 | `docs/issue60_repair_evidence/sources/migration_h9b0c1d2e3f4.py` |
| Domain | 1062 | `docs/issue60_repair_evidence/sources/domain_issue_centered.py` |
| Application | 501 | `docs/issue60_repair_evidence/sources/application_issue_work_product.py` |

Production paths (identical content): `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py`, `backend/domain/issue_centered.py`, `backend/application/issue_work_product.py`.

## Test output (actual run 2026-09-13T06:15:56Z)

See `test_issue_centered_v2_output.txt` and `live_issue_centered_v2_acceptance_output.txt`.

**pytest:** 15 passed, 2 warnings in 1.37s  
**live acceptance:** ISSUE-CENTERED CASE WORKSPACE V2: PASS (30 steps)

## Four required invariants

1. **ClaimDirection:** `create_claim_direction()` blocked without `_legacy_compat` — `test_claim_direction_production_disabled` PASSED
2. **ProofTaskFactLink:** explicit versions required, cross-case rejected — `test_proof_task_fact_link_rejects_implicit_version`, `test_proof_task_fact_link_cross_case_rejected` PASSED
3. **FORMAL_DEFENSE:** requires `opponent_material_ref` — `test_position_formal_defense_requires_material` PASSED
4. **merge/split:** HumanDecision + AuditLog — `test_issue_merge_and_split` PASSED
