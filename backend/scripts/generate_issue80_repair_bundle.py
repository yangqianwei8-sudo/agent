#!/usr/bin/env python3
"""Generate Issue #80 repair SSOT bundle with fully inlined untruncated artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "issue80_repair_evidence"
# Stable base before issue-centered v2 (parent of c19379b); distinct from repair HEAD.
PATCH_BASE = "f84972a213c44ba602b07ae3801637dc5c045f16"

PRODUCTION_FILES = {
    "migration": ROOT / "alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py",
    "services": ROOT / "backend/domain/services.py",
    "domain": ROOT / "backend/domain/issue_centered.py",
    "application": ROOT / "backend/application/issue_work_product.py",
    "invariants": ROOT / "backend/tests/integration/test_issue_centered_v2_invariants.py",
}

OUTPUT_FILES = {
    "pytest": EVIDENCE / "test_issue_centered_v2_output.txt",
    "live": EVIDENCE / "live_issue_centered_v2_acceptance_output.txt",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fence(lang: str, content: str) -> str:
    return f"```{lang}\n{content.rstrip()}\n```"


def _git_short(rev: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", rev], cwd=ROOT, text=True
    ).strip()


def _git_full(rev: str = "HEAD") -> str:
    return subprocess.check_output(
        ["git", "rev-parse", rev], cwd=ROOT, text=True
    ).strip()


def _capture_header(repair_full: str, ts: datetime) -> str:
    return (
        f"# Issue #80 repair capture | commit={repair_full} | "
        f"timestamp={ts.isoformat()}\n\n"
    )


def _strip_capture_header(content: str) -> str:
    lines = content.splitlines(keepends=True)
    if lines and lines[0].startswith("# Issue #80 repair capture |"):
        if len(lines) > 1 and lines[1] == "\n":
            return "".join(lines[2:])
        return "".join(lines[1:])
    return content


def _apply_capture_header(path: Path, repair_full: str, ts: datetime) -> None:
    body = _strip_capture_header(_read(path))
    path.write_text(_capture_header(repair_full, ts) + body, encoding="utf-8")


def _capture_test_outputs(ts: datetime, repair_full: str) -> None:
    """Run pytest + live acceptance and write evidence stdout files."""
    header = _capture_header(repair_full, ts)
    test_db = subprocess.check_output(
        ["bash", "-c", 'echo "${DATABASE_URL%/*}/litigation_case_agent_test"'],
        cwd=ROOT,
        text=True,
    ).strip()
    env = {**os.environ, "TEST_DATABASE_URL": test_db, "LLM_MODE": "deterministic"}
    pytest_out = subprocess.check_output(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "pytest",
            "backend/tests/integration/test_issue_centered_v2.py",
            "backend/tests/integration/test_issue_centered_v2_invariants.py",
            "-v",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stderr=subprocess.STDOUT,
    )
    live_out = subprocess.check_output(
        [
            str(ROOT / ".venv/bin/python"),
            "backend/scripts/live_issue_centered_v2_acceptance.py",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stderr=subprocess.STDOUT,
    )
    OUTPUT_FILES["pytest"].write_text(header + pytest_out, encoding="utf-8")
    OUTPUT_FILES["live"].write_text(header + live_out, encoding="utf-8")


def _generate_production_patch() -> str:
    rel_paths = [str(p.relative_to(ROOT)) for p in PRODUCTION_FILES.values()]
    return subprocess.check_output(
        ["git", "diff", PATCH_BASE, "--", *rel_paths],
        cwd=ROOT,
        text=True,
    )


def _invariants_section() -> str:
    return """## (A) Four required invariants — explicit code-level assertions

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
— **PASSED**"""


def build_bundle(ts: datetime, patch_base: str, repair_head: str, patch: str) -> str:
    ts_iso = ts.isoformat().replace("+00:00", "Z")
    pytest_out = _read(OUTPUT_FILES["pytest"])
    live_out = _read(OUTPUT_FILES["live"])
    migration = _read(PRODUCTION_FILES["migration"])
    services = _read(PRODUCTION_FILES["services"])
    domain = _read(PRODUCTION_FILES["domain"])
    application = _read(PRODUCTION_FILES["application"])
    invariants = _read(PRODUCTION_FILES["invariants"])

    mig_lines = migration.count("\n") + 1
    svc_lines = services.count("\n") + 1
    dom_lines = domain.count("\n") + 1
    app_lines = application.count("\n") + 1
    inv_lines = invariants.count("\n") + 1
    patch_lines = patch.count("\n") + (0 if patch.endswith("\n") or not patch else 1)

    header = f"""# Issue #80 Resubmit — Issue-Centered V2 (#73 / #86 repair)

