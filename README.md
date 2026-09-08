# 诉讼案件工作 Agent V1

当前进度：**Phase 4 Material / Extraction**（终点：SourceSpan）

## 快速开始

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
alembic upgrade head
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## 测试

```bash
ruff check backend
pytest -q
```

## 阶段边界

- Phase 4：CaseMaterial → ExtractedContent → SourceSpan（含 Pdf/Docx/OCR stub）
- **不做**：EvidenceItem、Organizer、LLM、Agent、前端
