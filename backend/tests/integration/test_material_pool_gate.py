"""Material pool gate — usable vs pending isolation."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.application.evidence_organizer import EvidenceOrganizerService
from backend.application.material_management import MaterialManagementService
from backend.application.material_upload import MaterialUploadService
from backend.application.material_usability import MaterialUsabilityPolicy
from backend.application.pleading_writer import PleadingWriterService
from backend.application.workspace import WorkspaceQueryService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.fixtures import ensure_fixtures
from backend.models import CaseMaterial, EvidenceItem, ExtractedContent, SourceSpan
from backend.schemas.evidence_proposal import EvidenceItemProposal
from backend.skills.evidence_organizer import ScriptedOrganizerEngine
from backend.tools.storage import ObjectStorage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixtures() -> None:
    ensure_fixtures()


@pytest.fixture
def storage(tmp_path: Path) -> ObjectStorage:
    return ObjectStorage(root=tmp_path / "storage")


def test_a_upload_parse_success_enters_usable(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-a", owner_user_id=owner_id)
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    result = MaterialUploadService(db_session, storage=storage).upload(
        case_id=case.id, filename="合同.pdf", data=data, actor_id=actor_id
    )
    assert result.success is True
    assert result.usable is True
    assert "进入案件材料" in result.message
    policy = MaterialUsabilityPolicy(db_session)
    usable_ids = {m.id for m in policy.list_usable_materials(case.id)}
    pending_ids = {m.id for m in policy.list_unusable_materials(case.id)}
    assert result.material.id in usable_ids
    assert result.material.id not in pending_ids


def test_b_upload_parse_failed_stays_pending(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-b", owner_user_id=owner_id)
    data = (FIXTURES / "sample_scanned.pdf").read_bytes()
    result = MaterialUploadService(db_session, storage=storage).upload(
        case_id=case.id, filename="扫描件.pdf", data=data, actor_id=actor_id
    )
    assert result.success is False
    assert result.usable is False
    assert "尚未进入案件材料" in result.message
    material = db_session.get(CaseMaterial, result.material.id)
    assert material is not None
    ecs = list(
        db_session.scalars(
            select(ExtractedContent).where(ExtractedContent.material_id == material.id)
        )
    )
    assert ecs and all(ec.status == "FAILED" for ec in ecs)
    policy = MaterialUsabilityPolicy(db_session)
    assert material.id not in {m.id for m in policy.list_usable_materials(case.id)}
    assert material.id in {m.id for m in policy.list_unusable_materials(case.id)}


def test_c_organizer_rejects_failed_material(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-c", owner_user_id=owner_id)
    data = (FIXTURES / "sample_scanned.pdf").read_bytes()
    result = MaterialUploadService(db_session, storage=storage).upload(
        case_id=case.id, filename="失败.pdf", data=data, actor_id=actor_id
    )
    org = EvidenceOrganizerService(db_session)
    with pytest.raises(ValidationError, match="usable material pool|unusable"):
        org.resolve_extracted_content_ids(
            case_id=case.id, material_ids=[result.material.id]
        )
    failed_ec = db_session.scalars(
        select(ExtractedContent).where(ExtractedContent.material_id == result.material.id)
    ).first()
    assert failed_ec is not None
    with pytest.raises(ValidationError, match="not SUCCEEDED|unusable"):
        org.resolve_extracted_content_ids(
            case_id=case.id, extracted_content_ids=[failed_ec.id]
        )


def test_d_provenance_requires_succeeded_ec(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="gate-d", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="x.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash="h1",
        storage_key="k1",
        created_by=actor_id,
    )
    text = "足够长的正文用于证据溯源测试。"
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=len(text),
        quote=text,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="证据",
        summary="摘要",
        category="CONTRACT",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item is not None
    # Corrupt: mark EC FAILED after accept
    ec.status = "FAILED"
    db_session.flush()
    analyst = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="not SUCCEEDED"):
        analyst._load_provenance_spans(case.id, item)  # noqa: SLF001
    writer = PleadingWriterService(db_session)
    with pytest.raises(ValidationError, match="not SUCCEEDED"):
        writer._assert_evidence_provenance(case.id, item)  # noqa: SLF001


def test_e_analyst_rejects_void_material_provenance(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="gate-e", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="y.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash="h2",
        storage_key="k2",
        created_by=actor_id,
    )
    text = "甲方应支付设计服务费人民币十万元整。"
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=len(text),
        quote=text,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="证据",
        summary="摘要",
        category="PAYMENT",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item is not None
    svc.void_material(material.id, reason="作废测试", actor_id=actor_id)
    analyst = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="VOID"):
        analyst._load_provenance_spans(case.id, item)  # noqa: SLF001


def test_f_writer_rejects_failed_material_citation(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="gate-f", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="z.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash="h3",
        storage_key="k3",
        created_by=actor_id,
    )
    text = "被告尚欠货款共计人民币伍万元。"
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=len(text),
        quote=text,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="欠款证据",
        summary="欠款",
        category="PAYMENT",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item is not None
    ec.status = "FAILED"
    db_session.flush()
    writer = PleadingWriterService(db_session)
    with pytest.raises(ValidationError, match="not SUCCEEDED"):
        writer._assert_evidence_provenance(case.id, item)  # noqa: SLF001


def test_g_reparse_failed_then_succeeded_moves_pool(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-g", owner_user_id=owner_id)
    scanned = (FIXTURES / "sample_scanned.pdf").read_bytes()
    upload = MaterialUploadService(db_session, storage=storage).upload(
        case_id=case.id, filename="待重解析.pdf", data=scanned, actor_id=actor_id
    )
    assert upload.usable is False
    mid = upload.material.id
    old_ecs = list(
        db_session.scalars(select(ExtractedContent).where(ExtractedContent.material_id == mid))
    )
    assert len(old_ecs) == 1 and old_ecs[0].status == "FAILED"

    # Replace storage object with readable PDF while keeping CaseMaterial row
    good = (FIXTURES / "sample_text.pdf").read_bytes()
    material = db_session.get(CaseMaterial, mid)
    assert material is not None
    new_key = f"{case.id}/{uuid.uuid4().hex[:12]}_reparse.pdf"
    storage.put_bytes(new_key, good)
    material.storage_key = new_key
    material.content_hash = hashlib.sha256(good).hexdigest()
    material.byte_size = len(good)
    db_session.flush()

    reparsed = MaterialManagementService(db_session, storage=storage).reparse_material(
        case_id=case.id, material_id=mid, actor_id=actor_id
    )
    assert reparsed.outcome.success is True
    assert reparsed.usable is True
    ecs = list(
        db_session.scalars(select(ExtractedContent).where(ExtractedContent.material_id == mid))
    )
    assert len(ecs) == 2
    assert any(ec.status == "FAILED" for ec in ecs)
    assert any(ec.status == "SUCCEEDED" for ec in ecs)
    policy = MaterialUsabilityPolicy(db_session)
    assert mid in {m.id for m in policy.list_usable_materials(case.id)}
    assert mid not in {m.id for m in policy.list_unusable_materials(case.id)}


def test_h_void_material_not_usable(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-h", owner_user_id=owner_id)
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    result = MaterialUploadService(db_session, storage=storage).upload(
        case_id=case.id, filename="将作废.pdf", data=data, actor_id=actor_id
    )
    MaterialManagementService(db_session, storage=storage).void_material(
        case_id=case.id,
        material_id=result.material.id,
        actor_id=actor_id,
        reason="测试作废",
    )
    policy = MaterialUsabilityPolicy(db_session)
    assert not policy.is_material_usable(result.material.id, case_id=case.id)
    assert result.material.id not in {m.id for m in policy.list_usable_materials(case.id)}


def test_i_workspace_counts_and_disclosure(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-i", owner_user_id=owner_id)
    uploader = MaterialUploadService(db_session, storage=storage)
    good = (FIXTURES / "sample_text.pdf").read_bytes()
    docx = (FIXTURES / "sample.docx").read_bytes()
    bad = (FIXTURES / "sample_scanned.pdf").read_bytes()
    for i in range(5):
        uploader.upload(
            case_id=case.id, filename=f"ok{i}.pdf", data=good, actor_id=actor_id
        )
    uploader.upload(case_id=case.id, filename="ok.docx", data=docx, actor_id=actor_id)
    uploader.upload(case_id=case.id, filename="ok2.pdf", data=good, actor_id=actor_id)
    for i in range(3):
        uploader.upload(
            case_id=case.id, filename=f"bad{i}.pdf", data=bad, actor_id=actor_id
        )
    # uploaded=10, usable=7, pending=3
    ws = WorkspaceQueryService(db_session).get_workspace(case.id)
    pool = ws["material_pool"]
    assert pool["uploaded_count"] == 10
    assert pool["usable_count"] == 7
    assert pool["pending_count"] == 3
    assert len(ws["materials"]) == 7
    assert len(ws["usable_materials"]) == 7
    assert len(ws["pending_materials"]) == 3
    assert all(m["usable"] is True for m in ws["materials"])
    assert all(m["usable"] is False for m in ws["pending_materials"])
    assert pool["disclosure"]["files_not_included"] == 3
    assert "3 files not included in analysis" in pool["disclosure"]["message"]
    assert "未能读取" in pool["disclosure"]["summary"]


def test_j_organizer_only_processes_usable(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-j", owner_user_id=owner_id)
    uploader = MaterialUploadService(db_session, storage=storage)
    good = uploader.upload(
        case_id=case.id,
        filename="可读.pdf",
        data=(FIXTURES / "sample_text.pdf").read_bytes(),
        actor_id=actor_id,
    )
    bad = uploader.upload(
        case_id=case.id,
        filename="不可读.pdf",
        data=(FIXTURES / "sample_scanned.pdf").read_bytes(),
        actor_id=actor_id,
    )
    policy = MaterialUsabilityPolicy(db_session)
    ec_ids = policy.resolve_usable_extracted_content_ids(case.id)
    assert len(ec_ids) == 1
    good_ec = policy.succeeded_extracted_contents(good.material.id)[0]
    assert ec_ids[0] == good_ec.id

    span = db_session.scalars(
        select(SourceSpan).where(SourceSpan.extracted_content_id == good_ec.id)
    ).first()
    assert span is not None
    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine(
            [
                EvidenceItemProposal(
                    proposal_id=uuid.uuid4(),
                    title="可读材料证据",
                    summary="来自成功解析",
                    category="CONTRACT",
                    source_span_ids=[span.id],
                    confidence=0.7,
                    organizer_reason="gate",
                )
            ]
        ),
    )
    result = org.organize(
        case_id=case.id, extracted_content_ids=ec_ids, actor_id=actor_id
    )
    assert len(result.created) == 1
    # No evidence should cite the failed material
    items = list(
        db_session.scalars(select(EvidenceItem).where(EvidenceItem.case_id == case.id))
    )
    for item in items:
        links = DomainService(db_session).repo.list_evidence_spans(item.id, item.version)
        for link in links:
            sp = db_session.get(SourceSpan, link.source_span_id)
            assert sp is not None
            assert sp.material_id != bad.material.id

def test_j_void_pool_add_pending_and_clear(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = DomainService(db_session).create_case(title="gate-j-void-pool", owner_user_id=owner_id)
    uploader = MaterialUploadService(db_session, storage=storage)
    mgmt = MaterialManagementService(db_session, storage=storage)
    bad = (FIXTURES / "sample_scanned.pdf").read_bytes()
    uploader.upload(case_id=case.id, filename="扫描失败.pdf", data=bad, actor_id=actor_id)
    uploader.upload(case_id=case.id, filename="扫描失败2.pdf", data=bad, actor_id=actor_id)
    policy = MaterialUsabilityPolicy(db_session)
    assert len(policy.list_unusable_materials(case.id)) == 2
    assert len(policy.list_void_materials(case.id)) == 0

    added = mgmt.void_all_pending(case_id=case.id, actor_id=actor_id)
    assert added.count == 2
    assert len(policy.list_unusable_materials(case.id)) == 0
    assert len(policy.list_void_materials(case.id)) == 2

    cleared = mgmt.clear_void_pool(case_id=case.id, actor_id=actor_id)
    assert cleared.count == 2
    assert len(policy.list_void_materials(case.id)) == 0
    ws = WorkspaceQueryService(db_session).get_workspace(case.id)
    assert ws["material_pool"]["void_count"] == 0
    assert ws["void_materials"] == []
