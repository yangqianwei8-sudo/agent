"""Party candidate management — lawyer entry without auto-confirm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.enums import PartyRole, PartyType
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import Case, CaseParty, HumanDecision

_NAME_MAX = 500
_ROLE_LABELS = {
    PartyRole.PLAINTIFF.value: "原告",
    PartyRole.DEFENDANT.value: "被告",
    PartyRole.THIRD_PARTY.value: "第三人",
}
_ROLE_ALIASES = {
    "原告": PartyRole.PLAINTIFF.value,
    "被告": PartyRole.DEFENDANT.value,
    "第三人": PartyRole.THIRD_PARTY.value,
    "plaintiff": PartyRole.PLAINTIFF.value,
    "defendant": PartyRole.DEFENDANT.value,
    "third_party": PartyRole.THIRD_PARTY.value,
    "thirdparty": PartyRole.THIRD_PARTY.value,
}
_TYPE_ALIASES = {
    "组织": PartyType.ORG.value,
    "公司": PartyType.ORG.value,
    "机构": PartyType.ORG.value,
    "自然人": PartyType.PERSON.value,
    "个人": PartyType.PERSON.value,
    "org": PartyType.ORG.value,
    "person": PartyType.PERSON.value,
}
_OPTIONAL_ID_KEYS = (
    "address",
    "legal_representative",
    "credit_code",
    "contact",
)


@dataclass(frozen=True)
class PartyCandidateDTO:
    party_key: UUID
    role: str
    name: str
    party_type: str
    layer: str
    version: int
    case_id: UUID
    identifiers_json: dict[str, Any] | None = None

    @classmethod
    def from_model(cls, party: CaseParty) -> PartyCandidateDTO:
        return cls(
            party_key=party.party_key,
            role=party.role,
            name=party.name,
            party_type=party.party_type,
            layer=party.layer,
            version=party.version,
            case_id=party.case_id,
            identifiers_json=party.identifiers_json,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "party_key": str(self.party_key),
            "role": self.role,
            "name": self.name,
            "party_type": self.party_type,
            "layer": self.layer,
            "version": self.version,
            "case_id": str(self.case_id),
            "identifiers_json": self.identifiers_json,
            "role_label": _ROLE_LABELS.get(self.role, self.role),
            "status_label": _status_label(self.layer),
        }


def normalize_party_name(name: str | None) -> str:
    if name is None:
        raise ValidationError("请输入当事人名称")
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        raise ValidationError("请输入当事人名称")
    if len(cleaned) > _NAME_MAX:
        raise ValidationError(f"当事人名称过长（最多 {_NAME_MAX} 字）")
    return cleaned


def normalize_party_role(role: str | None) -> str:
    if role is None or not str(role).strip():
        raise ValidationError("请选择当事人角色")
    raw = str(role).strip()
    upper = raw.upper().replace("-", "_").replace(" ", "_")
    if upper in {r.value for r in PartyRole} and upper != PartyRole.OTHER.value:
        return upper
    mapped = _ROLE_ALIASES.get(raw) or _ROLE_ALIASES.get(raw.lower())
    if mapped is None:
        raise ValidationError("当事人角色无效，请选择原告、被告或第三人")
    return mapped


def normalize_party_type(party_type: str | None) -> str:
    if party_type is None or not str(party_type).strip():
        return PartyType.ORG.value
    raw = str(party_type).strip()
    upper = raw.upper()
    if upper in {t.value for t in PartyType}:
        return upper
    mapped = _TYPE_ALIASES.get(raw) or _TYPE_ALIASES.get(raw.lower())
    if mapped is None:
        raise ValidationError("当事人类型无效")
    return mapped


def _status_label(layer: str) -> str:
    return {
        "CANDIDATE": "待确认",
        "CONFIRMED": "已确认",
        "REJECTED": "已拒绝",
        "SUPERSEDED": "已替代",
    }.get(layer, layer)


def _build_identifiers(
    *,
    identifiers_json: dict[str, Any] | None,
    address: str | None,
    legal_representative: str | None,
    credit_code: str | None,
    contact: str | None,
) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if identifiers_json:
        for key in _OPTIONAL_ID_KEYS:
            val = identifiers_json.get(key)
            if val is None:
                continue
            text = str(val).strip()
            if text:
                out[key] = text
    for key, val in (
        ("address", address),
        ("legal_representative", legal_representative),
        ("credit_code", credit_code),
        ("contact", contact),
    ):
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[key] = text
    return out or None


class PartyManagementService:
    """Application facade for lawyer-entered Party candidates."""

    def __init__(self, session: Session, *, domain: DomainService | None = None) -> None:
        self.session = session
        self.domain = domain or DomainService(session)

    def create_party_candidate(
        self,
        *,
        case_id: UUID,
        role: str,
        name: str,
        actor_id: UUID,
        party_type: str | None = None,
        identifiers_json: dict[str, Any] | None = None,
        address: str | None = None,
        legal_representative: str | None = None,
        credit_code: str | None = None,
        contact: str | None = None,
        status: str | None = None,
        confirmed: bool | None = None,
        layer: str | None = None,
    ) -> PartyCandidateDTO:
        # Create path must never auto-confirm, even if client sends flags.
        if status is not None or confirmed is True or (
            layer is not None and str(layer).upper() == "CONFIRMED"
        ):
            raise ValidationError("当事人录入只能创建待确认候选，不能直接确认")

        case = self.session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")

        norm_role = normalize_party_role(role)
        norm_name = normalize_party_name(name)
        norm_type = normalize_party_type(party_type)
        ids = _build_identifiers(
            identifiers_json=identifiers_json,
            address=address,
            legal_representative=legal_representative,
            credit_code=credit_code,
            contact=contact,
        )

        existing = self._find_duplicate(case_id, role=norm_role, name=norm_name)
        if existing is not None:
            raise ConflictError("该当事人已存在")

        before_decisions = self._decision_count(case_id)
        party = self.domain.create_party(
            case_id=case_id,
            role=norm_role,
            name=norm_name,
            party_type=norm_type,
            actor_id=actor_id,
            identifiers_json=ids,
        )
        if party.layer != "CANDIDATE":
            raise ConflictError("create_party must yield CANDIDATE")
        if self._decision_count(case_id) != before_decisions:
            raise ConflictError("create_party must not create HumanDecision")
        return PartyCandidateDTO.from_model(party)

    def _find_duplicate(
        self, case_id: UUID, *, role: str, name: str
    ) -> CaseParty | None:
        rows = list(
            self.session.scalars(
                select(CaseParty).where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                    CaseParty.role == role,
                )
            )
        )
        target = normalize_party_name(name)
        for row in rows:
            if normalize_party_name(row.name) == target:
                return row
        return None

    def _decision_count(self, case_id: UUID) -> int:
        return len(
            list(
                self.session.scalars(
                    select(HumanDecision).where(HumanDecision.case_id == case_id)
                )
            )
        )
