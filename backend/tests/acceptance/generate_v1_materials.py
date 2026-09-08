"""Generate high-fidelity V1 acceptance case materials (PDF + DOCX).

Synthetic acceptance case data — fictional entities only
(e.g. 智图设计优化咨询有限公司 / 星海地产开发有限公司).
"""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures_v1"

# Pad so PdfParser scan heuristic (min ~200 non-ws chars) does not force OCR.
_PAD = (
    "本文件为现场验收样本材料正文，包含足以通过文本层抽取阈值的内容。"
    "服务范围、付款安排与催告过程均以书面记载为准，供后续证据整理与事实候选使用。"
    "当事人名称、金额数字与交付事实均来自原始材料记载，不得由系统擅自补造。"
    "本段专门用于满足抽取工具对非空白字符数量的最低阈值，避免短页被误判为扫描件。"
)


def ensure_v1_materials() -> dict[str, Path]:
    """Write acceptance materials to disk; return name → path map."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_cid_font()
    paths = {
        "contract": _pdf(
            FIXTURES_DIR / "01_consulting_contract.pdf",
            [
                "设计优化 / 精细化审图咨询服务合同",
                "甲方（委托方）：星海地产开发有限公司",
                "乙方（受托方）：智图设计优化咨询有限公司",
                "双方于二千零二十五年三月一日签订本合同。",
                "合同约定咨询服务费总价为1000000元。",
                "服务范围：方案优化与施工图精细化审图咨询。",
                "付款方式：签约后支付部分，成果交付后结清余款。",
                _PAD,
            ],
        ),
        "delivery": _docx(
            FIXTURES_DIR / "02_delivery_receipt.docx",
            [
                "成果交付与签收记录",
                "智图设计优化咨询有限公司已向星海地产开发有限公司交付设计优化成果。",
                "交付日期：二千零二十五年六月十五日。",
                "签收人确认已收到全部约定成果文件。",
                _PAD,
            ],
        ),
        "invoice": _pdf(
            FIXTURES_DIR / "03_payment_notice.pdf",
            [
                "付款通知书",
                "致：星海地产开发有限公司",
                "根据咨询服务合同，现通知贵司支付咨询服务费。",
                "合同总价1000000元，请按约定支付。",
                "开票主体：智图设计优化咨询有限公司",
                _PAD,
            ],
        ),
        "payment": _pdf(
            FIXTURES_DIR / "04_payment_voucher.pdf",
            [
                "银行付款凭证",
                "付款人：星海地产开发有限公司",
                "收款人：智图设计优化咨询有限公司",
                "被告已支付300000元。",
                "付款用途：咨询服务费首期",
                _PAD,
            ],
        ),
        "demand": _docx(
            FIXTURES_DIR / "05_demand_letter.docx",
            [
                "催款函 / 沟通记录",
                "智图设计优化咨询有限公司致函星海地产开发有限公司。",
                "请尽快支付剩余咨询服务费。",
                "合同总价1000000元，已支付300000元，剩余应支付700000元。",
                "此前多次邮件催告未获足额支付。",
                _PAD,
            ],
        ),
        "conflict": _pdf(
            FIXTURES_DIR / "06_conflict_memo.pdf",
            [
                "内部沟通备忘（含冲突信息）",
                "对方口头表示：被告已支付500000元。",
                "该说法与银行付款凭证记载的300000元不一致，待核实。",
                "请律师注意证据冲突，不得自行采信一方。",
                _PAD,
            ],
        ),
    }
    return paths


def _ensure_cid_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    name = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    except Exception:  # noqa: BLE001
        pass
    return name


def _pdf(path: Path, lines: list[str]) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    font = _ensure_cid_font()
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(font, 11)
    y = 800
    for line in lines:
        # wrap long lines
        while len(line) > 42:
            c.drawString(72, y, line[:42])
            line = line[42:]
            y -= 18
            if y < 72:
                c.showPage()
                c.setFont(font, 11)
                y = 800
        c.drawString(72, y, line)
        y -= 18
        if y < 72:
            c.showPage()
            c.setFont(font, 11)
            y = 800
    c.save()
    return path


def _docx(path: Path, paragraphs: list[str]) -> Path:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(path)
    return path


if __name__ == "__main__":
    out = ensure_v1_materials()
    from backend.tools.docx_parser import DocxParser
    from backend.tools.pdf_parser import PdfParser

    for k, p in out.items():
        data = p.read_bytes()
        if p.suffix == ".pdf":
            r = PdfParser().parse(data)
            ok = type(r).__name__
            text = getattr(r, "full_text", "")[:60]
            print(k, ok, text.replace("\n", " "))
        else:
            r = DocxParser().parse(data)
            print(k, type(r).__name__, getattr(r, "full_text", "")[:60].replace("\n", " "))
