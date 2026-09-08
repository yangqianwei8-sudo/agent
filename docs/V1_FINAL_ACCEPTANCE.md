# Lawyer Case Agent V1 — Final Acceptance Record

**Status:** `V1 ACCEPTANCE PASS` · **Baseline:** FROZEN  
**Date:** 2026-09-09  

This document is the SSOT for restoring project context after V1 freeze.  
It does not authorize Phase 10 or V1.1 implementation.

---

## 1. Product Definition

Lawyer Case Agent V1 是一个面向律师的、有状态、可暂停/恢复、以证据可追溯和律师人工确认为硬约束的民事诉讼案件工作 Agent。

**不是：**

- 法律问答机器人
- 一键起诉状生成器
- 自主律师 Agent
- 法院自动提交系统

---

## 2. Phase Gate Summary

| Phase | Name | Result |
|------:|------|--------|
| 1 | Engineering Skeleton | PASS |
| 2 | DB + Domain | PASS |
| 3 | Workflow Runtime | PASS |
| 4 | Material / Extraction | PASS |
| 5 | Evidence Organizer | PASS |
| 6 | Case Analyst | PASS |
| 7 | Claim Direction | PASS* |
| 8 | Pleading Writer | PASS |
| 9 | Case Agent / Conversation | PASS |
| — | **V1 Live Acceptance** | **PASS** |
| — | **V1 Core Workflow** | **CLOSED LOOP** |

\* Known debt: ClaimDirection → Fact version provenance gap (see `V1_TECH_DEBT.md` D-022).  
Do **not** reopen Phase 7 to “fix” it in V1.

---

## 3. V1 Main Path

```text
Original Material
→ CaseMaterial
→ ExtractedContent
→ SourceSpan
→ EvidenceItem
→ Lawyer ACCEPT / EXCLUDE
→ Fact Candidate
→ Lawyer CONFIRM / REJECT
→ ClaimDirection Proposal
→ Lawyer CONFIRM / AMEND
→ PleadingWriter
→ DocumentDraft
→ DraftCitation
→ Lawyer APPROVE
→ Workflow SUCCEEDED
```

---

## 4. Architecture

```text
Conversation
↓
CaseAgent
↓
Intent / Command
↓
Workflow Runtime
↓
Application Services
↓
Skills / Domain Services
↓
DB / Audit
```

**Hard separations:**

```text
Agent ≠ Workflow
Agent ≠ Skill
Workflow ≠ Domain
```

Workflow (`WorkflowInstance` + `NodeRun`) is the **only** process-progress source of truth.  
Agent must not maintain a parallel stage machine.

---

## 5. Human Gates (frozen)

| Node | Gate |
|------|------|
| N3 | Evidence confirmation |
| N5 | Party confirmation |
| N6 | Fact confirmation |
| N7 | ClaimDirection confirmation |
| N9 | Draft review |

**Principle:** `CONTINUE` / `RESUME` must never auto-bypass a Human Gate.

---

## 6. Workflow Nodes (PLEADING_PREP)

```text
N0_CREATE
N1_PARSE
N2_ORGANIZE
N3_CONFIRM_EVIDENCE
N4_ANALYZE
N5_CONFIRM_PARTIES
N6_CONFIRM_FACTS
N7_CONFIRM_CLAIMS
N8_WRITE
N9_REVIEW
```

Migration / template head reference: `c4d5e6f7a8b9`.

---

## 7. Provenance Model

### Evidence

```text
EvidenceItem(version)
→ SourceSpan
→ ExtractedContent
→ CaseMaterial
→ original bytes
```

### Fact

```text
Fact(key+version)
→ FactEvidenceLink
→ EvidenceItem(version)
→ SourceSpan
→ Material
```

### Draft

```text
DocumentDraft
→ DraftCitation
→ Fact(key+version)
→ EvidenceItem(version)
→ SourceSpan
→ Material
```

---

## 8. Writer Safety (frozen)

Writer may only use:

- CONFIRMED + non-stale Party
- CONFIRMED + non-stale Fact
- CONFIRMED + non-stale ClaimDirection
- ACCEPTED EvidenceItem(version)

Writer must **not**:

- invent facts
- invent claims
- change amounts
- invent interest / LPR
- fabricate specific statutes
- auto-APPROVE drafts

---

## 9. Agent Safety (frozen)

- Agent does not directly UPDATE Domain tables
- Agent does not own a second state machine
- Agent does not bypass Human Gates
- Agent does not persist chain-of-thought
- Target resolution is case-scoped
- Ambiguous targets are refused (no guessing)

---

## 10. V1 Acceptance Evidence

Environment:

```text
Python: 3.12.10
DB: PostgreSQL
Migration head: c4d5e6f7a8b9
LLM: none (deterministic / acceptance stubs)
```

Live acceptance sample (synthetic consulting-fee dispute):

```text
Materials: 6
ExtractedContent: 6
SourceSpan: 15

EvidenceItem: 15
Accepted: 14
Excluded: 1

Facts: 14
Confirmed: 3
Rejected: 11

ClaimDirection: 1
Claim amount: 700000 CNY

DocumentDraft: 1
DraftCitation: 3

Workflow final status: SUCCEEDED
```

Quality gate at freeze:

```text
pytest: 148 passed, 0 skipped, 1 warning
ruff check backend: passed
```

Warning: Starlette/httpx TestClient deprecation — **KNOWN NON-BLOCKING** (D-003).

Regression entrypoint:

```text
backend/tests/acceptance/test_v1_live_acceptance.py
```

---

## 11. Related Documents

- [`docs/V1_TECH_DEBT.md`](V1_TECH_DEBT.md)
- [`docs/V1_1_BACKLOG.md`](V1_1_BACKLOG.md)

**Do not start Phase 10 or V1.1 from this freeze commit without an explicit new mandate.**
