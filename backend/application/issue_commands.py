"""Issue-centered application commands — formal mutations via DomainService."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.domain.services import DomainService


class IssueCommandService:
    def __init__(self, session: Session, *, actor_id: UUID) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.actor_id = actor_id

    def confirm_issue(self, issue_key: UUID) -> dict[str, Any]:
        issue = self.domain.confirm_issue(issue_key, actor_id=self.actor_id)
        self.session.commit()
        return {"issue_key": str(issue.issue_key), "version": issue.version, "status": issue.status}

    def reject_issue(self, issue_key: UUID) -> dict[str, Any]:
        issue = self.domain.reject_issue(issue_key, actor_id=self.actor_id)
        self.session.commit()
        return {"issue_key": str(issue.issue_key), "status": issue.status}

    def amend_issue(self, issue_key: UUID, *, new_statement: str) -> dict[str, Any]:
        issue = self.domain.amend_issue(
            issue_key, new_statement=new_statement, actor_id=self.actor_id
        )
        self.session.commit()
        return {"issue_key": str(issue.issue_key), "version": issue.version}

    def merge_issues(
        self, case_id: UUID, *, source_issue_keys: list[UUID], merged_statement: str
    ) -> dict[str, Any]:
        issue = self.domain.merge_issues(
            case_id=case_id,
            source_issue_keys=source_issue_keys,
            merged_statement=merged_statement,
            actor_id=self.actor_id,
        )
        self.session.commit()
        return {"issue_key": str(issue.issue_key), "version": issue.version}

    def split_issue(
        self, case_id: UUID, *, source_issue_key: UUID, targets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        created = self.domain.split_issue(
            case_id=case_id,
            source_issue_key=source_issue_key,
            targets=targets,
            actor_id=self.actor_id,
        )
        self.session.commit()
        return {"issues": [{"issue_key": str(i.issue_key)} for i in created]}

    def confirm_position(self, position_key: UUID) -> dict[str, Any]:
        pos = self.domain.confirm_position(position_key, actor_id=self.actor_id)
        self.session.commit()
        return {"position_key": str(pos.position_key), "status": pos.status}

    def reject_position(self, position_key: UUID) -> dict[str, Any]:
        pos = self.domain.reject_position(position_key, actor_id=self.actor_id)
        self.session.commit()
        return {"position_key": str(pos.position_key), "status": pos.status}

    def create_lawyer_position(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        side: str,
        position_type: str,
        statement: str,
        opponent_material_ref: str | None = None,
    ) -> dict[str, Any]:
        pos = self.domain.create_lawyer_position(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            side=side,
            position_type=position_type,
            statement=statement,
            actor_id=self.actor_id,
            opponent_material_ref=opponent_material_ref,
        )
        self.session.commit()
        return {"position_key": str(pos.position_key), "status": pos.status}

    def adopt_proof_task(self, proof_task_key: UUID) -> dict[str, Any]:
        task = self.domain.adopt_proof_task(proof_task_key, actor_id=self.actor_id)
        self.session.commit()
        return {"proof_task_key": str(task.proof_task_key), "status": task.status}

    def waive_proof_task(self, proof_task_key: UUID) -> dict[str, Any]:
        task = self.domain.waive_proof_task(proof_task_key, actor_id=self.actor_id)
        self.session.commit()
        return {"proof_task_key": str(task.proof_task_key), "status": task.status}

    def create_lawyer_proof_task(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
    ) -> dict[str, Any]:
        task = self.domain.create_lawyer_proof_task(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            actor_id=self.actor_id,
        )
        self.session.commit()
        return {"proof_task_key": str(task.proof_task_key), "status": task.status}

    def link_fact_to_proof_task(
        self,
        *,
        case_id: UUID,
        proof_task_key: UUID,
        proof_task_version: int,
        fact_key: UUID,
        fact_version: int,
        role: str,
    ) -> dict[str, Any]:
        link = self.domain.link_fact_to_proof_task(
            case_id=case_id,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            fact_key=fact_key,
            fact_version=fact_version,
            role=role,
            actor_id=self.actor_id,
        )
        self.session.commit()
        return {"link_id": str(link.id)}

    def resolve_conflict(self, conflict_id: UUID, *, resolution_note: str) -> dict[str, Any]:
        c = self.domain.resolve_conflict(
            conflict_id, resolution_note=resolution_note, actor_id=self.actor_id
        )
        self.session.commit()
        return {"conflict_id": str(c.id), "status": c.status}

    def dismiss_conflict(self, conflict_id: UUID, *, resolution_note: str) -> dict[str, Any]:
        c = self.domain.dismiss_conflict(
            conflict_id, resolution_note=resolution_note, actor_id=self.actor_id
        )
        self.session.commit()
        return {"conflict_id": str(c.id), "status": c.status}

    def resolve_proof_gap(self, gap_id: UUID, *, resolution_note: str) -> dict[str, Any]:
        g = self.domain.resolve_proof_gap(
            gap_id, resolution_note=resolution_note, actor_id=self.actor_id
        )
        self.session.commit()
        return {"gap_id": str(g.id), "status": g.status}

    def waive_proof_gap(self, gap_id: UUID, *, resolution_note: str) -> dict[str, Any]:
        g = self.domain.waive_proof_gap(
            gap_id, resolution_note=resolution_note, actor_id=self.actor_id
        )
        self.session.commit()
        return {"gap_id": str(g.id), "status": g.status}

    def create_proof_gap(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        gap_type: str,
        description: str,
        proof_task_key: UUID | None = None,
        proof_task_version: int | None = None,
        what_exists: str | None = None,
        what_is_missing: str | None = None,
        why_it_matters: str | None = None,
        suggested_material_types: list[str] | None = None,
    ) -> dict[str, Any]:
        g = self.domain.create_proof_gap(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            gap_type=gap_type,
            description=description,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            what_exists=what_exists,
            what_is_missing=what_is_missing,
            why_it_matters=why_it_matters,
            suggested_material_types=suggested_material_types,
            actor_id=self.actor_id,
            source_type="LAWYER_CREATED",
        )
        self.session.commit()
        return {"gap_id": str(g.id), "status": g.status}

    def create_lawyer_assessment(
        self,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        content: str,
    ) -> dict[str, Any]:
        a = self.domain.create_lawyer_assessment(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            content=content,
            actor_id=self.actor_id,
        )
        self.session.commit()
        return {"assessment_key": str(a.assessment_key), "version": a.version}

    def amend_lawyer_assessment(
        self, assessment_key: UUID, *, new_content: str
    ) -> dict[str, Any]:
        a = self.domain.amend_lawyer_assessment(
            assessment_key, new_content=new_content, actor_id=self.actor_id
        )
        self.session.commit()
        return {"assessment_key": str(a.assessment_key), "version": a.version}

    def withdraw_lawyer_assessment(self, assessment_key: UUID) -> dict[str, Any]:
        a = self.domain.withdraw_lawyer_assessment(
            assessment_key, actor_id=self.actor_id
        )
        self.session.commit()
        return {"assessment_key": str(a.assessment_key), "status": a.status}
