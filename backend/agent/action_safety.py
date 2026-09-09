"""Natural Language Action Safety Gate — mutation requires complete valid slots.

LLM/Router proposing an intent ≠ permission to mutate Domain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from backend.agent.dto import AgentIntent

logger = logging.getLogger(__name__)


class SafetyVerdict(StrEnum):
    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


class ActionAuditEvent(StrEnum):
    ACTION_PROPOSED = "ACTION_PROPOSED"
    ACTION_VALIDATED = "ACTION_VALIDATED"
    ACTION_REJECTED_INCOMPLETE = "ACTION_REJECTED_INCOMPLETE"
    ACTION_REJECTED_AMBIGUOUS = "ACTION_REJECTED_AMBIGUOUS"
    ACTION_REJECTED_INVALID = "ACTION_REJECTED_INVALID"
    ACTION_EXECUTED = "ACTION_EXECUTED"


# Intents that mutate domain / advance workflow gates
MUTATION_INTENTS = frozenset(
    {
        AgentIntent.CREATE_PARTY,
        AgentIntent.CONFIRM_PARTY,
        AgentIntent.REJECT_PARTY,
        AgentIntent.AMEND_PARTY,
        AgentIntent.ACCEPT_EVIDENCE,
        AgentIntent.EXCLUDE_EVIDENCE,
        AgentIntent.CONFIRM_FACT,
        AgentIntent.REJECT_FACT,
        AgentIntent.AMEND_FACT,
        AgentIntent.CONFIRM_CLAIM_DIRECTION,
        AgentIntent.REJECT_CLAIM_DIRECTION,
        AgentIntent.AMEND_CLAIM_DIRECTION,
        AgentIntent.APPROVE_DRAFT,
        AgentIntent.GENERATE_COMPLAINT,
        AgentIntent.CONTINUE,
        AgentIntent.PAUSE,
        AgentIntent.RESUME,
        AgentIntent.RETRY,
        AgentIntent.START_CASE_WORKFLOW,
        AgentIntent.ORGANIZE_EVIDENCE,
    }
)

_ROLE_WORDS = frozenset({"原告", "被告", "第三人", "当事人", "双方"})
_PRONOUNS = frozenset({"他", "她", "他们", "她们", "对方", "这边", "那边"})
_GENERIC = frozenset(
    {
        "这个公司",
        "那个公司",
        "该公司",
        "公司",
        "企业",
        "单位",
        "对方公司",
        "某公司",
        "某某",
        "某某公司",
    }
)
_ACTION_PHRASES = frozenset(
    {
        "帮我录入",
        "先录一下",
        "录一下",
        "录入",
        "新增",
        "添加",
        "登记",
        "创建",
    }
)
_CONJ_PREFIX = re.compile(
    r"^(与|和|以及|及|还有|再加|还有个|还有一位|还有一个|还有名)\s*"
)
_TRAILING_PARTICLE = re.compile(r"[啊呀呢吧嘛哦了啦]+$")


@dataclass
class ActionProposal:
    intent: AgentIntent
    arguments: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    confidence: float = 1.0
    raw_message: str = ""


@dataclass
class SafetyResult:
    verdict: SafetyVerdict
    proposal: ActionProposal
    message: str
    missing_fields: list[str] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    audit_event: ActionAuditEvent = ActionAuditEvent.ACTION_PROPOSED


def audit_action(
    event: ActionAuditEvent,
    *,
    case_id: UUID | None,
    conversation_id: UUID | None,
    intent: AgentIntent | str,
    missing_fields: list[str] | None = None,
    validation_result: str | None = None,
) -> None:
    logger.info(
        "action_safety event=%s case_id=%s conversation_id=%s intent=%s "
        "missing_fields=%s validation_result=%s",
        event.value,
        str(case_id) if case_id else None,
        str(conversation_id) if conversation_id else None,
        getattr(intent, "value", intent),
        missing_fields or [],
        validation_result,
    )


class PartyNameValidator:
    """Reject residual fragments / pronouns / role words as party names."""

    MIN_LEN = 2
    MAX_LEN = 200

    @classmethod
    def normalize(cls, name: str | None) -> str:
        if name is None:
            return ""
        text = str(name).strip()
        text = text.strip("：:，,。.;；、\"'“”‘’（）()[]【】")
        text = _TRAILING_PARTICLE.sub("", text).strip()
        return text

    @classmethod
    def is_valid(cls, name: str | None) -> bool:
        text = cls.normalize(name)
        if not text:
            return False
        if len(text) < cls.MIN_LEN or len(text) > cls.MAX_LEN:
            return False
        if text in _ROLE_WORDS or text in _PRONOUNS or text in _GENERIC:
            return False
        if text in _ACTION_PHRASES:
            return False
        # Conjunction residue: 与被告 / 和被告啊 / 以及被告
        if _CONJ_PREFIX.match(text):
            return False
        # Role-word residue fragments (not names like 「被告乙」)
        if re.search(r"(与|和|以及|及|还有|再加)\s*(原告|被告|第三人)", text):
            return False
        if re.fullmatch(r"(原告|被告|第三人)[啊呀呢吧嘛哦了啦]*", text):
            return False
        if re.match(r"^(原告|被告|第三人)(与|和|以及)", text):
            return False
        # Pure conjunction / particle
        if re.fullmatch(r"[与和及以及啊呀呢吧嘛哦了啦\s]+", text):
            return False
        # Too generic short tokens
        if text in {"这个", "那个", "这些", "那些", "有关", "相关"}:
            return False
        if re.fullmatch(r"[你我您咱们我们]+", text):
            return False
        if text.startswith(("如果", "假如", "作为", "你是", "我是")):
            return False
        # Residual action / filler around role words
        if re.search(r"(录入|录进去|登记|添加|新增|帮我|也录|先录)", text):
            return False
        if re.fullmatch(r"[也还再又]+.*", text) and len(text) <= 8:
            return False
        return True

    @classmethod
    def invalid_reason(cls, name: str | None) -> str:
        text = cls.normalize(name)
        if not text:
            return "缺少名称"
        if not cls.is_valid(name):
            return f"“{text}”不是有效的当事人名称"
        return ""


def extract_parties_from_text(text: str) -> list[dict[str, str]]:
    """Extract explicit plaintiff/defendant/third-party name pairs from NL."""
    raw = (text or "").strip()
    if not raw:
        return []

    found: list[dict[str, str]] = []

    # Pattern: 原告是X，被告是Y / 原告：X 被告：Y / 原告X，被告Y
    patterns = [
        (
            r"原告\s*(?:是|为|：|:)?\s*(.+?)\s*[，,；;、\n]+\s*"
            r"被告\s*(?:是|为|：|:)?\s*(.+?)(?:$|[，,。！？!?\n]|帮我|录入)",
            ("PLAINTIFF", "DEFENDANT"),
        ),
        (
            r"被告\s*(?:是|为|：|:)?\s*(.+?)\s*[，,；;、\n]+\s*"
            r"原告\s*(?:是|为|：|:)?\s*(.+?)(?:$|[，,。！？!?\n]|帮我|录入)",
            ("DEFENDANT", "PLAINTIFF"),
        ),
        (
            r"(?:帮我)?(?:把|将)\s*(.+?)\s*(?:录为|登记为|作为)\s*原告",
            ("PLAINTIFF",),
        ),
        (
            r"(?:帮我)?(?:把|将)\s*(.+?)\s*(?:录为|登记为|作为)\s*被告",
            ("DEFENDANT",),
        ),
        (
            r"(.+?)\s*是\s*原告(?!律师|代理人)",
            ("PLAINTIFF",),
        ),
        (
            r"(.+?)\s*是\s*被告(?!律师|代理人)",
            ("DEFENDANT",),
        ),
        (
            r"原告\s*(?:是|为|：|:)\s*(.+?)(?:$|[，,。；;\n])",
            ("PLAINTIFF",),
        ),
        (
            r"被告\s*(?:是|为|：|:)\s*(.+?)(?:$|[，,。；;\n])",
            ("DEFENDANT",),
        ),
        (
            r"第三人\s*(?:是|为|：|:)\s*(.+?)(?:$|[，,。；;\n])",
            ("THIRD_PARTY",),
        ),
        # Role + name without separator (e.g. 被告四川富茂置业有限公司)
        (
            r"(?:^|[，,；;\s])原告(?!与|和|以及|及)(.+?)(?:$|[，,。；;\n])",
            ("PLAINTIFF",),
        ),
        (
            r"(?:^|[，,；;\s])被告(?!与|和|以及|及)(.+?)(?:$|[，,。；;\n])",
            ("DEFENDANT",),
        ),
        # Explicit create with name (name still validated later)
        (
            r"(?:录入|新增|添加|登记)\s*原告\s*[:：]?\s*(.+)$",
            ("PLAINTIFF",),
        ),
        (
            r"(?:录入|新增|添加|登记)\s*被告\s*[:：]?\s*(.+)$",
            ("DEFENDANT",),
        ),
        (
            r"(?:录入|新增|添加|登记)\s*第三人\s*[:：]?\s*(.+)$",
            ("THIRD_PARTY",),
        ),
    ]

    # Prefer dual-role patterns first
    dual = re.search(patterns[0][0], raw)
    if dual:
        a = PartyNameValidator.normalize(dual.group(1))
        b = PartyNameValidator.normalize(dual.group(2))
        a = re.sub(r"(帮我)?(录进去|录入|登记|添加|新增)$", "", a).strip(" ，,")
        b = re.sub(r"(帮我)?(录进去|录入|登记|添加|新增)$", "", b).strip(" ，,")
        if a:
            found.append({"role": "PLAINTIFF", "name": a})
        if b:
            found.append({"role": "DEFENDANT", "name": b})
        return _dedupe_parties(found)

    dual2 = re.search(patterns[1][0], raw)
    if dual2:
        a = PartyNameValidator.normalize(dual2.group(1))
        b = PartyNameValidator.normalize(dual2.group(2))
        a = re.sub(r"(帮我)?(录进去|录入|登记|添加|新增)$", "", a).strip(" ，,")
        b = re.sub(r"(帮我)?(录进去|录入|登记|添加|新增)$", "", b).strip(" ，,")
        if a:
            found.append({"role": "DEFENDANT", "name": a})
        if b:
            found.append({"role": "PLAINTIFF", "name": b})
        return _dedupe_parties(found)

    for pat, roles in patterns[2:]:
        m = re.search(pat, raw)
        if not m:
            continue
        name = PartyNameValidator.normalize(m.group(1))
        name = re.sub(r"^(是|为)\s*", "", name).strip()
        # Strip trailing action verbs
        name = re.sub(r"(帮我)?(录进去|录入|登记|添加|新增)$", "", name).strip(" ，,")
        if not name:
            continue
        if any(x["role"] == roles[0] for x in found):
            continue
        found.append({"role": roles[0], "name": name})

    return _dedupe_parties(found)


def _dedupe_parties(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for it in items:
        key = (it["role"], it["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def detect_create_party_request(text: str) -> ActionProposal | None:
    """Parse CREATE_PARTY / multi-party create from natural language."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Cancel phrases handled elsewhere
    create_verb = bool(re.search(r"(录入|新增|添加|登记|录进去|录一下|帮我录|录为)", raw))
    mentions_plaintiff = "原告" in raw
    mentions_defendant = "被告" in raw
    mentions_third = "第三人" in raw
    explicit_role_as = bool(
        re.search(
            r"(录为原告|录为被告|"
            r"(?:^|[^律])是\s*原告(?:[，,。：:\s]|$)|"
            r"(?:^|[^律代])是\s*被告(?:[，,。：:\s]|$))",
            raw,
        )
    )
    if not create_verb and not explicit_role_as:
        # Allow "X是被告，录进去"
        if not (re.search(r"是\s*被告|是\s*原告", raw) and re.search(r"录", raw)):
            return None
        # Exclude 「是被告律师/代理人」 discussion
        if re.search(r"是\s*被告(?:律师|代理人|诉讼代理人)", raw):
            return None
        if re.search(r"是\s*原告(?:律师|代理人)", raw):
            return None

    parties = extract_parties_from_text(raw)
    valid_parties = [
        p for p in parties if PartyNameValidator.is_valid(p.get("name"))
    ]

    requested_roles: list[str] = []
    if mentions_plaintiff:
        requested_roles.append("PLAINTIFF")
    if mentions_defendant:
        requested_roles.append("DEFENDANT")
    if mentions_third:
        requested_roles.append("THIRD_PARTY")

    # "录入原告与被告啊" → both roles, no valid names
    if create_verb and len(requested_roles) >= 2 and not valid_parties:
        missing = []
        if "PLAINTIFF" in requested_roles:
            missing.append("plaintiff_name")
        if "DEFENDANT" in requested_roles:
            missing.append("defendant_name")
        if "THIRD_PARTY" in requested_roles:
            missing.append("third_party_name")
        return ActionProposal(
            intent=AgentIntent.CREATE_PARTY,
            arguments={
                "mode": "multi",
                "requested_roles": requested_roles,
                "parties": [],
            },
            missing_fields=missing,
            raw_message=raw,
        )

    # Single role create without name: 帮我录入原告
    if create_verb and len(requested_roles) == 1 and not valid_parties:
        role = requested_roles[0]
        field_name = {
            "PLAINTIFF": "plaintiff_name",
            "DEFENDANT": "defendant_name",
            "THIRD_PARTY": "third_party_name",
        }[role]
        return ActionProposal(
            intent=AgentIntent.CREATE_PARTY,
            arguments={"mode": "single", "role": role, "name": None, "parties": []},
            missing_fields=[field_name],
            raw_message=raw,
        )

    # "把被告也录进去" without name
    if create_verb and mentions_defendant and not mentions_plaintiff and not valid_parties:
        return ActionProposal(
            intent=AgentIntent.CREATE_PARTY,
            arguments={"mode": "single", "role": "DEFENDANT", "name": None},
            missing_fields=["defendant_name"],
            raw_message=raw,
        )

    if valid_parties:
        # If user asked for both roles but only one name extracted → incomplete
        if len(requested_roles) >= 2:
            have = {p["role"] for p in valid_parties}
            missing = []
            if "PLAINTIFF" in requested_roles and "PLAINTIFF" not in have:
                missing.append("plaintiff_name")
            if "DEFENDANT" in requested_roles and "DEFENDANT" not in have:
                missing.append("defendant_name")
            if missing:
                return ActionProposal(
                    intent=AgentIntent.CREATE_PARTY,
                    arguments={
                        "mode": "multi",
                        "requested_roles": requested_roles,
                        "parties": valid_parties,
                    },
                    missing_fields=missing,
                    raw_message=raw,
                )
            return ActionProposal(
                intent=AgentIntent.CREATE_PARTY,
                arguments={
                    "mode": "multi",
                    "requested_roles": requested_roles,
                    "parties": valid_parties,
                },
                missing_fields=[],
                raw_message=raw,
            )

        # Single valid party
        p0 = valid_parties[0]
        return ActionProposal(
            intent=AgentIntent.CREATE_PARTY,
            arguments={
                "mode": "single",
                "role": p0["role"],
                "name": p0["name"],
                "parties": valid_parties,
            },
            missing_fields=[],
            raw_message=raw,
        )

    return None


