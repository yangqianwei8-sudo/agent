@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo  Lawyer Case Agent 启动
echo ========================================
echo.

if not exist ".venv\Scripts\uvicorn.exe" (
  echo [错误] 未找到虚拟环境 .venv
  echo 请先按 docs\USER_GUIDE.md 完成首次安装。
  pause
  exit /b 1
)

if not exist ".env" (
  echo [错误] 未找到 .env
  echo 请复制 .env.example 为 .env，并填写 LLM_API_KEY 等配置。
  pause
  exit /b 1
)

echo 正在启动服务...
echo 浏览器请打开: http://127.0.0.1:8000
echo 停止服务: 在本窗口按 Ctrl+C
echo.

".venv\Scripts\uvicorn.exe" backend.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
pause
