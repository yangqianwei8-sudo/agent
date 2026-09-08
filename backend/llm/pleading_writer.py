"""LLM Pleading Writer engine — structured draft DTO; claim mirror enforced."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import PLEADING_WRITER_PROMPT_VERSION, PLEADING_WRITER_SYSTEM
from backend.schemas.pleading_writer import (
    ClaimDirectionRef,
    ClaimLine,
    PleadingWriterEngineResult,
    PleadingWriterInput,
    WriterWarning,
)
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    ConfirmedFactView,
    DeterministicPleadingWriterStub,
    PartyView,
    looks_like_fabricated_law_citation,
    looks_like_invented_interest,
)


class LLMPleadingWriterEngine:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = PLEADING_WRITER_PROMPT_VERSION
        self.last_meta: dict[str, Any] = {}
        self._fallback = DeterministicPleadingWriterStub()

    def write(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
    ) -> PleadingWriterEngineResult:
        allowed_facts = {(str(f.fact_key), int(f.fact_version)) for f in facts}
        allowed_ev = {
            (str(e.evidence_item_id), int(e.evidence_item_version)) for e in evidence
        }
        user_prompt = _build_prompt(inp, parties, facts, claim, evidence)

        try:
            result = self.client.complete_json(
                system_prompt=PLEADING_WRITER_SYSTEM,
                user_prompt=user_prompt,
                schema_name="pleading_writer_v1",
            )
        except LLMError:
            raise

        self.last_meta = {
            "engine_mode": "real",
            "model": result.model,
            "provider": result.provider,
            "prompt_version": PLEADING_WRITER_PROMPT_VERSION,
            "request_id": result.request_id,
            "usage": result.usage.model_dump(),
            "latency_ms": result.latency_ms,
        }

        payload = result.parsed_json
        if not isinstance(payload, dict):
            raise LLMSchemaValidationError("writer root must be object")

        base = self._fallback.write(
            inp, parties=parties, facts=facts, claim=claim, evidence=evidence
        )

        try:
            engine_result = _assemble(
                payload,
                base=base,
                claim=claim,
                allowed_facts=allowed_facts,
                allowed_ev=allowed_ev,
            )
        except ValidationError as exc:
            raise LLMSchemaValidationError("writer schema invalid") from exc

        combined = (
            engine_result.facts_and_reasons_section
            + "\n"
            + engine_result.claims_section
            + "\n"
            + engine_result.parties_section
        )
        if looks_like_fabricated_law_citation(combined):
            raise LLMSchemaValidationError("fabricated statute citation forbidden")
        src_claims = list(claim.payload.get("claims") or [])
        src_has_interest = any(
            bool(c.get("interest_start_date") or c.get("interest_rate"))
            for c in src_claims
            if isinstance(c, dict)
        )
        if not src_has_interest and looks_like_invented_interest(combined):
            raise LLMSchemaValidationError("invented interest scheme forbidden")

        return engine_result


def _assemble(
    payload: dict[str, Any],
    *,
    base: PleadingWriterEngineResult,
    claim: ClaimDirectionView,
    allowed_facts: set[tuple[str, int]],
    allowed_ev: set[tuple[str, int]],
) -> PleadingWriterEngineResult:
    data = dict(payload)
    src_claims = list(claim.payload.get("claims") or [])

    # Claims: if LLM omitted, use deterministic mirror; if present, must match exactly
    llm_claims = data.get("claims")
    if not isinstance(llm_claims, list) or not llm_claims:
        data["claims"] = [c.model_dump(mode="json") for c in base.claims]
        data["claims_section"] = base.claims_section
    else:
        if len(llm_claims) != len(src_claims):
            raise LLMSchemaValidationError("claims count mismatch with ClaimDirection")
        for out, src in zip(llm_claims, src_claims, strict=True):
            if not isinstance(out, dict):
                raise LLMSchemaValidationError("invalid claim line")
            if str(out.get("claim_type")) != str(src.get("claim_type")):
                raise LLMSchemaValidationError("claim_type mismatch")
            src_amount = src.get("amount")
            out_amount = out.get("amount")
            if src_amount is None and out_amount is not None:
                raise LLMSchemaValidationError("invented amount")
            if src_amount is not None:
                if out_amount is None or abs(float(out_amount) - float(src_amount)) > 1e-9:
                    raise LLMSchemaValidationError("amount mismatch with ClaimDirection")
                if str(out.get("currency") or "") != str(src.get("currency") or ""):
                    raise LLMSchemaValidationError("currency mismatch")
        # Prefer deterministic rendered claim text for exact amount digits
        data["claims"] = [c.model_dump(mode="json") for c in base.claims]
        data["claims_section"] = base.claims_section

    data["used_claim_direction_ref"] = ClaimDirectionRef(
        claim_direction_key=claim.claim_direction_key,
        claim_direction_version=claim.claim_direction_version,
    ).model_dump(mode="json")

    data.setdefault("title", base.title)
    data.setdefault("parties_section", base.parties_section)
    data.setdefault("court_section", base.court_section)
    data.setdefault("signature_section", base.signature_section)
    data.setdefault("evidence_section", base.evidence_section)

    blocks = data.get("fact_blocks")
    if not isinstance(blocks, list) or not blocks:
        data["fact_blocks"] = [b.model_dump(mode="json") for b in base.fact_blocks]
        data["facts_and_reasons_section"] = base.facts_and_reasons_section
    else:
        cleaned_blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            b = dict(block)
            frefs = _clean_fact_refs(b.get("fact_refs"), allowed_facts)
            if not frefs:
                continue
            b["fact_refs"] = frefs
            b["evidence_refs"] = _clean_ev_refs(b.get("evidence_refs"), allowed_ev)
            cleaned_blocks.append(b)
        if not cleaned_blocks:
            raise LLMSchemaValidationError("no valid fact_blocks after ref filter")
        data["fact_blocks"] = cleaned_blocks
        if not data.get("facts_and_reasons_section"):
            data["facts_and_reasons_section"] = base.facts_and_reasons_section

    edir = data.get("evidence_directory")
    if not isinstance(edir, list) or not edir:
        data["evidence_directory"] = [
            e.model_dump(mode="json") for e in base.evidence_directory
        ]
    else:
        cleaned_e = []
        for item in edir:
            if not isinstance(item, dict):
                continue
            eid, ev = item.get("evidence_item_id"), item.get("evidence_item_version")
            if isinstance(ev, str) and ev.isdigit():
                ev = int(ev)
                item = {**item, "evidence_item_version": ev}
            if eid is None or not isinstance(ev, int):
                continue
            if (str(eid), int(ev)) not in allowed_ev:
                continue
            cleaned_e.append(item)
        if not cleaned_e:
            raise LLMSchemaValidationError("no valid evidence_directory refs")
        data["evidence_directory"] = cleaned_e

    used_facts: list[dict[str, Any]] = []
    seen_f: set[tuple[str, int]] = set()
    for block in data["fact_blocks"]:
        for fr in block.get("fact_refs") or []:
            key = (str(fr["fact_key"]), int(fr["fact_version"]))
            if key in seen_f:
                continue
            seen_f.add(key)
            used_facts.append(fr)
    data["used_fact_refs"] = used_facts or [
        r.model_dump(mode="json") for r in base.used_fact_refs
    ]

    used_ev: list[dict[str, Any]] = []
    seen_e: set[tuple[str, int]] = set()
    for item in data["evidence_directory"]:
        key = (str(item["evidence_item_id"]), int(item["evidence_item_version"]))
        if key in seen_e:
            continue
        seen_e.add(key)
        used_ev.append(
            {
                "evidence_item_id": item["evidence_item_id"],
                "evidence_item_version": item["evidence_item_version"],
            }
        )
    data["used_evidence_refs"] = used_ev or [
        r.model_dump(mode="json") for r in base.used_evidence_refs
    ]

    warnings = list(data.get("warnings") or [])
    if not any(
        isinstance(w, dict) and w.get("code") == "CLAIM_FACT_VERSION_PROVENANCE_GAP"
        for w in warnings
    ):
        warnings.append(
            WriterWarning(
                code="CLAIM_FACT_VERSION_PROVENANCE_GAP",
                message=(
                    "ClaimDirection.payload.supporting_fact_ids stores fact_key only; "
                    "Fact version is validated at Writer Application layer."
                ),
            ).model_dump(mode="json")
        )
    data["warnings"] = warnings

    court = str(data.get("court_section") or "")
    if "待律师" not in court and "人民法院" in court:
        data["court_section"] = base.court_section

    # Ensure ClaimLine typing via model
    data["claims"] = [
        ClaimLine.model_validate(c).model_dump(mode="json") for c in data["claims"]
    ]
    return PleadingWriterEngineResult.model_validate(data)


def _clean_fact_refs(refs: Any, allowed: set[tuple[str, int]]) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    out = []
    for fr in refs:
        if not isinstance(fr, dict):
            continue
        fk, fv = fr.get("fact_key"), fr.get("fact_version")
        if isinstance(fv, str) and fv.isdigit():
            fv = int(fv)
        if fk is None or not isinstance(fv, int):
            continue
        if (str(fk), int(fv)) not in allowed:
            continue
        out.append({"fact_key": fk, "fact_version": fv})
    return out


def _clean_ev_refs(refs: Any, allowed: set[tuple[str, int]]) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    out = []
    for er in refs:
        if not isinstance(er, dict):
            continue
        eid, ev = er.get("evidence_item_id"), er.get("evidence_item_version")
        if isinstance(ev, str) and ev.isdigit():
            ev = int(ev)
        if eid is None or not isinstance(ev, int):
            continue
        if (str(eid), int(ev)) not in allowed:
            continue
        out.append({"evidence_item_id": eid, "evidence_item_version": ev})
    return out


def _build_prompt(
    inp: PleadingWriterInput,
    parties: list[PartyView],
    facts: list[ConfirmedFactView],
    claim: ClaimDirectionView,
    evidence: list[AcceptedEvidenceView],
) -> str:
    party_rows = [
        {
            "party_key": str(p.party_key),
            "role": p.role,
            "name": p.name,
            "party_type": p.party_type,
            "version": p.version,
            "identifiers_json": p.identifiers_json or {},
        }
        for p in parties
    ]
    fact_rows = [
        {
            "fact_key": str(f.fact_key),
            "fact_version": f.fact_version,
            "statement": f.statement,
            "evidence_refs": [
                {
                    "evidence_item_id": str(r.evidence_item_id),
                    "evidence_item_version": r.evidence_item_version,
                }
                for r in f.evidence_refs
            ],
        }
        for f in facts
    ]
    ev_rows = [
        {
            "evidence_item_id": str(e.evidence_item_id),
            "evidence_item_version": e.evidence_item_version,
            "number": e.number,
            "title": e.title,
            "summary": e.summary,
            "category": e.category,
        }
        for e in evidence
    ]
    return (
        f"case_id={inp.case_id}\n"
        f"claim_direction_key={claim.claim_direction_key}\n"
        f"claim_direction_version={claim.claim_direction_version}\n"
        f"claim_payload={json.dumps(claim.payload, ensure_ascii=False)}\n"
        f"parties={json.dumps(party_rows, ensure_ascii=False)}\n"
        f"facts={json.dumps(fact_rows, ensure_ascii=False)}\n"
        f"evidence={json.dumps(ev_rows, ensure_ascii=False)}\n"
        "claims 必须与 claim_payload.claims 完全一致（类型/金额/币种）。请输出 JSON。"
    )