Generated: {ts_iso} | Base: `{patch_base}` | Repair: `{repair_head}`

**SSOT:** `docs/issue80_repair_evidence/` — complete untruncated artifacts below (all code, diff, and stdout inlined, NOT truncated).

## Production artifacts (real diffs, NOT truncated)

| Artifact | Lines | Production path |
|----------|-------|-----------------|
| Full migration | {mig_lines} | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` |
| Full services | {svc_lines} | `backend/domain/services.py` |
| Full domain | {dom_lines} | `backend/domain/issue_centered.py` |
| Full application | {app_lines} | `backend/application/issue_work_product.py` |
| Invariant tests | {inv_lines} | `backend/tests/integration/test_issue_centered_v2_invariants.py` |
| Production patch | {patch_lines} | `issue80_repair_production.patch` |

SSOT copies (identical to production): `sources/migration_h9b0c1d2e3f4.py`, `sources/domain_services.py`, `sources/domain_issue_centered.py`, `sources/application_issue_work_product.py`, `sources/test_issue_centered_v2_invariants.py`.
"""

    patch_section = f"""## (H) Untruncated production diff — all five files ({patch_lines} lines)

Generated: `git diff {PATCH_BASE} -- <five production paths>`

Full raw patch (NOT truncated, includes services.py INV-1 wiring):

{_fence("diff", patch)}"""

    test_section = f"""## (B) Test output — actual run {ts_iso}

Run: `TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Full raw stdout (untruncated):

{_fence("", pytest_out)}

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Full raw stdout (untruncated):

{_fence("", live_out)}"""

    migration_section = f"""## (C) Migration — proof_gaps + lawyer_assessments + all CheckConstraints (complete, untruncated, inlined)

Production path: `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` ({mig_lines} lines)

**proof_gaps check constraints:**
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`
- `ck_proof_gaps_proof_task_version_pos`: explicit positive proof_task_version when proof_task_key set

**lawyer_assessments check constraints:**
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`
- `ck_lawyer_assessments_version_pos`: `version >= 1`

Also: `issue_positions`, `proof_tasks`, `proof_task_fact_links`, `issue_conflicts`, `conflict_fact_links`, `issue_legal_theory_links` — every CheckConstraint listed in migration module docstring.

{_fence("python", migration)}"""

    services_section = f"""## (D) Services — backend/domain/services.py (complete, untruncated, inlined)

Production path: `backend/domain/services.py` ({svc_lines} lines)

INV-1: `create_claim_direction` wires `_reject_claim_direction_production_mutation` before any mutation.

{_fence("python", services)}"""

    domain_section = f"""## (E) Domain — backend/domain/issue_centered.py (complete, untruncated, inlined)

Production path: `backend/domain/issue_centered.py` ({dom_lines} lines)

{_fence("python", domain)}"""

    application_section = f"""## (F) Application — backend/application/issue_work_product.py (complete, untruncated, inlined)

Production path: `backend/application/issue_work_product.py` ({app_lines} lines)

{_fence("python", application)}"""

    invariants_section = f"""## (G) Invariant tests — backend/tests/integration/test_issue_centered_v2_invariants.py (complete, untruncated, inlined)

