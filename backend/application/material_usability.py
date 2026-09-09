"""Material usability policy — single source of truth for the usable material pool.

CaseMaterial = upload record
ExtractedContent = parse result

Usable / Active Case Material =
  CaseMaterial (not VOID)
  + at least one ExtractedContent with status == SUCCEEDED

Upload success != usable material.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import ValidationError
from backend.models import CaseMaterial, ExtractedContent

# Soft-hide VOID materials after lawyer clears the void pool (no schema migration).
CLEARED_FROM_VOID_POOL_PREFIX = "【已从作废池清空】"


def is_cleared_from_void_pool(material: CaseMaterial) -> bool:
    reason = material.void_reason or ""
    return reason.startswith(CLEARED_FROM_VOID_POOL_PREFIX)


@dataclass(frozen=True)
class MaterialUsabilityView:
    material: CaseMaterial
    succeeded_ecs: tuple[ExtractedContent, ...]
    all_ecs: tuple[ExtractedContent, ...]
    usable: bool
    pending_reason: str | None
    status_label: str
    analysis_participation: str


class MaterialUsabilityPolicy:
    """Case-scoped usability queries. Never guesses by filename or parse_status text."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_materials(self, case_id: UUID) -> list[CaseMaterial]:
        return list(
            self.session.scalars(
                select(CaseMaterial)
                .where(CaseMaterial.case_id == case_id)
                .order_by(CaseMaterial.created_at.desc())
            )
        )

    def list_extracted_contents(self, material_id: UUID) -> list[ExtractedContent]:
        return list(
            self.session.scalars(
                select(ExtractedContent)
                .where(ExtractedContent.material_id == material_id)
                .order_by(ExtractedContent.created_at.asc())
            )
        )

    def succeeded_extracted_contents(
        self, material_id: UUID
    ) -> list[ExtractedContent]:
        return [
            ec
            for ec in self.list_extracted_contents(material_id)
            if ec.status == "SUCCEEDED"
        ]

    def is_material_usable(self, material_id: UUID, *, case_id: UUID | None = None) -> bool:
        material = self.session.get(CaseMaterial, material_id)
        if material is None:
            return False
        if case_id is not None and material.case_id != case_id:
            return False
        if material.life_status == "VOID":
            return False
        return bool(self.succeeded_extracted_contents(material_id))

    def list_usable_materials(self, case_id: UUID) -> list[CaseMaterial]:
        return [
            m
            for m in self.list_materials(case_id)
            if m.life_status != "VOID"
            and self.succeeded_extracted_contents(m.id)
        ]

    def list_unusable_materials(self, case_id: UUID) -> list[CaseMaterial]:
        """ACTIVE materials with no SUCCEEDED ExtractedContent (pending / failed parse)."""
        return [
            m
            for m in self.list_materials(case_id)
            if m.life_status != "VOID"
            and not self.succeeded_extracted_contents(m.id)
        ]

    def list_void_materials(self, case_id: UUID) -> list[CaseMaterial]:
        return [
            m
            for m in self.list_materials(case_id)
            if m.life_status == "VOID" and not is_cleared_from_void_pool(m)
        ]

    def get_usable_extracted_contents(self, case_id: UUID) -> list[ExtractedContent]:
        """Return the explicitly selected SUCCEEDED EC per usable material.

        Reuses Agent/Organizer rule: exactly one SUCCEEDED EC per material.
        Multiple SUCCEEDED versions require explicit extracted_content_ids elsewhere.
        """
        out: list[ExtractedContent] = []
        for material in self.list_usable_materials(case_id):
            ecs = self.succeeded_extracted_contents(material.id)
            if len(ecs) == 1:
                out.append(ecs[0])
            elif len(ecs) > 1:
                raise ValidationError(
                    f"material {material.id} has multiple ExtractedContent; "
                    "pass extracted_content_ids explicitly (no auto latest)"
                )
        return out

    def resolve_usable_extracted_content_ids(self, case_id: UUID) -> list[UUID]:
        return [ec.id for ec in self.get_usable_extracted_contents(case_id)]

    def assert_extracted_content_usable(
        self, *, case_id: UUID, extracted_content_id: UUID
    ) -> ExtractedContent:
        ec = self.session.get(ExtractedContent, extracted_content_id)
        if ec is None:
            raise ValidationError(f"extracted_content not found: {extracted_content_id}")
        material = self.session.get(CaseMaterial, ec.material_id)
        if material is None or material.case_id != case_id:
            raise ValidationError(
                f"extracted_content {extracted_content_id} does not belong to case {case_id}"
            )
        if material.life_status == "VOID":
            raise ValidationError(
                f"extracted_content {extracted_content_id} belongs to VOID material"
            )
        if ec.status != "SUCCEEDED":
            raise ValidationError(
                f"extracted_content {extracted_content_id} is not SUCCEEDED "
                f"(status={ec.status})"
            )
        return ec

    def assert_material_usable_for_scope(
        self, *, case_id: UUID, material_id: UUID
    ) -> CaseMaterial:
        material = self.session.get(CaseMaterial, material_id)
        if material is None or material.case_id != case_id:
            raise ValidationError(f"material {material_id} does not belong to case {case_id}")
        if material.life_status == "VOID":
            raise ValidationError(f"material {material_id} is VOID and not usable")
        if not self.succeeded_extracted_contents(material_id):
            raise ValidationError(
                f"material {material_id} has no SUCCEEDED ExtractedContent; "
                "not in usable material pool"
            )
        return material

    def describe_material(self, material: CaseMaterial) -> MaterialUsabilityView:
        all_ecs = tuple(self.list_extracted_contents(material.id))
        succeeded = tuple(ec for ec in all_ecs if ec.status == "SUCCEEDED")
        usable = material.life_status != "VOID" and bool(succeeded)
        pending_reason, status_label, participation = self._pending_labels(
            material, all_ecs, succeeded, usable
        )
        return MaterialUsabilityView(
            material=material,
            succeeded_ecs=succeeded,
            all_ecs=all_ecs,
            usable=usable,
            pending_reason=pending_reason,
            status_label=status_label,
            analysis_participation=participation,
        )

    def pool_summary(self, case_id: UUID) -> dict[str, Any]:
        all_mats = self.list_materials(case_id)
        usable = self.list_usable_materials(case_id)
        pending = self.list_unusable_materials(case_id)
        voided = self.list_void_materials(case_id)
        pending_names = [m.filename for m in pending]
        disclosure = self.build_disclosure(
            usable_count=len(usable),
            pending_filenames=pending_names,
        )
        return {
            "uploaded_count": len(all_mats),
            "usable_count": len(usable),
            "pending_count": len(pending),
            "void_count": len(voided),
            "pending_filenames": pending_names,
            "disclosure": disclosure,
            "has_pending": len(pending) > 0,
            "warning": (
                f"注意：仍有 {len(pending)} 份文件未参与分析。"
                "这些文件可能影响案件事实、金额、履行情况或诉讼请求。"
                if pending
                else None
            ),
        }

    @staticmethod
    def build_disclosure(
        *, usable_count: int, pending_filenames: list[str]
    ) -> dict[str, Any]:
        pending_count = len(pending_filenames)
        if pending_count == 0:
            summary = f"本次分析基于 {usable_count} 份可读取材料。"
            if usable_count > 0:
                summary += "全部已上传且可读取的材料均已纳入有效案件材料池。"
            return {
                "usable_count": usable_count,
                "pending_count": 0,
                "pending_filenames": [],
                "summary": summary,
                "files_not_included": 0,
                "message": summary,
            }
        lines = "\n".join(f"- {name}" for name in pending_filenames)
        summary = (
            f"本次分析基于 {usable_count} 份可读取材料。\n"
            f"另有 {pending_count} 份文件未能读取，因此未参与本次分析：\n"
            f"{lines}"
        )
        return {
            "usable_count": usable_count,
            "pending_count": pending_count,
            "pending_filenames": list(pending_filenames),
            "summary": summary,
            "files_not_included": pending_count,
            "message": f"{pending_count} files not included in analysis",
        }

    def _pending_labels(
        self,
        material: CaseMaterial,
        all_ecs: tuple[ExtractedContent, ...],
        succeeded: tuple[ExtractedContent, ...],
        usable: bool,
    ) -> tuple[str | None, str, str]:
        if material.life_status == "VOID":
            return (
                material.void_reason or "材料已作废",
                "已作废",
                "未参与",
            )
        if usable:
            return (None, "解析成功", "可参与")
        if not all_ecs:
            return ("尚未产生解析结果", "待解析", "未参与")
        latest_failed = next(
            (ec for ec in reversed(all_ecs) if ec.status == "FAILED"),
            None,
        )
        detail = (latest_failed.error_detail if latest_failed else None) or "读取失败"
        lower = detail.lower()
        if "ocr" in lower or "needs_ocr" in lower or "文字层" in detail:
            reason = (
                f"{detail}；扫描件可走 CamScanner 转 Markdown，或手动上传 .md / .docx"
            )
            return (reason, "读取失败", "未参与")
        if "unsupported" in lower:
            return (detail, "格式不支持", "未参与")
        return (detail, "读取失败", "未参与")