def detect_incomplete_mutation(text: str) -> ActionProposal | None:
    """Map clear action verbs with missing slots → intent + missing_fields.

    Does not invent targets. Used by Deterministic + LLM routers so incomplete
    actions never fall through to UNKNOWN.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # Pronoun / deixis actions — target filled later via recent_focus
    if re.search(r"(把)?(它|这个|那个)(也)?接受(了|一下)?", raw) and not re.search(
        r"证据\s*[0-9]+", raw
    ):
        return ActionProposal(
            intent=AgentIntent.ACCEPT_EVIDENCE,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )
    if re.search(r"(把)?(它|这个|那个)(也)?排除(了|掉|一下)?", raw) and not re.search(
        r"证据\s*[0-9]+", raw
    ):
        return ActionProposal(
            intent=AgentIntent.EXCLUDE_EVIDENCE,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )
    if re.search(
        r"(把)?(它|这个|那个|有关)?(事实)?(也)?确认(了|一下)?", raw
    ) and "事实" in raw and not re.search(r"事实\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.CONFIRM_FACT,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # ACCEPT_EVIDENCE without number
    if re.search(
        r"(把)?证据(都)?接受(一下)?|接受(一下)?证据|证据接受(一下)?|"
        r"把证据(也)?(给)?接受|帮我接受(一下)?证据",
        raw,
    ) and not re.search(r"证据\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.ACCEPT_EVIDENCE,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # EXCLUDE_EVIDENCE without number
    if re.search(
        r"(把)?(这个|那个)?证据(给)?排除(掉|一下)?|排除(一下)?证据|"
        r"帮我排除(一下)?证据",
        raw,
    ) and not re.search(r"证据\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.EXCLUDE_EVIDENCE,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # CONFIRM_FACT without number
    if (
        re.search(
            r"(把)?(那个|这个|有关)?事实(也)?确认(一下|了)?|"
            r"确认(一下|那个|这个|有关)?事实|事实(也)?确认(一下|了)?",
            raw,
        )
        and not re.search(r"事实\s*[0-9]+", raw)
        and "诉讼请求" not in raw
    ):
        return ActionProposal(
            intent=AgentIntent.CONFIRM_FACT,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # REJECT_FACT without number
    if re.search(
        r"(这个|那个)?事实(不对|有问题)?[，,]?(拒绝|删掉)|拒绝(一下)?事实|"
        r"事实(给)?拒绝(掉)?",
        raw,
    ) and not re.search(r"事实\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.REJECT_FACT,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # AMEND_FACT: with or without target number
    if m := re.search(
        r"(?:帮我)?(?:改|修改)(?:一下)?事实\s*([0-9]+)"
        r"(?:\s*(?:为|成|改成|修改为)\s*(.+))?$",
        raw,
    ):
        target = m.group(1)
        stmt = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
        if stmt:
            return ActionProposal(
                intent=AgentIntent.AMEND_FACT,
                targets=[target],
                arguments={"new_statement": stmt},
                missing_fields=[],
                raw_message=raw,
            )
        return ActionProposal(
            intent=AgentIntent.AMEND_FACT,
            targets=[target],
            arguments={"missing_fields": ["new_statement"]},
            missing_fields=["new_statement"],
            raw_message=raw,
        )
    if re.search(r"(?:帮我)?(?:改|修改)(?:一下)?事实(?!\s*[0-9])", raw) and not re.search(
        r"事实\s*[0-9]+", raw
    ):
        return ActionProposal(
            intent=AgentIntent.AMEND_FACT,
            arguments={"missing_fields": ["target", "new_statement"]},
            missing_fields=["target", "new_statement"],
            raw_message=raw,
        )

    # CONFIRM_PARTY without number
    if re.search(
        r"(把)?(那个|这个)?(当事人|原告|被告|第三人)(也)?确认(了|一下)?|"
        r"确认(一下)?(那个|这个)?(当事人|原告|被告|第三人)(?!\s*[0-9])",
        raw,
    ) and not re.search(r"(当事人|原告|被告|第三人)\s*[0-9]+", raw):
        role = None
        if "被告" in raw and "原告" not in raw:
            role = "DEFENDANT"
        elif "原告" in raw and "被告" not in raw:
            role = "PLAINTIFF"
        elif "第三人" in raw:
            role = "THIRD_PARTY"
        args: dict[str, Any] = {"missing_fields": ["target"]}
        if role:
            args["role"] = role
        return ActionProposal(
            intent=AgentIntent.CONFIRM_PARTY,
            arguments=args,
            missing_fields=["target"],
            raw_message=raw,
        )

    # REJECT_PARTY without number
    if re.search(
        r"(那个|这个)?(当事人|原告|被告|第三人)(给)?(删掉|拒绝掉|拒绝)|"
        r"拒绝(一下)?(当事人|原告|被告|第三人)(?!\s*[0-9])",
        raw,
    ) and not re.search(r"(当事人|原告|被告|第三人)\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.REJECT_PARTY,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    # CONFIRM_CLAIM without / with optional number already handled elsewhere
    if re.search(
        r"(把)?诉讼请求(也)?确认(了|一下)?|确认(一下)?诉讼请求(?!\s*[0-9])",
        raw,
    ) and not re.search(r"诉讼请求\s*[0-9]+", raw):
        return ActionProposal(
            intent=AgentIntent.CONFIRM_CLAIM_DIRECTION,
            arguments={"missing_fields": ["target"]},
            missing_fields=["target"],
            raw_message=raw,
        )

    return None


class ActionSafetyGate:
    """Validate mutation proposals before Domain writes."""

    def validate(
        self,
        *,
        intent: AgentIntent,
        targets: list[str],
        parameters: dict[str, Any],
        raw_message: str = "",
        case_id: UUID | None = None,
        conversation_id: UUID | None = None,
        resolution: dict[str, Any] | None = None,
    ) -> SafetyResult:
        proposal = ActionProposal(
            intent=intent,
            arguments=dict(parameters or {}),
            targets=list(targets or []),
            missing_fields=list((parameters or {}).get("missing_fields") or []),
            confidence=float((parameters or {}).get("confidence") or 1.0),
            raw_message=raw_message,
        )
        audit_action(
            ActionAuditEvent.ACTION_PROPOSED,
            case_id=case_id,
            conversation_id=conversation_id,
            intent=intent,
            missing_fields=proposal.missing_fields,
        )

        if intent not in MUTATION_INTENTS:
            result = SafetyResult(
                verdict=SafetyVerdict.VALID,
                proposal=proposal,
                message="",
                audit_event=ActionAuditEvent.ACTION_VALIDATED,
            )
            return result

        if intent == AgentIntent.CREATE_PARTY:
            return self._validate_create_party(
                proposal, case_id=case_id, conversation_id=conversation_id
            )

        # Target-required mutations
        needs_target = {
            AgentIntent.CONFIRM_PARTY,
            AgentIntent.REJECT_PARTY,
            AgentIntent.AMEND_PARTY,
            AgentIntent.ACCEPT_EVIDENCE,
            AgentIntent.EXCLUDE_EVIDENCE,
            AgentIntent.CONFIRM_FACT,
            AgentIntent.REJECT_FACT,
            AgentIntent.AMEND_FACT,
        }
        resolution = resolution or {}

        # Pronoun / deixis → recent_focus (unique only)
        if intent in needs_target and not proposal.targets:
            filled = self._try_resolve_focus(intent, raw_message, resolution)
            if filled is not None:
                if filled.get("ambiguous"):
                    from backend.agent.clarification import ActionClarificationRenderer

                    msg = ActionClarificationRenderer().render(
                        intent=intent,
                        missing_fields=["target"],
                        verdict="AMBIGUOUS",
                        ctx=self._clarification_ctx(resolution, intent, proposal),
                    )
                    audit_action(
                        ActionAuditEvent.ACTION_REJECTED_AMBIGUOUS,
                        case_id=case_id,
                        conversation_id=conversation_id,
                        intent=intent,
                        missing_fields=["target"],
                        validation_result="ambiguous_focus",
                    )
                    return SafetyResult(
                        verdict=SafetyVerdict.AMBIGUOUS,
                        proposal=proposal,
                        message=msg,
                        missing_fields=["target"],
                        pending_action=self._pending_for(
                            intent, proposal, ["target"]
                        ),
                        audit_event=ActionAuditEvent.ACTION_REJECTED_AMBIGUOUS,
                    )
                if filled.get("target"):
                    proposal.targets = [str(filled["target"])]
                    if filled.get("role"):
                        proposal.arguments["role"] = filled["role"]

        # Conservative unique candidate resolution (party / claim only)
        if intent == AgentIntent.CONFIRM_PARTY and not proposal.targets:
            role = proposal.arguments.get("role")
            role_unique = resolution.get("role_unique_parties") or {}
            if role and isinstance(role_unique.get(role), dict):
                proposal.targets = ["1"]
                proposal.arguments["role"] = role
            else:
                uniq = resolution.get("unique_candidate_party")
                if isinstance(uniq, dict) and uniq.get("display_index") and not role:
                    proposal.targets = [str(uniq["display_index"])]
                    if uniq.get("role"):
                        proposal.arguments["role"] = uniq["role"]
        if intent == AgentIntent.CONFIRM_CLAIM_DIRECTION and not proposal.targets:
            if resolution.get("unique_claim_index") is not None:
                proposal.targets = [str(resolution["unique_claim_index"])]
            elif int(resolution.get("claim_count") or 0) > 1:
                from backend.agent.clarification import ActionClarificationRenderer

                msg = ActionClarificationRenderer().render(
                    intent=intent,
                    missing_fields=["target"],
                    verdict="INCOMPLETE",
                    ctx=self._clarification_ctx(resolution, intent, proposal),
                )
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    missing_fields=["target"],
                    validation_result="claim_ambiguous",
                )
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message=msg,
                    missing_fields=["target"],
                    pending_action=self._pending_for(intent, proposal, ["target"]),
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                )

        if intent in needs_target:
            has_param_target = bool(
                proposal.arguments.get("evidence_item_id")
                or proposal.arguments.get("fact_key")
                or proposal.arguments.get("party_key")
            )
            if not proposal.targets and not has_param_target:
                from backend.agent.clarification import ActionClarificationRenderer

                msg = ActionClarificationRenderer().render(
                    intent=intent,
                    missing_fields=["target"],
                    verdict="INCOMPLETE",
                    ctx=self._clarification_ctx(resolution, intent, proposal),
                )
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    missing_fields=["target"],
                    validation_result="missing_target",
                )
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message=msg,
                    missing_fields=["target"],
                    pending_action=self._pending_for(intent, proposal, ["target"]),
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                )

        if intent == AgentIntent.AMEND_FACT:
            new_statement = (proposal.arguments.get("new_statement") or "").strip()
            if not new_statement:
                from backend.agent.clarification import ActionClarificationRenderer

                amend_t = (
                    f"事实{proposal.targets[0]}" if proposal.targets else None
                )
                msg = ActionClarificationRenderer().render(
                    intent=intent,
                    missing_fields=["new_statement"],
                    verdict="INCOMPLETE",
                    ctx=self._clarification_ctx(
                        resolution, intent, proposal, amend_target=amend_t
                    ),
                )
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    missing_fields=["new_statement"],
                    validation_result="missing_new_statement",
                )
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message=msg,
                    missing_fields=["new_statement"],
                    pending_action=self._pending_for(
                        intent, proposal, ["new_statement"]
                    ),
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                )

        if intent == AgentIntent.CONFIRM_CLAIM_DIRECTION and not proposal.targets:
            # Unique already handled; zero claims → incomplete
            if int(resolution.get("claim_count") or 0) == 0:
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    missing_fields=["target"],
                    validation_result="no_claims",
                )
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message="当前没有可确认的诉讼请求候选。",
                    missing_fields=["target"],
                    pending_action=None,
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                )

        if intent == AgentIntent.AMEND_CLAIM_DIRECTION:
            if proposal.arguments.get("amount") is None and not proposal.arguments.get(
                "description"
            ):
                # amount-only amend is common; if neither present → incomplete
                if "amount" not in proposal.arguments:
                    audit_action(
                        ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                        case_id=case_id,
                        conversation_id=conversation_id,
                        intent=intent,
                        missing_fields=["amount"],
                        validation_result="missing_claim_amend_fields",
                    )
                    return SafetyResult(
                        verdict=SafetyVerdict.INCOMPLETE,
                        proposal=proposal,
                        message="请明确要修改的诉讼请求金额或内容。",
                        missing_fields=["amount"],
                        pending_action=self._pending_for(
                            intent, proposal, ["amount"]
                        ),
                        audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    )

        audit_action(
            ActionAuditEvent.ACTION_VALIDATED,
            case_id=case_id,
            conversation_id=conversation_id,
            intent=intent,
            validation_result="ok",
        )
        return SafetyResult(
            verdict=SafetyVerdict.VALID,
            proposal=proposal,
            message="",
            audit_event=ActionAuditEvent.ACTION_VALIDATED,
        )

    @staticmethod
    def _pending_for(
        intent: AgentIntent, proposal: ActionProposal, missing: list[str]
    ) -> dict[str, Any]:
        return {
            "pending_action": intent.value,
            "missing_fields": list(missing),
            "targets": list(proposal.targets or []),
            "parameters": {
                k: v
                for k, v in (proposal.arguments or {}).items()
                if k
                not in {
                    "llm_reason",
                    "prompt_version",
                    "confidence",
                    "user_message",
                    "missing_fields",
                }
            },
            "role": (proposal.arguments or {}).get("role"),
        }

    @staticmethod
    def _clarification_ctx(
        resolution: dict[str, Any],
        intent: AgentIntent,
        proposal: ActionProposal,
        *,
        amend_target: str | None = None,
    ):
        from backend.agent.clarification import ClarificationContext

        return ClarificationContext(
            evidence_numbers=list(resolution.get("evidence_numbers") or []),
            fact_indices=list(resolution.get("fact_indices") or []),
            party_labels=list(resolution.get("party_labels") or []),
            claim_count=int(resolution.get("claim_count") or 0),
            amend_target=amend_target,
            role_hint=(proposal.arguments or {}).get("role")
            or resolution.get("role_hint"),
        )

    @staticmethod
    def _try_resolve_focus(
        intent: AgentIntent,
        raw_message: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve 它/这个/那个 via recent focus. Returns None if not deixis."""
        if not re.search(r"(它|这个|那个)", raw_message or ""):
            return None
        prefer = None
        if intent in {AgentIntent.ACCEPT_EVIDENCE, AgentIntent.EXCLUDE_EVIDENCE}:
            prefer = "EVIDENCE"
        elif intent in {
            AgentIntent.CONFIRM_FACT,
            AgentIntent.REJECT_FACT,
            AgentIntent.AMEND_FACT,
        }:
            prefer = "FACT"
        elif intent in {
            AgentIntent.CONFIRM_PARTY,
            AgentIntent.REJECT_PARTY,
            AgentIntent.AMEND_PARTY,
        }:
            prefer = "PARTY"
        mentions = list(resolution.get("recent_mentions") or [])
        focus = resolution.get("recent_focus")
        from backend.agent.clarification import resolve_unique_focus

        typed = (
            [m for m in mentions if m.get("entity_type") == prefer]
            if prefer
            else list(mentions)
        )
        if len(typed) > 1:
            return {"ambiguous": True}
        unique = resolve_unique_focus(mentions, prefer_type=prefer)
        if unique is None and isinstance(focus, dict):
            if prefer is None or focus.get("entity_type") == prefer:
                # Only trust stored focus when recent mentions aren't multi-ambiguous
                if len(typed) <= 1:
                    unique = focus
        if unique is None:
            return {"ambiguous": True}
        return {"target": unique.get("display_number")}

    def _validate_create_party(
        self,
        proposal: ActionProposal,
        *,
        case_id: UUID | None,
        conversation_id: UUID | None,
    ) -> SafetyResult:
        args = proposal.arguments
        mode = args.get("mode")
        parties = list(args.get("parties") or [])
        missing = list(proposal.missing_fields)

        # Legacy single role/name from router
        if not mode and args.get("role") and args.get("name") is not None:
            name = PartyNameValidator.normalize(args.get("name"))
            role = args.get("role")
            if not PartyNameValidator.is_valid(name):
                # Treat invalid extracted name as missing, never mutate
                field = {
                    "PLAINTIFF": "plaintiff_name",
                    "DEFENDANT": "defendant_name",
                    "THIRD_PARTY": "third_party_name",
                }.get(str(role), "name")
                msg = self._ask_for_party_names(
                    requested_roles=[str(role)] if role else [],
                    partial_parties=[],
                )
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INVALID,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=AgentIntent.CREATE_PARTY,
                    missing_fields=[field],
                    validation_result="invalid_party_name",
                )
                pending = {
                    "pending_action": "CREATE_PARTIES",
                    "requested_roles": [str(role)] if role else ["PLAINTIFF"],
                    "missing_fields": [field],
                    "parties": [],
                }
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message=msg,
                    missing_fields=[field],
                    pending_action=pending,
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INVALID,
                )
            # Rewrite to validated single
            proposal.arguments = {
                "mode": "single",
                "role": role,
                "name": name,
                "parties": [{"role": str(role), "name": name}],
            }
            audit_action(
                ActionAuditEvent.ACTION_VALIDATED,
                case_id=case_id,
                conversation_id=conversation_id,
                intent=AgentIntent.CREATE_PARTY,
                validation_result="ok_single",
            )
            return SafetyResult(
                verdict=SafetyVerdict.VALID,
                proposal=proposal,
                message="",
                audit_event=ActionAuditEvent.ACTION_VALIDATED,
            )

        # Multi / structured
        requested_roles = list(args.get("requested_roles") or [])
        if mode == "multi" or (requested_roles and len(requested_roles) >= 2):
            valid = []
            for p in parties:
                n = PartyNameValidator.normalize(p.get("name"))
                r = p.get("role")
                if r and PartyNameValidator.is_valid(n):
                    valid.append({"role": str(r), "name": n})
            have = {p["role"] for p in valid}
            need_roles = requested_roles or ["PLAINTIFF", "DEFENDANT"]
            missing = []
            if "PLAINTIFF" in need_roles and "PLAINTIFF" not in have:
                missing.append("plaintiff_name")
            if "DEFENDANT" in need_roles and "DEFENDANT" not in have:
                missing.append("defendant_name")
            if "THIRD_PARTY" in need_roles and "THIRD_PARTY" not in have:
                missing.append("third_party_name")
            if missing:
                msg = self._ask_for_party_names(need_roles, valid)
                audit_action(
                    ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    intent=AgentIntent.CREATE_PARTY,
                    missing_fields=missing,
                    validation_result="incomplete_multi",
                )
                return SafetyResult(
                    verdict=SafetyVerdict.INCOMPLETE,
                    proposal=proposal,
                    message=msg,
                    missing_fields=missing,
                    pending_action={
                        "pending_action": "CREATE_PARTIES",
                        "requested_roles": need_roles,
                        "missing_fields": missing,
                        "parties": valid,
                    },
                    audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                )
            proposal.arguments = {
                "mode": "multi",
                "requested_roles": need_roles,
                "parties": valid,
            }
            audit_action(
                ActionAuditEvent.ACTION_VALIDATED,
                case_id=case_id,
                conversation_id=conversation_id,
                intent=AgentIntent.CREATE_PARTY,
                validation_result="ok_multi",
            )
            return SafetyResult(
                verdict=SafetyVerdict.VALID,
                proposal=proposal,
                message="",
                audit_event=ActionAuditEvent.ACTION_VALIDATED,
            )

        # Single structured with missing name
        role = args.get("role")
        name = PartyNameValidator.normalize(args.get("name"))
        if role and not PartyNameValidator.is_valid(name):
            field = {
                "PLAINTIFF": "plaintiff_name",
                "DEFENDANT": "defendant_name",
                "THIRD_PARTY": "third_party_name",
            }.get(str(role), "name")
            msg = self._ask_for_party_names([str(role)], [])
            audit_action(
                ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                case_id=case_id,
                conversation_id=conversation_id,
                intent=AgentIntent.CREATE_PARTY,
                missing_fields=[field],
                validation_result="missing_single_name",
            )
            return SafetyResult(
                verdict=SafetyVerdict.INCOMPLETE,
                proposal=proposal,
                message=msg,
                missing_fields=[field],
                pending_action={
                    "pending_action": "CREATE_PARTIES",
                    "requested_roles": [str(role)],
                    "missing_fields": [field],
                    "parties": [],
                },
                audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
            )

        if missing:
            msg = self._ask_for_party_names(
                list(args.get("requested_roles") or ([role] if role else [])),
                parties,
            )
            audit_action(
                ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
                case_id=case_id,
                conversation_id=conversation_id,
                intent=AgentIntent.CREATE_PARTY,
                missing_fields=missing,
                validation_result="declared_missing",
            )
            return SafetyResult(
                verdict=SafetyVerdict.INCOMPLETE,
                proposal=proposal,
                message=msg,
                missing_fields=missing,
                pending_action={
                    "pending_action": "CREATE_PARTIES",
                    "requested_roles": list(
                        args.get("requested_roles") or ([role] if role else [])
                    ),
                    "missing_fields": missing,
                    "parties": [
                        p
                        for p in parties
                        if PartyNameValidator.is_valid(p.get("name"))
                    ],
                },
                audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
            )

        if role and PartyNameValidator.is_valid(name):
            proposal.arguments = {
                "mode": "single",
                "role": role,
                "name": name,
                "parties": [{"role": str(role), "name": name}],
            }
            audit_action(
                ActionAuditEvent.ACTION_VALIDATED,
                case_id=case_id,
                conversation_id=conversation_id,
                intent=AgentIntent.CREATE_PARTY,
                validation_result="ok",
            )
            return SafetyResult(
                verdict=SafetyVerdict.VALID,
                proposal=proposal,
                message="",
                audit_event=ActionAuditEvent.ACTION_VALIDATED,
            )

        audit_action(
            ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
            case_id=case_id,
            conversation_id=conversation_id,
            intent=AgentIntent.CREATE_PARTY,
            missing_fields=["name"],
            validation_result="fallback_incomplete",
        )
        return SafetyResult(
            verdict=SafetyVerdict.INCOMPLETE,
            proposal=proposal,
            message="可以。请告诉我要录入的当事人角色和具体名称。",
            missing_fields=["name"],
            pending_action={
                "pending_action": "CREATE_PARTIES",
                "requested_roles": [],
                "missing_fields": ["plaintiff_name", "defendant_name"],
                "parties": [],
            },
            audit_event=ActionAuditEvent.ACTION_REJECTED_INCOMPLETE,
        )

    @staticmethod
    def _ask_for_party_names(
        requested_roles: list[str], partial_parties: list[dict[str, str]]
    ) -> str:
        role_zh = {
            "PLAINTIFF": "原告",
            "DEFENDANT": "被告",
            "THIRD_PARTY": "第三人",
        }
        have = {p["role"]: p["name"] for p in partial_parties}
        if set(requested_roles) >= {"PLAINTIFF", "DEFENDANT"} and not have:
            return (
                "可以。请告诉我原告和被告的具体名称。\n\n"
                "例如：\n"
                "原告：四川维海科技有限公司\n"
                "被告：四川富茂置业有限公司"
            )
        parts = []
        for role in requested_roles or ["PLAINTIFF", "DEFENDANT"]:
            if role in have:
                parts.append(f"已收到{role_zh.get(role, role)}：{have[role]}")
            else:
                parts.append(f"请告诉我{role_zh.get(role, role)}的具体名称。")
        if not parts:
            return "可以。请告诉我要录入的当事人具体名称。"
        head = "可以。" if not have else "好的，还差一些信息。"
        return head + "\n".join(parts)

    def merge_pending_with_message(
        self,
        pending: dict[str, Any],
        message: str,
    ) -> ActionProposal:
        """Fill pending action slots from a follow-up short answer / phrase."""
        pending_name = str(pending.get("pending_action") or "")
        if pending_name in {"CREATE_PARTIES", "CREATE_PARTY"}:
            return self._merge_pending_create_party(pending, message)

        try:
            intent = AgentIntent(pending_name)
        except ValueError:
            intent = AgentIntent.UNKNOWN

        targets = list(pending.get("targets") or [])
        params = dict(pending.get("parameters") or {})
        missing = list(pending.get("missing_fields") or [])
        text = (message or "").strip()

        # Strip common fillers
        stmt = re.sub(r"^(改成|修改为|改为|为|成)[:：\s]*", "", text).strip()

        if "target" in missing:
            nums: list[str] = []
            if m := re.search(r"(?:证据|事实|当事人|原告|被告|第三人|诉讼请求)\s*([0-9]+)", text):
                nums = [m.group(1)]
            elif re.fullmatch(r"[0-9]+", text):
                nums = [text]
            if nums:
                targets = nums
                missing = [f for f in missing if f != "target"]

        if "new_statement" in missing and stmt and not re.fullmatch(r"[0-9]+", text):
            # Don't treat bare number as statement
            if not re.fullmatch(
                r"(证据|事实|当事人|诉讼请求)\s*[0-9]+", text
            ):
                params["new_statement"] = stmt
                missing = [f for f in missing if f != "new_statement"]

        if pending.get("role") and "role" not in params:
            params["role"] = pending["role"]

        return ActionProposal(
            intent=intent,
            arguments=params,
            targets=targets,
            missing_fields=missing,
            raw_message=message,
        )

    def _merge_pending_create_party(
        self, pending: dict[str, Any], message: str
    ) -> ActionProposal:
        """Fill pending CREATE_PARTIES slots from a follow-up message."""
        extracted = extract_parties_from_text(message)
        existing = list(pending.get("parties") or [])
        by_role = {
            p["role"]: p for p in existing if PartyNameValidator.is_valid(p.get("name"))
        }
        for p in extracted:
            if PartyNameValidator.is_valid(p.get("name")):
                by_role[p["role"]] = {
                    "role": p["role"],
                    "name": PartyNameValidator.normalize(p["name"]),
                }

        # Bare name when only one role missing
        requested = list(pending.get("requested_roles") or [])
        missing = list(pending.get("missing_fields") or [])
        if len(missing) == 1 and not extracted:
            cand = PartyNameValidator.normalize(message)
            cand = re.sub(r"^(原告|被告|第三人)\s*(是|为|：|:)?\s*", "", cand).strip()
            role_for_field = {
                "plaintiff_name": "PLAINTIFF",
                "defendant_name": "DEFENDANT",
                "third_party_name": "THIRD_PARTY",
            }.get(missing[0])
            if role_for_field and PartyNameValidator.is_valid(cand):
                by_role[role_for_field] = {"role": role_for_field, "name": cand}

        parties = list(by_role.values())
        return ActionProposal(
            intent=AgentIntent.CREATE_PARTY,
            arguments={
                "mode": "multi" if len(requested) >= 2 else "single",
                "requested_roles": requested,
                "parties": parties,
                **(
                    {
                        "role": parties[0]["role"],
                        "name": parties[0]["name"],
                    }
                    if len(parties) == 1 and len(requested) <= 1
                    else {}
                ),
            },
            missing_fields=[],
            raw_message=message,
        )


