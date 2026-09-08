# Lawyer Case Agent V1 — Technical Debt Register

Confirmed debts only. Not a wishlist.  
V1 remains ACCEPTANCE PASS with these items open.

---

## D-001

PartyChanged → Fact stale partially depends on name matching.

## D-002

EvidenceItem dual identity: physical `row_id` + stable `id`/`version`.

## D-003

TestClient / httpx Starlette deprecation warning.  
**KNOWN NON-BLOCKING WARNING** — do not upgrade casually for V1 freeze.

## D-004

Short-text PDF scan heuristic may false-positive as “needs OCR”.

## D-005

Image OCR is a stub.

## D-006

Extraction method name `pdfplumber_text` vs actual pypdf implementation mismatch.

## D-008

Domain `create_evidence_item` still allows empty spans; Organizer path enforces spans.

## D-009

Evidence Organizer has no real LLM (deterministic / scripted engines).

## D-010

Evidence `category` remains a free string (no controlled vocabulary).

## D-012

`fact_type` is not persisted on Fact domain rows.

## D-013

Legal-conclusion keyword firewall is coarse.

## D-016

Issue / LegalTheory lack a formal lawyer confirmation flow.

## D-017

Timeline consumers must respect Fact / layer status (easy to misuse).

## D-022 — ClaimDirection → Fact version provenance gap

**Severity:** structural (accepted for V1)

Domain:

```text
ClaimDirection.payload.supporting_fact_ids
→ stores fact_key only
→ does NOT natively store fact_version
```

Current mitigations:

- Application inputs require explicit `fact_key + fact_version`
- SkillExecution records versioned refs
- Writer records actually used fact refs
- Writer emits `CLAIM_FACT_VERSION_PROVENANCE_GAP` warning

Future (V1.1+ only):

```text
ClaimDirectionFactLink
or equivalent version-pinned relation
```

**Do not reopen Phase 7 in V1 to “fix” this.**

## D-023

Draft stale still has coarse-grained dependency invalidation.

## D-024

Intent Router is deterministic keyword matching — not production LLM.

## D-025

N1 does not orchestrate complex automatic Extraction.

## D-026

Organizer currently splits by SourceSpan; EvidenceItem count can be high (lawyer must filter).
