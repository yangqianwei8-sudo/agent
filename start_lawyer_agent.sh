#!/usr/bin/env bash
# DevBox / Linux — manual dev server with hot reload.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo
echo "========================================"
echo " Lawyer Case Agent 启动 (DevBox/Linux)"
echo "========================================"
echo

if [[ ! -x "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "[错误] 未找到虚拟环境 .venv"
  echo "请先执行:"
  echo "  python3.11 -m venv .venv"
  echo "  .venv/bin/pip install -e \".[dev]\""
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "[错误] 未找到 .env"
  echo "请复制 .env.example 为 .env，并填写 LLM_API_KEY 等配置。"
  exit 1
fi

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8080}"

if command -v ss >/dev/null 2>&1 && ss -tln | grep -q ":${PORT} "; then
  echo "[提示] 端口 ${PORT} 已被占用。"
  echo "DevBox 上通常已有 supervise-uvicorn 在后台运行，可直接打开浏览器："
  echo "  http://127.0.0.1:${PORT}"
  echo
  echo "若需手动重启后台服务："
  echo "  pkill -f supervise-uvicorn.sh || true"
  echo "  nohup ./deploy/supervise-uvicorn.sh >/dev/null 2>&1 &"
  echo
  echo "若要用本脚本做热重载开发，请先停止占用 ${PORT} 的进程。"
  exit 1
fi

echo "正在启动开发服务器（--reload）..."
echo "浏览器请打开: http://127.0.0.1:${PORT}"
echo "停止服务: 在本窗口按 Ctrl+C"
echo

exec "$ROOT/.venv/bin/uvicorn" backend.main:app --reload --host "$HOST" --port "$PORT"
