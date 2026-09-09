# 每天怎么启动（1 分钟）

面向：律师本人。不需要 Cursor，不需要写代码。

## 1. 确认 PostgreSQL 在运行

本机服务名通常为 `postgresql-x64-17`。可在「服务」里确认状态为“正在运行”。

## 2. 打开 PowerShell

```powershell
cd "C:\Users\37108\Desktop\律师助理"
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

或双击项目根目录的 `start_lawyer_agent.bat`。

## 3. 打开浏览器

地址：

```text
http://127.0.0.1:8000
```

## 4. 停止系统

在启动窗口按 `Ctrl + C`。

---

更完整说明见 [USER_GUIDE.md](USER_GUIDE.md)。
