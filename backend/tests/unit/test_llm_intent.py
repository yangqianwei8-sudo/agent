"""LLM Intent Router tests with FakeLLMClient."""

from __future__ import annotations

import uuid

from backend.agent.dto import AgentIntent
from backend.agent.intent_router import IntentParseContext
from backend.llm.fake import FakeLLMClient
from backend.llm.intent_router import LLMIntentRouter


def _router(payload: dict) -> LLMIntentRouter:
    return LLMIntentRouter(FakeLLMClient(responses=[payload]))


def test_g_accept_evidence_2() -> None:
    r = _router(
        {
            "intent": "ACCEPT_EVIDENCE",
            "target_text": "证据2",
            "arguments": {},
            "confidence": 0.96,
            "reason": "用户明确要求接受编号2的证据",
        }
    )
    out = r.parse("接受证据2")
    assert out.intent == AgentIntent.ACCEPT_EVIDENCE
    assert out.targets == ["2"]
    assert "evidence_item_id" not in out.parameters


def test_h_approve_draft() -> None:
    r = _router(
        {
            "intent": "APPROVE_DRAFT",
            "target_text": "",
            "arguments": {},
            "confidence": 0.9,
            "reason": "批准起诉状",
        }
    )
    out = r.parse("批准这份起诉状")
    assert out.intent == AgentIntent.APPROVE_DRAFT


def test_i_ambiguous_hao_at_human_gate() -> None:
    r = _router(
        {
            "intent": "CONFIRM_FACT",
            "target_text": "事实1",
            "arguments": {},
            "confidence": 0.8,
            "reason": "推断同意",
        }
    )
    out = r.parse(
        "好",
        context=IntentParseContext(
            workflow_status="WAITING_USER",
            waiting_reason="EVIDENCE",
            pending_human_gate=True,
        ),
    )
    assert out.intent == AgentIntent.UNKNOWN


def test_j_invalid_enum_becomes_unknown() -> None:
    r = _router(
        {
            "intent": "HACK_THE_PLANET",
            "target_text": "",
            "arguments": {},
            "confidence": 1.0,
            "reason": "bad",
        }
    )
    out = r.parse("随便")
    assert out.intent == AgentIntent.UNKNOWN


def test_k_uuid_does_not_bypass_target_resolver() -> None:
    fake_id = str(uuid.uuid4())
    r = _router(
        {
            "intent": "ACCEPT_EVIDENCE",
            "target_text": fake_id,
            "arguments": {"evidence_item_id": fake_id},
            "confidence": 0.99,
            "reason": "llm invented uuid",
        }
    )
    out = r.parse(f"接受证据 {fake_id}")
    # UUID stripped from targets; no evidence_item_id param
    assert all(
        len(t) < 36 or t.count("-") != 4 for t in out.targets
    ) or out.targets == []
    assert "evidence_item_id" not in out.parameters
    # Without numeric target → UNKNOWN / conversation / or ACCEPT without usable target
    assert out.intent in {
        AgentIntent.UNKNOWN,
        AgentIntent.ACCEPT_EVIDENCE,
        AgentIntent.CASE_CONVERSATION,
    }
    if out.intent == AgentIntent.ACCEPT_EVIDENCE:
        assert fake_id not in out.targets