def is_cancel_pending(message: str) -> bool:
    t = (message or "").strip()
    if re.search(
        r"^(算了|先不录|不要了|取消|不用了|暂时不录|先不录入|"
        r"先不弄|先不弄这个|先不改|先不改了|暂时不弄)([。.!！]?)$",
        t,
    ):
        return True
    return bool(
        re.search(
            r"^(算了[，,]\s*)?(先不录|不要了|不用了|取消|先不弄(这个)?|"
            r"先不改(了)?|暂时不弄)([。.!！]?)$",
            t,
        )
    )


def looks_like_pending_slot_fill(
    pending: dict[str, Any], message: str, routed_intent: AgentIntent
) -> bool:
    """Whether a follow-up message is completing pending slots (not a new action)."""
    if not pending:
        return False
    pending_name = str(pending.get("pending_action") or "")
    text = (message or "").strip()
    if not text:
        return False
    # Questions are not slot fills
    if re.search(r"(哪一条|哪一份|哪个|什么|为什么|怎么|吗|？|\?)$", text):
        return False

    try:
        if pending_name == "CREATE_PARTIES":
            pending_intent = AgentIntent.CREATE_PARTY
        else:
            pending_intent = AgentIntent(pending_name)
    except ValueError:
        pending_intent = (
            AgentIntent.CREATE_PARTY
            if pending_name == "CREATE_PARTIES"
            else AgentIntent.UNKNOWN
        )
    if routed_intent in MUTATION_INTENTS and routed_intent != pending_intent:
        # Same family CREATE ok
        if not (
            pending_intent == AgentIntent.CREATE_PARTY
            and routed_intent == AgentIntent.CREATE_PARTY
        ):
            return False

    if routed_intent == pending_intent:
        return True

    missing = list(pending.get("missing_fields") or [])
    if pending_name in {"CREATE_PARTIES", "CREATE_PARTY"} or "name" in "".join(missing):
        if extract_parties_from_text(text):
            return True
        cand = PartyNameValidator.normalize(text)
        cand = re.sub(r"^(原告|被告|第三人)\s*(是|为|：|:)?\s*", "", cand).strip()
        if PartyNameValidator.is_valid(cand):
            return True
        return False

    if "target" in missing:
        if re.fullmatch(r"[0-9]+", text):
            return True
        if re.search(r"(证据|事实|当事人|原告|被告|第三人|诉讼请求)\s*[0-9]+", text):
            return True
        return False

    if "new_statement" in missing:
        if len(text) >= 4 and not re.fullmatch(r"[0-9]+", text):
            return True
        return False

    return routed_intent in {
        AgentIntent.CASE_CONVERSATION,
        AgentIntent.UNKNOWN,
    }