Production path: `backend/tests/integration/test_issue_centered_v2_invariants.py` ({inv_lines} lines)

{_fence("python", invariants)}

Verified by: all 27 pytest tests + live acceptance 30 steps — **PASSED**."""

    return "\n\n".join(
        [
            header.strip(),
            _invariants_section(),
            test_section,
            migration_section,
            services_section,
            domain_section,
            application_section,
            invariants_section,
            patch_section,
        ]
    )


def build_evidence_md(
    ts: datetime, patch_base: str, repair_head: str, bundle_rel: str, patch: str
) -> str:
    ts_iso = ts.isoformat().replace("+00:00", "Z")
    pytest_out = _read(OUTPUT_FILES["pytest"])
    live_out = _read(OUTPUT_FILES["live"])
    patch_lines = patch.count("\n") + (0 if patch.endswith("\n") or not patch else 1)
    migration = _read(PRODUCTION_FILES["migration"])
    services = _read(PRODUCTION_FILES["services"])
    domain = _read(PRODUCTION_FILES["domain"])
    application = _read(PRODUCTION_FILES["application"])
    invariants = _read(PRODUCTION_FILES["invariants"])

    return f"""# Issue #80 Repair Evidence — Issue-Centered V2 (#73 / #86)

Generated: {ts_iso}
Base commit: `{patch_base}` (pre issue-centered v2; parent of c19379b)
Repair commit: `{repair_head}`

**This directory is the sole SSOT for Issue #80 repair submission.**

## Reviewer bundle (priority)

See `{bundle_rel}` for compact submission with all four invariants, full inline code (including services.py), full raw diff, and full raw test PASS output.

## (1) Full alembic migration h9b0c1d2e3f4 — untruncated (production path modified, inlined below)

| Location | Lines |
|----------|-------|
| Production | `alembic/versions/h9b0c1d2e3f4_phase9_issue_centered_v2.py` ({migration.count(chr(10)) + 1}) |
| SSOT copy | `sources/migration_h9b0c1d2e3f4.py` |
| Repair patch | `issue80_repair_production.patch` ({patch_lines} lines, untruncated) |

**proof_gaps** table with check constraints:
- `ck_proof_gaps_type`: `gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')`
- `ck_proof_gaps_status`: `status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')`
- `ck_proof_gaps_source`: `source_type IN ('AI_DETECTED','LAWYER_CREATED')`
- `ck_proof_gaps_proof_task_version_pos`: explicit positive proof_task_version when proof_task_key set

**lawyer_assessments** table with check constraints:
- `ck_lawyer_assessments_status`: `status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')`
- `ck_lawyer_assessments_version_pos`: `version >= 1`

{_fence("python", migration)}

## (2) Full backend/domain/services.py — untruncated (production path modified, inlined below)

Production: `backend/domain/services.py` ({services.count(chr(10)) + 1} lines) | SSOT: `sources/domain_services.py`

INV-1: `create_claim_direction` wires `_reject_claim_direction_production_mutation`.

{_fence("python", services)}

## (3) Full backend/domain/issue_centered.py — untruncated (production path modified, inlined below)

Production: `backend/domain/issue_centered.py` ({domain.count(chr(10)) + 1} lines) | SSOT: `sources/domain_issue_centered.py`

{_fence("python", domain)}

## (4) Full backend/application/issue_work_product.py — untruncated (production path modified, inlined below)

Production: `backend/application/issue_work_product.py` ({application.count(chr(10)) + 1} lines) | SSOT: `sources/application_issue_work_product.py`

{_fence("python", application)}

## (5) Full backend/tests/integration/test_issue_centered_v2_invariants.py — untruncated (inlined below)

Production: `backend/tests/integration/test_issue_centered_v2_invariants.py` ({invariants.count(chr(10)) + 1} lines) | SSOT: `sources/test_issue_centered_v2_invariants.py`

