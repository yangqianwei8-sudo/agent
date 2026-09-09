"""Material upload for lawyer MVP — wraps Domain + Extraction only."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from backend.application.material_extraction import MaterialExtractionService
from backend.application.material_usability import MaterialUsabilityPolicy
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.infrastructure.config import get_settings
from backend.models import CaseMaterial
from backend.tools.storage import ObjectStorage

ALLOWED_EXT = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".md": "text/markdown",
}


@dataclass
class UploadResult:
    material: CaseMaterial
    extraction_status: str | None
    extraction_error: str | None
    success: bool
    usable: bool
    message: str
    needs_ocr: bool = False


def sanitize_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = name.strip().lstrip(".")
    name = re.sub(r"[^\w.\u4e00-\u9fff\-]+", "_", name, flags=re.UNICODE)
    if not name or name in {".", ".."}:
        raise ValidationError("invalid filename")
    if ".." in name or "/" in name or "\\" in name:
        raise ValidationError("path traversal rejected")
    return name[:180]


class MaterialUploadService:
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.storage = storage or ObjectStorage()
        self.settings = get_settings()
        self.extraction = MaterialExtractionService(session, storage=self.storage)

    def upload(
        self,
        *,
        case_id: UUID,
        filename: str,
        data: bytes,
        actor_id: UUID,
    ) -> UploadResult:
        if not data:
            raise ValidationError("empty file rejected")
        if len(data) > self.settings.max_material_bytes:
            raise ValidationError(
                f"file exceeds max_material_bytes={self.settings.max_material_bytes}"
            )
        safe = sanitize_filename(filename)
        ext = Path(safe).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise ValidationError("only .pdf, .docx and .md are supported")
        mime = ALLOWED_EXT[ext]
        digest = hashlib.sha256(data).hexdigest()
        storage_key = f"{case_id}/{uuid.uuid4().hex[:12]}_{safe}"
        self.storage.put_bytes(storage_key, data)
        material = self.domain.register_material(
            case_id=case_id,
            filename=safe,
            mime=mime,
            byte_size=len(data),
            content_hash=digest,
            storage_key=storage_key,
            created_by=actor_id,
        )
        outcome = self.extraction.extract_material(material.id, actor_id=actor_id)
        ec = outcome.extracted_content
        usable = MaterialUsabilityPolicy(self.session).is_material_usable(
            material.id, case_id=case_id
        )
        if outcome.success and usable:
            message = "文件已上传并读取成功，已进入案件材料。"
        else:
            reason = getattr(ec, "error_detail", None) if ec else None
            detail = f"\n原因：{reason}" if reason else ""
            ocr_hint = (
                "\n扫描件将尝试 CamScanner 转 Markdown；失败时请登录 CLI 后重试，"
                "或上传 .md / .docx / 带文字层 PDF。"
                if outcome.needs_ocr
                else ""
            )
            message = (
                "文件已上传，但 AI 暂时无法读取。"
                "该文件尚未进入案件材料，也不会参与证据整理和案件分析。"
                f"{detail}{ocr_hint}"
            )
        return UploadResult(
            material=material,
            extraction_status=ec.status if ec else None,
            extraction_error=getattr(ec, "error_detail", None) if ec else None,
            success=outcome.success,
            usable=usable,
            message=message,
            needs_ocr=outcome.needs_ocr,
        )
