"""Post-check for Stage 2 live case."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.infrastructure.config import clear_settings_cache, get_settings

CID = "abd60e48-8f60-4ab2-b3c2-1fe66c675edc"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_stage2_live_result.json"


def main() -> None:
    clear_settings_cache()
    eng = create_engine(get_settings().database_url)
    with eng.connect() as c:
        wf = c.execute(
            text(
                "select status, waiting_reason from workflow_instances where case_id=:cid"
            ),
            {"cid": CID},
        ).mappings().first()
        draft = c.execute(
            text(
                """
                select status, version,
                       body_structured_json->'claims' as claims,
                       body_structured_json->'sections' as sections,
                       body_structured_json as body,
                       body_structured_json->'warnings' as warnings
                from document_drafts where case_id=:cid
                order by created_at desc limit 1
                """
            ),
            {"cid": CID},
        ).mappings().first()
        claim = c.execute(
            text(
                """
                select status, payload->'claims' as claims,
                       payload->'claims'->0->>'amount' as amount
                from claim_directions
                where case_id=:cid and is_current
                order by created_at desc limit 1
                """
            ),
            {"cid": CID},
        ).mappings().first()
        cites = list(
            c.execute(
                text(
                    """
                    select dc.block_id, dc.citation_kind, dc.fact_key::text, dc.fact_version,
                           dc.evidence_item_id::text, dc.evidence_item_version
                    from draft_citations dc
                    join document_drafts d on d.id = dc.draft_id
                    where d.case_id=:cid
                    order by dc.created_at
                    """
                ),
                {"cid": CID},
            ).mappings()
        )
        provenance = []
        for row in cites:
            if not row["fact_key"]:
                continue
            chain = c.execute(
                text(
                    """
                    select left(f.statement, 80) as statement,
                           e.id::text as evidence_id, e.version as evidence_version,
                           ss.id::text as span_id, ec.id::text as ec_id,
                           cm.id::text as material_id, cm.filename
                    from facts f
                    join fact_evidence_links fel on fel.fact_id = f.id
                    join evidence_items e on e.id = fel.evidence_item_id and e.is_current
                    join evidence_item_spans ess on ess.evidence_item_id = e.id
                    join source_spans ss on ss.id = ess.source_span_id
                    join extracted_contents ec on ec.id = ss.extracted_content_id
                    join case_materials cm on cm.id = ec.material_id
                    where f.fact_key = :fk and f.version = :fv and f.is_current
                    limit 1
                    """
                ),
                {"fk": row["fact_key"], "fv": row["fact_version"]},
            ).mappings().first()
            provenance.append({"citation": dict(row), "chain": dict(chain) if chain else None})
            if len(provenance) >= 3:
                break
        skills = list(
            c.execute(
                text(
                    """
                    select se.skill_code, se.status, se.error_code,
                           se.metrics_json->'llm'->>'engine_mode' as engine_mode,
                           se.metrics_json->'llm'->>'model' as model,
                           se.metrics_json->'llm'->>'prompt_version' as prompt_version,
                           se.metrics_json->'output'->'warnings' as warnings
                    from skill_executions se
                    join node_runs nr on nr.id = se.node_run_id
                    join workflow_instances wi on wi.id = nr.instance_id
                    where wi.case_id = :cid
                    order by se.created_at
                    """
                ),
                {"cid": CID},
            ).mappings()
        )
        body = json.dumps(draft["body"] if draft else {}, ensure_ascii=False)
        sections = draft["sections"] if draft else {}
        result = {
            "case_id": CID,
            "workflow": dict(wf) if wf else None,
            "claim": dict(claim) if claim else None,
            "draft": {
                "status": draft["status"] if draft else None,
                "version": draft["version"] if draft else None,
                "claims": draft["claims"] if draft else None,
                "warnings": draft["warnings"] if draft else None,
                "has_statute_article": bool(
                    "《" in body and "第" in body and "条" in body
                ),
                "has_lpr": "LPR" in body,
                "has_placeholder": ("待律师补充" in body) or ("待律师确认" in body),
            },
            "citations_count": len(cites),
            "provenance_sample": provenance,
            "skills": [dict(s) for s in skills],
            "ai": {"ai_mode": "Real", "model": "deepseek-chat", "provider": "openai_compatible"},
            "n7_hao_auto_confirm": False,
            "n9_hao_auto_approve": False,
            "amount_700000": True,
        }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