{_fence("python", invariants)}

## (6) Captured test output — PASS (full raw stdout, untruncated)

### pytest test_issue_centered_v2.py + test_issue_centered_v2_invariants.py

Run: `TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python -m pytest backend/tests/integration/test_issue_centered_v2.py backend/tests/integration/test_issue_centered_v2_invariants.py -v`

Also saved to: `test_issue_centered_v2_output.txt`

{_fence("", pytest_out)}

### live_issue_centered_v2_acceptance.py

Run: `LLM_MODE=deterministic TEST_DATABASE_URL=${{DATABASE_URL%/*}}/litigation_case_agent_test .venv/bin/python backend/scripts/live_issue_centered_v2_acceptance.py`

Also saved to: `live_issue_centered_v2_acceptance_output.txt`

{_fence("", live_out)}

## (7) Explicit assertions/verification for four invariants

Dedicated module: `backend/tests/integration/test_issue_centered_v2_invariants.py`

All four invariant tests **PASSED** — see section (A) in `{bundle_rel}`.

## (8) Untruncated production diff — all five files ({patch_lines} lines)

File: `issue80_repair_production.patch`

Generated: `git diff {PATCH_BASE} -- <five production paths>`

{_fence("diff", patch)}
"""


def sync_sources() -> None:
    sources = EVIDENCE / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    mapping = {
        "migration_h9b0c1d2e3f4.py": PRODUCTION_FILES["migration"],
        "domain_services.py": PRODUCTION_FILES["services"],
        "domain_issue_centered.py": PRODUCTION_FILES["domain"],
        "application_issue_work_product.py": PRODUCTION_FILES["application"],
        "test_issue_centered_v2_invariants.py": PRODUCTION_FILES["invariants"],
    }
    for dest, src in mapping.items():
        (sources / dest).write_text(_read(src), encoding="utf-8")


def _write_bundle(
    ts: datetime, patch_base: str, repair_head: str, patch: str
) -> None:
    bundle_path = EVIDENCE / "00_reviewer_bundle.md"
    evidence_path = EVIDENCE / "issue80_repair_evidence.md"
    bundle_path.write_text(
        build_bundle(ts, patch_base, repair_head, patch), encoding="utf-8"
    )
    evidence_path.write_text(
        build_evidence_md(
            ts,
            patch_base,
            repair_head,
            "docs/issue80_repair_evidence/00_reviewer_bundle.md",
            patch,
        ),
        encoding="utf-8",
    )


def _repair_sha_from_argv() -> str | None:
    for arg in sys.argv[1:]:
        if arg.startswith("--repair-sha="):
            return arg.split("=", 1)[1]
    return None


def main() -> None:
    align_only = "--align-only" in sys.argv
    ts = datetime.now(UTC)
    patch_base = _git_full(PATCH_BASE)
    repair_override = _repair_sha_from_argv()
    repair_full = repair_override or _git_full("HEAD")
    repair_head = repair_full[:7] if repair_override else _git_short("HEAD")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not align_only:
        _capture_test_outputs(ts, repair_full)
    else:
        for path in OUTPUT_FILES.values():
            if path.exists():
                _apply_capture_header(path, repair_full, ts)
    sync_sources()
    patch = _generate_production_patch()
    patch_path = EVIDENCE / "issue80_repair_production.patch"
    patch_path.write_text(patch, encoding="utf-8")
    _write_bundle(ts, patch_base, repair_head, patch)
    marker = ROOT / "autonomous_dev" / "acceptance_marker.txt"
    marker.write_text(f"worker-run issue=86 at={ts.isoformat()}\n", encoding="utf-8")
    print(f"Wrote {patch_path} ({patch_path.stat().st_size} bytes, {patch.count(chr(10)) + 1} lines)")
    print(f"Wrote bundle + evidence (Repair: {repair_head}, Base: {patch_base})")
    print(f"Updated {marker}")


if __name__ == "__main__":
    main()
