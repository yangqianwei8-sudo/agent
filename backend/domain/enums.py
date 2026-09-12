"""Domain and persistence enums (string values stored in DB)."""

from enum import StrEnum


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    ARCHIVED = "ARCHIVED"


class PartyRole(StrEnum):
    PLAINTIFF = "PLAINTIFF"
    DEFENDANT = "DEFENDANT"
    THIRD_PARTY = "THIRD_PARTY"
    OTHER = "OTHER"


class PartyType(StrEnum):
    ORG = "ORG"
    PERSON = "PERSON"


class LayerStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class MaterialLifeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    VOID = "VOID"


class ParseStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class EvidenceAcceptance(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXCLUDED = "EXCLUDED"


class FactStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class IssueStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class IssueSourceType(StrEnum):
    AI_PROPOSED = "AI_PROPOSED"
    LAWYER_CREATED = "LAWYER_CREATED"
    LAWYER_REFINED = "LAWYER_REFINED"
    OPPONENT_RAISED = "OPPONENT_RAISED"
    COURT_SUMMARIZED = "COURT_SUMMARIZED"


class IssueLinkRole(StrEnum):
    SUPPORT = "SUPPORT"
    ADVERSE = "ADVERSE"
    CONTEXT = "CONTEXT"


class IssueLinkStatus(StrEnum):
    ACTIVE = "ACTIVE"
    VOID = "VOID"


class FactImportance(StrEnum):
    CORE = "CORE"
    SUPPORTING = "SUPPORTING"
    BACKGROUND = "BACKGROUND"


class LinkRole(StrEnum):
    PROVES = "PROVES"
    CORROBORATES = "CORROBORATES"
    CONTEXT = "CONTEXT"


class LinkStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    VOID = "VOID"


class SpanRoleInItem(StrEnum):
    PRIMARY = "PRIMARY"
    ATTACHMENT = "ATTACHMENT"
    SIGNATURE_PAGE = "SIGNATURE_PAGE"


class WorkflowInstanceStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    WAITING_RETRY = "WAITING_RETRY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    FAILED = "FAILED"
    SUCCEEDED = "SUCCEEDED"


class SkillExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class MessageRole(StrEnum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class DecisionResult(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SELECTED = "SELECTED"
    AMENDED = "AMENDED"


class CommandStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DONE = "DONE"


class DraftStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED_BY_LAWYER = "APPROVED_BY_LAWYER"
    STALE = "STALE"


class ClaimType(StrEnum):
    PAYMENT = "PAYMENT"
    LIQUIDATED_DAMAGES = "LIQUIDATED_DAMAGES"
    DAMAGES = "DAMAGES"
    TERMINATION = "TERMINATION"
    SPECIFIC_PERFORMANCE = "SPECIFIC_PERFORMANCE"
    PERFORMANCE = "PERFORMANCE"
    DECLARATORY = "DECLARATORY"
    CONFIRMATION = "CONFIRMATION"
    INTEREST = "INTEREST"
    COSTS = "COSTS"
    OTHER = "OTHER"


class ClaimStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ClaimSourceType(StrEnum):
    AI_PROPOSED = "AI_PROPOSED"
    LAWYER_CREATED = "LAWYER_CREATED"
    LAWYER_REFINED = "LAWYER_REFINED"
    OPPONENT_RAISED = "OPPONENT_RAISED"
    COURT_ADJUSTED = "COURT_ADJUSTED"


class ClaimIssueLinkRole(StrEnum):
    BASIS = "BASIS"
    LIMITATION = "LIMITATION"
    CONTEXT = "CONTEXT"


class ClaimFactLinkRole(StrEnum):
    BASIS = "BASIS"
    AMOUNT_BASIS = "AMOUNT_BASIS"
    LIMITATION = "LIMITATION"
    CONTEXT = "CONTEXT"


class ClaimLinkStatus(StrEnum):
    ACTIVE = "ACTIVE"
    VOID = "VOID"


class StaleReason(StrEnum):
    PARTY_CHANGED = "PARTY_CHANGED"
    FACT_REJECTED = "FACT_REJECTED"
    FACT_AMENDED = "FACT_AMENDED"
    CLAIM_DIRECTION_CHANGED = "CLAIM_DIRECTION_CHANGED"
    CLAIM_CHANGED = "CLAIM_CHANGED"
    ISSUE_CHANGED = "ISSUE_CHANGED"
    EVIDENCE_EXCLUDED = "EVIDENCE_EXCLUDED"
    EXTRACT_REBUILT = "EXTRACT_REBUILT"


class StaleEvent(StrEnum):
    PARTY_CHANGED = "PartyChanged"
    FACT_REJECTED = "FactRejected"
    FACT_AMENDED = "FactAmended"
    CLAIM_DIRECTION_CHANGED = "ClaimDirectionChanged"
    CLAIM_CHANGED = "ClaimChanged"
    ISSUE_CHANGED = "IssueChanged"
    EVIDENCE_EXCLUDED = "EvidenceExcluded"
    EXTRACTED_CONTENT_REBUILT = "ExtractedContentRebuilt"


class PositionSide(StrEnum):
    OUR = "OUR"
    OPPONENT = "OPPONENT"


class PositionType(StrEnum):
    ASSERTION = "ASSERTION"
    ANTICIPATED_DEFENSE = "ANTICIPATED_DEFENSE"
    FORMAL_DEFENSE = "FORMAL_DEFENSE"


class PositionSourceType(StrEnum):
    AI_PROPOSED = "AI_PROPOSED"
    LAWYER_CREATED = "LAWYER_CREATED"
    LAWYER_REFINED = "LAWYER_REFINED"
    OPPONENT_MATERIAL = "OPPONENT_MATERIAL"


class PositionStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ProofTaskStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ADOPTED = "ADOPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    WAIVED = "WAIVED"


class ProofTaskSourceType(StrEnum):
    AI_PROPOSED = "AI_PROPOSED"
    LAWYER_CREATED = "LAWYER_CREATED"
    LAWYER_REFINED = "LAWYER_REFINED"


class ProofTaskFactLinkRole(StrEnum):
    SUPPORT = "SUPPORT"
    ADVERSE = "ADVERSE"
    CONTEXT = "CONTEXT"


class ConflictStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ConflictSourceType(StrEnum):
    AI_DETECTED = "AI_DETECTED"
    LAWYER_CREATED = "LAWYER_CREATED"


class ConflictFactRole(StrEnum):
    SIDE_A = "SIDE_A"
    SIDE_B = "SIDE_B"
    CONTEXT = "CONTEXT"


class ProofGapType(StrEnum):
    FACT = "FACT"
    EVIDENCE = "EVIDENCE"
    SOURCE = "SOURCE"
    LEGAL_RESEARCH = "LEGAL_RESEARCH"


class ProofGapStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"
    SUPERSEDED = "SUPERSEDED"


class ProofGapSourceType(StrEnum):
    AI_DETECTED = "AI_DETECTED"
    LAWYER_CREATED = "LAWYER_CREATED"


class LawyerAssessmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class IssueLegalTheoryRole(StrEnum):
    OUR_THEORY = "OUR_THEORY"
    COUNTER_THEORY = "COUNTER_THEORY"
    CONTEXT = "CONTEXT"


class ProofState(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


class LawyerJudgmentState(StrEnum):
    NOT_ANALYZED = "NOT_ANALYZED"
    RESEARCHING = "RESEARCHING"
    LAWYER_ASSESSMENT_FORMED = "LAWYER_ASSESSMENT_FORMED"
