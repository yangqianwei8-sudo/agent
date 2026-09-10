"""Case Work Product V1 — scenario acceptance via Workspace API."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from backend.application.workspace import WorkspaceQueryService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.workflow.seed import ensure_pleading_prep_template  # noqa: E402

OUT = Path("backend/tests/fixtures/case_work_product_live_acceptance.json")


def _wp(session: Session, case_id: uuid.UUID) -> dict:
    return WorkspaceQueryService(session).get_workspace(case_id)


def main() -> None:
    results: list[dict] = []
    Session = get_session_factory()
    with Session() as session:
        ensure_pleading_prep_template(session)
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)

        # Scenario A — new case, no materials
        case_a = svc.create_case(title="场景A 新案件", owner_user_id=owner)
        ws_a = _wp(session, case_a.id)
        wp_a = ws_a["work_product"]
        results.append(
            {
                "scenario": "A",
                "stage": wp_a["stage"]["stage_label"],
                "next_action": wp_a["next_action"]["label"],
                "todo_count": wp_a["todo_summary"]["total_count"],
            }
        )
        assert wp_a["stage"]["stage_label"] == "材料整理"
        assert wp_a["next_action"]["label"] == "上传案件材料"

        # Scenario B — 1 usable + 1 pending
        case_b = svc.create_case(title="场景B 待解析", owner_user_id=owner)
        text = "合同服务费100000元。"
        mat_ok = svc.register_material(
            case_id=case_b.id,
            filename="ok.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:8]}",
            storage_key=f"k-{uuid.uuid4().hex[:8]}",
            created_by=actor,
        )
        ec = svc.create_extracted_content(
            material_id=mat_ok.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            full_text=text,
            actor_id=actor,
        )
        svc.create_source_span(
            material_id=mat_ok.id,
            extracted_content_id=ec.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
        )
        svc.register_material(
            case_id=case_b.id,
            filename="scan.pdf",
            mime="application/pdf",
            byte_size=100,
            content_hash=f"h-{uuid.uuid4().hex[:8]}",
            storage_key=f"k-{uuid.uuid4().hex[:8]}",
            created_by=actor,
        )
        ws_b = _wp(session, case_b.id)
        results.append(
            {
                "scenario": "B",
                "usable": len(ws_b["usable_materials"]),
                "pending": len(ws_b["pending_materials"]),
                "next_action": ws_b["work_product"]["next_action"]["label"],
            }
        )
        assert len(ws_b["usable_materials"]) == 1
        assert len(ws_b["pending_materials"]) >= 1

        # Scenario C — pending evidence + fact
        case_c = svc.create_case(title="场景C 待确认", owner_user_id=owner)
        ev = svc.create_evidence_item(
            case_id=case_c.id,
            number="1",
            title="合同",
            category="CONTRACT",
            summary="签约",
            actor_id=actor,
        )
        svc.propose_fact(
            case_id=case_c.id,
            statement="原告已交付成果",
            evidence_links=[
                {"evidence_item_id": ev.id, "evidence_item_version": ev.version}
            ],
            actor_id=actor,
        )
        ws_c = _wp(session, case_c.id)
        todos_c = ws_c["work_product"]["todo_summary"]["total_count"]
        results.append({"scenario": "C", "todo_count": todos_c})
        assert todos_c >= 2

        session.rollback()  # read-only scenarios only

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
