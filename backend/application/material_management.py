"""Material lifecycle for lawyer MVP — void + reparse via Domain/Extraction only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.application.material_extraction import (
    ExtractionOutcome,
    MaterialExtractionService,
)
from backend.application.material_usability import (
    CLEARED_FROM_VOID_POOL_PREFIX,
    MaterialUsabilityPolicy,
    is_cleared_from_void_pool,
)
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import CaseMaterial
from backend.tools.storage import ObjectStorage


@dataclass
class ReparseResult:
    material: CaseMaterial
    outcome: ExtractionOutcome
    previous_extracted_content_id: UUID | None
    usable: bool


@dataclass
class VoidPoolActionResult:
    affected_ids: list[UUID]
    count: int


class MaterialManagementService:
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.extraction = MaterialExtractionService(session, storage=storage)
        self.policy = MaterialUsabilityPolicy(session)

    def void_material(
        self,
        *,
        case_id: UUID,
        material_id: UUID,
        actor_id: UUID,
        reason: str | None = None,
    ) -> CaseMaterial:
        material = self.domain.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.case_id != case_id:
            raise ValidationError("material does not belong to this case")
        text = (reason or "").strip() or "律师在工作台作废材料"
        if len(text) > 500:
            raise ValidationError("void reason too long")
        try:
            return self.domain.void_material(
                material_id,
                reason=text,
                actor_id=actor_id,
            )
        except ConflictError:
            raise
        except NotFoundError:
            raise

    def void_all_pending(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        reason: str | None = None,
    ) -> VoidPoolActionResult:
        """Move all pending (unusable ACTIVE) materials into the void pool."""
        text = (reason or "").strip() or "律师将待处理文件批量移入作废池"
        pending = self.policy.list_unusable_materials(case_id)
        ids: list[UUID] = []
        for material in pending:
            self.domain.void_material(
                material.id,
                reason=text,
                actor_id=actor_id,
            )
            ids.append(material.id)
        return VoidPoolActionResult(affected_ids=ids, count=len(ids))

    def clear_void_pool(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
    ) -> VoidPoolActionResult:
        """Hide all VOID materials from the void pool (soft clear; files kept)."""
        voided = self.policy.list_void_materials(case_id)
        ids: list[UUID] = []
        stamp = datetime.now(UTC).isoformat()
        for material in voided:
            prior = material.void_reason or ""
            if is_cleared_from_void_pool(material):
                continue
            material.void_reason = f"{CLEARED_FROM_VOID_POOL_PREFIX}{stamp}|{prior}"[
                :500
            ]
            material.updated_at = datetime.now(UTC)
            self.domain._audit(  # noqa: SLF001 — reuse Domain audit helper
                actor_id,
                "clear_void_pool_item",
                "case_materials",
                material.id,
                case_id=case_id,
                after={"void_reason": material.void_reason},
            )
            ids.append(material.id)
        self.session.flush()
        return VoidPoolActionResult(affected_ids=ids, count=len(ids))

    def reparse_material(
        self,
        *,
        case_id: UUID,
        material_id: UUID,
        actor_id: UUID,
    ) -> ReparseResult:
        """Create a NEW ExtractedContent attempt; never overwrite/delete old EC rows."""
        material = self.domain.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.case_id != case_id:
            raise ValidationError("material does not belong to this case")
        if material.life_status == "VOID":
            raise ConflictError("cannot reparse VOID material")

        existing = self.policy.list_extracted_contents(material_id)
        previous_id = existing[-1].id if existing else None
        version = self._next_extraction_version(existing)
        outcome = self.extraction.extract_material(
            material_id,
            actor_id=actor_id,
            extraction_version=version,
            previous_extracted_content_id=previous_id,
        )
        # Refresh material after parse_status update
        material = self.domain.repo.get_material(material_id)
        assert material is not None
        return ReparseResult(
            material=material,
            outcome=outcome,
            previous_extracted_content_id=previous_id,
            usable=self.policy.is_material_usable(material_id, case_id=case_id),
        )

    @staticmethod
    def _next_extraction_version(existing: list) -> str:
        # Unique under (material_id, method, version); method may vary, so bump a counter.
        n = len(existing) + 1
        return f"reparse-v{n}"
