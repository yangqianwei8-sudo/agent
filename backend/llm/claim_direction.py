"""LLM ClaimDirection engine — proposals only; Application validates amounts/refs."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from pydantic import ValidationError

from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import CLAIM_DIRECTION_PROMPT_VERSION, CLAIM_DIRECTION_SYSTEM
from backend.schemas.claim_direction_proposal import (
    ClaimDirectionEngineResult,
    ClaimDirectionInput,
    ClaimDirectionProposal,
    ClaimProposalItem,
)
from backend.skills.claim_direction import FactView, PartyView

_INTEREST_HINT = re.compile(r"利率|利息|LPR|起算|年息|日息")


class LLMClaimDirectionEngine:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = CLAIM_DIRECTION_PROMPT_VERSION
        self.last_meta: dict[str, Any] = {}

    def propose(
        self,
        inp: ClaimDirectionInput,
        facts: list[FactView],
        parties: list[PartyView],
    ) -> ClaimDirectionEngineResult:
        allowed = {(str(f.fact_key), int(f.fact_version)) for f in facts}
        interest_supported = any(_INTEREST_HINT.search(f.statement or "") for f in facts)
        user_prompt = _build_prompt(inp, facts, parties)

        try:
            result = self.client.complete_json(
                system_prompt=CLAIM_DIRECTION_SYSTEM,
                user_prompt=user_prompt,
                schema_name="claim_direction_v1",
            )
        except LLMError:
            raise

        self.last_meta = {
            "engine_mode": "real",
            "model": result.model,
            "provider": result.provider,
            "prompt_version": CLAIM_DIRECTION_PROMPT_VERSION,
            "request_id": result.request_id,
            "usage": result.usage.model_dump(),
            "latency_ms": result.latency_ms,
        }

        payload = result.parsed_json
        if not isinstance(payload, dict):
            raise LLMSchemaValidationError("claim_direction root must be object")
        raw_list = payload.get("proposals")
        if not isinstance(raw_list, list):
            raise LLMSchemaValidationError("proposals must be array")

        proposals: list[ClaimDirectionProposal] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            try:
                prop = _parse_proposal(raw, allowed, interest_supported)
            except ValidationError:
                continue
            if prop is None:
                continue
            proposals.append(prop)

        if not proposals and raw_list:
            # Blocking: had proposals but all invalid on core fields
            raise LLMSchemaValidationError(
                "all claim direction proposals failed core validation"
            )
        return ClaimDirectionEngineResult(proposals=proposals)


def _parse_proposal(
    raw: dict[str, Any],
    allowed: set[tuple[str, int]],
    interest_supported: bool,
) -> ClaimDirectionProposal | None:
    row = dict(raw)
    if "proposal_id" not in row:
        row["proposal_id"] = str(uuid.uuid4())
    claims_raw = row.get("claims")
    if not isinstance(claims_raw, list) or not claims_raw:
        return None
    cleaned_claims: list[dict[str, Any]] = []
    missing = list(row.get("missing_confirmations") or [])
    for claim in claims_raw:
        if not isinstance(claim, dict):
            return None  # blocking core claim list corruption
        c = dict(claim)
        refs = c.get("supporting_fact_refs") or []
        if not isinstance(refs, list) or not refs:
            return None
        good_refs = []
        for ref in refs:
            if not isinstance(ref, dict):
                return None
            fk = ref.get("fact_key")
            fv = ref.get("fact_version")
            if isinstance(fv, str) and fv.isdigit():
                fv = int(fv)
            if fk is None or not isinstance(fv, int):
                return None
            if (str(fk), int(fv)) not in allowed:
                return None
            good_refs.append({"fact_key": fk, "fact_version": fv})
        c["supporting_fact_refs"] = good_refs
        # Interest safety: strip unsupported interest (explicit null policy)
        if not interest_supported and (
            c.get("interest_rate") or c.get("interest_start_date")
        ):
            c["interest_rate"] = None
            c["interest_start_date"] = None
            missing.append("利息起算与利率未由已确认事实支持，已置空待律师确认")
        try:
            ClaimProposalItem.model_validate(c)
        except ValidationError:
            return None
        cleaned_claims.append(c)
    row["claims"] = cleaned_claims
    row["missing_confirmations"] = missing
    if not row.get("overall_strategy"):
        row["overall_strategy"] = "基于已确认事实的诉讼请求方向建议"
    return ClaimDirectionProposal.model_validate(row)


def _build_prompt(
    inp: ClaimDirectionInput,
    facts: list[FactView],
    parties: list[PartyView],
) -> str:
    fact_rows = [
        {
            "fact_key": str(f.fact_key),
            "fact_version": f.fact_version,
            "statement": f.statement,
            "amounts_mentioned": f.amounts_mentioned,
        }
        for f in facts
    ]
    party_rows = [
        {
            "party_key": str(p.party_key),
            "role": p.role,
            "name": p.name,
            "party_type": p.party_type,
            "version": p.version,
        }
        for p in parties
    ]
    return (
        f"case_id={inp.case_id}\n"
        "仅可引用下列 CONFIRMED Fact（含精确 version）与当事人。\n"
        f"parties={json.dumps(party_rows, ensure_ascii=False)}\n"
        f"facts={json.dumps(fact_rows, ensure_ascii=False)}\n"
        "请输出 JSON。"
    )
