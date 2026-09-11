FROM python:3.11-slim-bookworm

WORKDIR /app

ARG GIT_SHA=unknown
ARG BUILD_TIME=
ENV GIT_SHA=${GIT_SHA}
ENV IMAGE_TAG=${GIT_SHA}
ENV BUILD_TIME=${BUILD_TIME}
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY autonomous_dev ./autonomous_dev
COPY alembic ./alembic
COPY alembic.ini ./

RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir -U pip \
    && /app/.venv/bin/pip install --no-cache-dir -e .

RUN echo "${GIT_SHA}" > /app/.git_sha

EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
