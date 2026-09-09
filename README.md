# 诉讼案件工作 Agent V1

## Lawyer Case Agent V1

- **Status:** V1 Acceptance PASS
- **Core workflow:** CLOSED LOOP
- **Acceptance regression:** `backend/tests/acceptance/test_v1_live_acceptance.py`

Documents:

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — 律师日常办案使用手册
- [docs/QUICK_START.md](docs/QUICK_START.md) — 每天启动（1 分钟）
- [docs/V1_FINAL_ACCEPTANCE.md](docs/V1_FINAL_ACCEPTANCE.md)
- [docs/V1_TECH_DEBT.md](docs/V1_TECH_DEBT.md)
- [docs/V1_1_BACKLOG.md](docs/V1_1_BACKLOG.md)

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

Acceptance (synthetic case data):

```bash
pytest backend/tests/acceptance -q
```
