"""Convert scanned PDF to Markdown via CamScanner CLI.

Docs: https://www.camscanner.com/agent-docs/zh/platforms/for-agents/cli/

  camscanner-cli pdf convert paper.pdf --format md -o paper.md

No Repository / Domain writes. Auth is managed by the CLI (OAuth login).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from backend.infrastructure.config import Settings, get_settings
from backend.tools.dto import ParseFailureDTO, ParseResultDTO, ParseSuccessDTO
from backend.tools.markdown_parser import MarkdownParser

logger = logging.getLogger(__name__)


class CamScannerPdfToMd:
    EXTRACTION_METHOD = "camscanner_pdf_to_md"
    EXTRACTION_VERSION = "v1"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        markdown_parser: MarkdownParser | None = None,
        runner: Callable[[Path, Path], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.markdown_parser = markdown_parser or MarkdownParser()
        self._runner = runner or self._run_cli

    def convert(self, data: bytes, *, source_name: str = "scan.pdf") -> ParseResultDTO:
        if not self.settings.camscanner_cli_enabled:
            return ParseFailureDTO(
                error_code="CAMSCANNER_DISABLED",
                error_detail="CamScanner CLI 转换未启用（CAMSCANNER_CLI_ENABLED=false）",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
            )
        if not data:
            return ParseFailureDTO(
                error_code="CAMSCANNER_EMPTY_INPUT",
                error_detail="empty PDF bytes",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
            )
        max_bytes = self.settings.camscanner_max_upload_bytes
        if len(data) > max_bytes:
            return ParseFailureDTO(
                error_code="CAMSCANNER_FILE_TOO_LARGE",
                error_detail=(
                    f"PDF exceeds CamScanner limit ({max_bytes} bytes). "
                    "文档限制约 40MB。"
                ),
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
            )

        safe_stem = Path(source_name).stem or "scan"
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in safe_stem)[:80]
        tmp_parent = self._temp_parent()
        try:
            with tempfile.TemporaryDirectory(prefix="camscanner_", dir=tmp_parent) as tmp:
                tmp_path = Path(tmp)
                pdf_path = tmp_path / f"{safe_stem}.pdf"
                md_path = tmp_path / f"{safe_stem}.md"
                try:
                    pdf_path.write_bytes(data)
                except OSError as exc:
                    return ParseFailureDTO(
                        error_code="CAMSCANNER_TEMP_WRITE_FAILED",
                        error_detail=(
                            f"写入临时文件失败（{exc}）。"
                            "请清理磁盘空间，或在 .env 设置 CAMSCANNER_TEMP_DIR "
                            "到有空间的盘符（如 D:\\lawyer-agent-temp）。"
                        ),
                        extraction_method=self.EXTRACTION_METHOD,
                        extraction_version=self.EXTRACTION_VERSION,
                        needs_ocr=True,
                    )

                try:
                    completed = self._runner(pdf_path, md_path)
                except FileNotFoundError as exc:
                    return ParseFailureDTO(
                        error_code="CAMSCANNER_CLI_NOT_FOUND",
                        error_detail=(
                            f"找不到 CamScanner CLI（{exc}）。"
                            "请先安装 Node.js，并在项目目录执行: npm install camscanner-cli"
                        ),
                        extraction_method=self.EXTRACTION_METHOD,
                        extraction_version=self.EXTRACTION_VERSION,
                        needs_ocr=True,
                    )
                except subprocess.TimeoutExpired:
                    return ParseFailureDTO(
                        error_code="CAMSCANNER_TIMEOUT",
                        error_detail=(
                            f"CamScanner CLI 超时"
                            f"（>{self.settings.camscanner_cli_timeout_seconds}s）"
                        ),
                        extraction_method=self.EXTRACTION_METHOD,
                        extraction_version=self.EXTRACTION_VERSION,
                        needs_ocr=True,
                    )

                if completed.returncode != 0:
                    err = (completed.stderr or completed.stdout or "").strip()
                    detail = err[:800] if err else f"exit code {completed.returncode}"
                    lower = detail.lower()
                    auth_hint = ""
                    if any(
                        x in lower
                        for x in ("login", "auth", "unauthorized", "未登录", "授权", "oauth")
                    ):
                        auth_hint = (
                            " 请先在本机运行: npx camscanner-cli auth login"
                            "（需浏览器完成 OAuth）。"
                        )
                    return ParseFailureDTO(
                        error_code="CAMSCANNER_CLI_FAILED",
                        error_detail=f"{detail}{auth_hint}",
                        extraction_method=self.EXTRACTION_METHOD,
                        extraction_version=self.EXTRACTION_VERSION,
                        needs_ocr=True,
                        meta={"returncode": completed.returncode},
                    )

                if not md_path.is_file():
                    candidates = list(tmp_path.glob("*.md"))
                    if not candidates:
                        return ParseFailureDTO(
                            error_code="CAMSCANNER_NO_OUTPUT",
                            error_detail="CLI 成功退出但未生成 Markdown 文件",
                            extraction_method=self.EXTRACTION_METHOD,
                            extraction_version=self.EXTRACTION_VERSION,
                            needs_ocr=True,
                        )
                    md_path = candidates[0]

                md_bytes = md_path.read_bytes()
                parsed = self.markdown_parser.parse(md_bytes, filename=md_path.name)
                if isinstance(parsed, ParseFailureDTO):
                    return ParseFailureDTO(
                        error_code=parsed.error_code,
                        error_detail=parsed.error_detail,
                        extraction_method=self.EXTRACTION_METHOD,
                        extraction_version=self.EXTRACTION_VERSION,
                        needs_ocr=True,
                        meta={"camscanner_md_parse": parsed.error_code},
                    )

                assert isinstance(parsed, ParseSuccessDTO)
                meta = {
                    **(parsed.meta or {}),
                    "converter": "camscanner-cli",
                    "source_format": "pdf",
                    "target_format": "md",
                    "cli_returncode": completed.returncode,
                }
                return ParseSuccessDTO(
                    full_text=parsed.full_text,
                    page_count=parsed.page_count,
                    layout_json=parsed.layout_json,
                    spans=parsed.spans,
                    extraction_method=self.EXTRACTION_METHOD,
                    extraction_version=self.EXTRACTION_VERSION,
                    meta=meta,
                )
        except OSError as exc:
            return ParseFailureDTO(
                error_code="CAMSCANNER_TEMP_DIR_FAILED",
                error_detail=(
                    f"无法创建临时目录（{exc}）。"
                    "请清理 C 盘空间，或在 .env 设置 CAMSCANNER_TEMP_DIR=D:\\lawyer-agent-temp"
                ),
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
            )

    def _temp_parent(self) -> str | None:
        configured = (self.settings.camscanner_temp_dir or "").strip()
        if configured:
            path = Path(configured)
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        return None

    def _run_cli(self, pdf_path: Path, md_path: Path) -> subprocess.CompletedProcess[str]:
        cmd = self._build_command(pdf_path, md_path)
        cwd = str(self._resolve_cwd())
        env = self._cli_env()
        logger.info("CamScanner CLI: %s (cwd=%s)", " ".join(cmd), cwd)
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=self.settings.camscanner_cli_timeout_seconds,
            env=env,
            check=False,
        )

    def _resolve_cwd(self) -> Path:
        configured = (self.settings.camscanner_cli_cwd or "").strip()
        if configured:
            return Path(configured)
        # Prefer repo root that contains package.json (camscanner-cli install location).
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            if (parent / "package.json").is_file():
                return parent
        return Path.cwd()

    def _find_node(self) -> Path | None:
        """Locate node.exe even when the backend process PATH is incomplete."""
        which = shutil.which("node")
        if which:
            return Path(which)
        candidates = [
            Path(r"C:\Program Files\nodejs\node.exe"),
            Path(r"C:\Program Files (x86)\nodejs\node.exe"),
            Path.home() / "AppData" / "Roaming" / "nvm" / "nodejs" / "node.exe",
        ]
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        node = self._find_node()
        if node is None:
            return env
        node_dir = str(node.parent)
        path_key = "Path" if "Path" in env and "PATH" not in env else "PATH"
        current = env.get(path_key, "")
        parts = [p for p in current.split(os.pathsep) if p]
        if node_dir.lower() not in {p.lower() for p in parts}:
            env[path_key] = node_dir + os.pathsep + current
        return env

    def _build_command(self, pdf_path: Path, md_path: Path) -> list[str]:
        """Prefer `node run.js` so Windows .cmd wrappers do not lose node PATH."""
        args_tail = [
            "pdf",
            "convert",
            str(pdf_path),
            "--format",
            "md",
            "-o",
            str(md_path),
        ]

        configured = (self.settings.camscanner_cli_command or "").strip()
        if configured:
            # Allow absolute node + script, or a wrapper path.
            parts = configured.split()
            if len(parts) == 1 and parts[0].lower().endswith((".cmd", ".bat", ".exe")):
                return [parts[0], *args_tail]
            if len(parts) == 1 and parts[0].lower().endswith(".js"):
                node = self._find_node()
                if node is None:
                    raise FileNotFoundError("node.exe not found on PATH or default install paths")
                return [str(node), parts[0], *args_tail]
            return [*parts, *args_tail]

        cwd = self._resolve_cwd()
        run_js = cwd / "node_modules" / "camscanner-cli" / "bin" / "run.js"
        node = self._find_node()
        if run_js.is_file() and node is not None:
            return [str(node), str(run_js), *args_tail]

        local_cmd = cwd / "node_modules" / ".bin" / "camscanner-cli.cmd"
        if local_cmd.is_file():
            return [str(local_cmd), *args_tail]
        local_bin = cwd / "node_modules" / ".bin" / "camscanner-cli"
        if local_bin.is_file():
            return [str(local_bin), *args_tail]

        binary = shutil.which("camscanner-cli")
        if binary:
            return [binary, *args_tail]

        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if npx and node is not None:
            return [npx, "camscanner-cli", *args_tail]

        raise FileNotFoundError(
            "node/camscanner-cli not found; install Node.js and npm install camscanner-cli"
        )
