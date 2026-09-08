# Lawyer Case Agent — V1.1 Backlog

**Backlog only. Do not implement from this freeze.**

When starting V1.1, open a new mandate. Keep LLM / Agent safety boundaries below.

---

## P0

1. ClaimDirection → Fact version provenance fix (D-022)
2. Real LLM Adapter / Intent / Skill Engine integration
3. Real case upload & material processing entrypoint
4. Word export

## P1

5. Lawyer case workbench (UI)
6. Evidence Organizer denoise / merge
7. Draft review UI
8. Precise dependency / stale graph

## P2

9. PDF export
10. Legal-basis confirmation layer
11. Legal database / RAG
12. More pleading / litigation document types

---

## Real LLM boundary (mandatory for V1.1+)

Allowed:

```text
LLM
↓
Proposal / Intent
↓
Application validation
↓
Workflow / Domain
```

Forbidden:

```text
LLM
↓
ORM
```

A real LLM must **never** be allowed to:

- directly CONFIRM Fact
- directly CONFIRM ClaimDirection
- directly APPROVE Draft
- bypass Workflow / Human Gates
