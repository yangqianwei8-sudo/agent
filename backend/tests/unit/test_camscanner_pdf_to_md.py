"""Unit tests for CamScanner PDF → Markdown converter (mocked CLI)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.infrastructure.config import Settings
from backend.tools.camscanner_pdf_to_md import CamScannerPdfToMd
from backend.tools.dto import ParseFailureDTO, ParseSuccessDTO


def test_convert_success_parses_markdown(tmp_path: Path) -> None:
    settings = Settings(
        camscanner_cli_enabled=True,
        camscanner_cli_cwd=str(tmp_path),
        camscanner_cli_timeout_seconds=30,
    )

    def fake_runner(pdf_path: Path, md_path: Path) -> subprocess.CompletedProcess[str]:
        assert pdf_path.is_file()
        md_path.write_text("# 判决书\n\n原告请求支付 **100000** 元。\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["camscanner-cli"], returncode=0, stdout="ok", stderr=""
        )

    tool = CamScannerPdfToMd(settings, runner=fake_runner)
    result = tool.convert(b"%PDF-1.4 fake", source_name="判决.pdf")
    assert isinstance(result, ParseSuccessDTO)
    assert result.extraction_method == "camscanner_pdf_to_md"
    assert "# 判决书" in result.full_text
    assert "100000" in result.full_text
    assert result.meta.get("converter") == "camscanner-cli"


def test_convert_disabled() -> None:
    settings = Settings(camscanner_cli_enabled=False)
    result = CamScannerPdfToMd(settings).convert(b"%PDF", source_name="a.pdf")
    assert isinstance(result, ParseFailureDTO)
    assert result.error_code == "CAMSCANNER_DISABLED"
    assert result.needs_ocr is True


def test_convert_cli_nonzero_exit_auth_hint() -> None:
    settings = Settings(camscanner_cli_enabled=True)

    def fake_runner(pdf_path: Path, md_path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["camscanner-cli"],
            returncode=1,
            stdout="",
            stderr="unauthorized: please login",
        )

    result = CamScannerPdfToMd(settings, runner=fake_runner).convert(
        b"%PDF", source_name="a.pdf"
    )
    assert isinstance(result, ParseFailureDTO)
    assert result.error_code == "CAMSCANNER_CLI_FAILED"
    assert "auth login" in result.error_detail


def test_build_command_prefers_node_run_js(tmp_path: Path) -> None:
    # Simulate project layout: package.json + node_modules/camscanner-cli/bin/run.js
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    run_js = tmp_path / "node_modules" / "camscanner-cli" / "bin" / "run.js"
    run_js.parent.mkdir(parents=True)
    run_js.write_text("// stub", encoding="utf-8")
    fake_node = tmp_path / "node.exe"
    fake_node.write_bytes(b"MZ")

    settings = Settings(
        camscanner_cli_enabled=True,
        camscanner_cli_cwd=str(tmp_path),
        camscanner_cli_command="",
    )
    tool = CamScannerPdfToMd(settings)
    tool._find_node = lambda: fake_node  # type: ignore[method-assign]
    cmd = tool._build_command(tmp_path / "a.pdf", tmp_path / "a.md")
    assert cmd[0] == str(fake_node)
    assert cmd[1] == str(run_js)
    assert "pdf" in cmd and "convert" in cmd and "--format" in cmd and "md" in cmd
