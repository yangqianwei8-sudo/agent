"""Live DeepSeek acceptance — Pleading Readiness Gate V1.

Builds the 富茂 vs 中梁 incomplete case, asks readiness questions,
attempts generate (must refuse), then supplements confirmed facts to READY
and allows draft creation.

Run:
  .\\.venv\\Scripts\\python.exe backend/scripts/live_pleading_readiness_acceptance.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

from sqlalchemy import func, select  # noqa: E402

from backend.agent.case_agent import CaseAgent  # noqa: E402
from backend.agent.dto import AgentIntent  # noqa: E402
from backend.agent.intent_router import DeterministicIntentRouter  # noqa: E402
from backend.application.pleading_readiness import PleadingReadinessService  # noqa: E402
from backend.application.pleading_writer import PleadingWriterService  # noqa: E402
from backend.infrastructure.config import get_settings  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.models import DocumentDraft, DraftCitation, EvidenceItem, Fact, HumanDecision  # noqa: E402
from backend.tests.integration.test_pleading_readiness import (  # noqa: E402
    _add_confirmed_fact,
    _seed_incident_case,
)


def main() -> int:
    settings = get_settings()
    print(f"LLM_MODE={settings.llm_mode} model={getattr(settings, 'llm_model', None)}")
    session = get_session_factory()()
    actor_id = uuid.uuid4()
    owner_id = actor_id
    try:
        svc, case, parties, _facts, claim = _seed_incident_case(
            session, owner_id=owner_id, actor_id=actor_id
        )
        session.commit()

        readiness = PleadingReadinessService(session).evaluate(case.id)
        assert readiness.status.value == "NOT_READY"
        codes = {i.code for i in readiness.blocking_issues}
        for need in (
            "DEFENDANT_LIABILITY_BASIS_MISSING",
            "PERFORMANCE_NOT_ESTABLISHED",
            "CLAIM_AMOUNT_NOT_PROVEN",
        ):
            assert need in codes, codes

        agent = CaseAgent(
            session,
            actor_id=actor_id,
            intent_engine=DeterministicIntentRouter(),
        )

        turns = [
            "现在能不能生成起诉状？",
            "为什么不能？",
            "还缺哪些关键东西？",
            "帮我生成起诉状",
        ]
        cid = None
        draft_before = session.scalar(select(func.count()).select_from(DocumentDraft)) or 0
        cite_before = session.scalar(select(func.count()).select_from(DraftCitation)) or 0
        hd_before = session.scalar(
            select(func.count())
            .select_from(HumanDecision)
            .where(HumanDecision.case_id == case.id)
        )

        answers = []
        for msg in turns:
            resp = agent.handle_message(case.id, msg, conversation_id=cid)
            cid = resp.conversation_id
            answers.append({"q": msg, "intent": resp.intent.value, "message": resp.message})
            safe = (resp.message or "")[:800].encode("utf-8", "replace").decode("utf-8")
            print(f"\n=== {msg} ===\n{safe}\n", flush=True)

        for a in answers[:3]:
            assert a["intent"] == AgentIntent.CASE_CONVERSATION.value
            body = a["message"]
            assert any(
                k in body
                for k in ("还不建议", "关键问题", "不能生成", "尚未具备", "缺少")
            ), body[:200]

        assert answers[3]["intent"] == AgentIntent.GENERATE_COMPLAINT.value
        assert "还不建议" in answers[3]["message"] or "关键问题" in answers[3]["message"]
        assert (
            session.scalar(select(func.count()).select_from(DocumentDraft)) or 0
        ) == draft_before
        assert (
            session.scalar(select(func.count()).select_from(DraftCitation)) or 0
        ) == cite_before

        _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement="中梁地产债务加入并确认承担付款责任。",
            actor_id=actor_id,
            number="40",
        )
        _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement="原告已交付优化成果并经对方签收。",
            actor_id=actor_id,
            number="41",
        )
        _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement=(
                "优化金额为5000000元，按8%计取服务费封顶300000元，"
                "已付0元，尚欠300000元。"
            ),
            actor_id=actor_id,
            number="42",
        )
        _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement="合同约定成果提交后付款，付款条件已成就，债务已到期。",
            actor_id=actor_id,
            number="43",
        )
        _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement="合同约定由被告住所地人民法院管辖。",
            actor_id=actor_id,
            number="44",
        )
        session.commit()

        ready = PleadingReadinessService(session).evaluate(case.id)
        assert ready.status.value == "READY", [i.code for i in ready.blocking_issues]

        evidences = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == case.id,
                    EvidenceItem.acceptance == "ACCEPTED",
                    EvidenceItem.is_current.is_(True),
                )
            )
        )
        all_facts = list(
            session.scalars(
                select(Fact).where(
                    Fact.case_id == case.id,
                    Fact.status == "CONFIRMED",
                    Fact.stale.is_(False),
                    Fact.is_current.is_(True),
                )
            )
        )
        result = PleadingWriterService(session).write(
            case_id=case.id,
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            },
            confirmed_fact_refs=[
                {"fact_key": str(f.fact_key), "fact_version": f.version} for f in all_facts
            ],
            accepted_evidence_refs=[
                {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
                for e in evidences
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )
        assert result.draft is not None
        session.commit()

        out = {
            "ok": True,
            "case_id": str(case.id),
            "answers": answers,
            "ready_after_supplement": ready.status.value,
            "draft_id": str(result.draft.id),
            "hd_delta_after_questions": (
                session.scalar(
                    select(func.count())
                    .select_from(HumanDecision)
                    .where(HumanDecision.case_id == case.id)
                )
                - (hd_before or 0)
            ),
        }
        path = (
            ROOT / "backend" / "tests" / "fixtures" / "pleading_readiness_live_result.json"
        )
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
