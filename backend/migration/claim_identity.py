"""Legacy ClaimDirection payload → stable Claim identity mapping.

``claim_key`` must represent the same relief item across versions.
Array index alone is NOT a stable identity (insert/delete/reorder/rewrite).

Principle: false split > false merge.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

# Namespace for deterministic uuid5 keys during legacy migration only.
LEGACY_CLAIM_NS = uuid.UUID("00000000-0000-4000-8000-000000000099")

MIN_MATCH_SCORE = 0.72
AMBIGUITY_MARGIN = 0.05


@dataclass(frozen=True)
class LegacyClaimItem:
    claim_type: str
    description: str
    amount: float | None
    currency: str | None
    index: int

    @classmethod
    def from_payload(cls, item: dict[str, Any], index: int) -> LegacyClaimItem:
        desc = item.get("description") or item.get("title") or "诉讼请求"
        amount = item.get("amount")
        return cls(
            claim_type=str(item.get("claim_type") or "OTHER"),
            description=desc,
            amount=float(amount) if amount is not None else None,
            currency=item.get("currency"),
            index=index,
        )


def normalize_statement(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", "", t)
    return t.casefold()


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_statement(a), normalize_statement(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def amounts_compatible(
    prev: float | None, curr: float | None, *, text_sim: float
) -> bool:
    if prev is None and curr is None:
        return True
    if prev is None or curr is None:
        # Amount added/removed on otherwise similar text — still same claim.
        return text_sim >= 0.85
    if abs(prev - curr) < 0.01:
        return True
    denom = max(abs(prev), abs(curr), 1.0)
    return abs(prev - curr) / denom <= 0.15


def match_score(prev: LegacyClaimItem, curr: LegacyClaimItem) -> float:
    if prev.claim_type != curr.claim_type:
        return 0.0
    sim = text_similarity(prev.description, curr.description)
    if sim < 0.5:
        return 0.0
    if not amounts_compatible(prev.amount, curr.amount, text_sim=sim):
        return sim * 0.4
    return sim


def content_fingerprint(item: LegacyClaimItem) -> str:
    parts = [
        item.claim_type,
        normalize_statement(item.description),
        str(item.amount) if item.amount is not None else "",
        item.currency or "",
    ]
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def new_claim_key(
    *,
    claim_direction_key: uuid.UUID,
    direction_version: int,
    item: LegacyClaimItem,
    slot: str,
) -> uuid.UUID:
    fp = content_fingerprint(item)
    seed = f"{claim_direction_key}:v{direction_version}:{slot}:{fp}"
    return uuid.uuid5(LEGACY_CLAIM_NS, seed)


def match_items_to_prior_keys(
    prev_items: list[LegacyClaimItem],
    prev_keys: list[uuid.UUID],
    curr_items: list[LegacyClaimItem],
    *,
    claim_direction_key: uuid.UUID,
    direction_version: int,
) -> list[uuid.UUID]:
    """Map current-version items to prior claim_keys or allocate new ones."""
    if len(prev_items) != len(prev_keys):
        raise ValueError("prev_items and prev_keys length mismatch")

    curr_keys: list[uuid.UUID] = []
    used_prev: set[int] = set()

    for ci, curr in enumerate(curr_items):
        scored: list[tuple[int, float]] = []
        for pi, prev in enumerate(prev_items):
            if pi in used_prev:
                continue
            scored.append((pi, match_score(prev, curr)))
        scored.sort(key=lambda x: x[1], reverse=True)

        assigned: uuid.UUID | None = None
        if scored and scored[0][1] >= MIN_MATCH_SCORE:
            best_pi, best_score = scored[0]
            second_score = scored[1][1] if len(scored) > 1 else 0.0
            if best_score >= second_score + AMBIGUITY_MARGIN:
                assigned = prev_keys[best_pi]
                used_prev.add(best_pi)

        if assigned is None:
            assigned = new_claim_key(
                claim_direction_key=claim_direction_key,
                direction_version=direction_version,
                item=curr,
                slot=f"new:{ci}",
            )
        curr_keys.append(assigned)

    return curr_keys


def assign_claim_keys_for_direction(
    claim_direction_key: uuid.UUID,
    version_rows: list[tuple[int, list[dict[str, Any]]]],
) -> dict[tuple[int, int], uuid.UUID]:
    """Return (direction_version, item_index) -> claim_key for all items."""
    if not version_rows:
        return {}

    version_rows = sorted(version_rows, key=lambda x: x[0])
    result: dict[tuple[int, int], uuid.UUID] = {}
    prev_items: list[LegacyClaimItem] = []
    prev_keys: list[uuid.UUID] = []

    for direction_version, raw_items in version_rows:
        curr_items = [
            LegacyClaimItem.from_payload(item, idx) for idx, item in enumerate(raw_items)
        ]
        if not prev_items:
            keys = [
                new_claim_key(
                    claim_direction_key=claim_direction_key,
                    direction_version=direction_version,
                    item=item,
                    slot=f"init:{item.index}",
                )
                for item in curr_items
            ]
        else:
            keys = match_items_to_prior_keys(
                prev_items,
                prev_keys,
                curr_items,
                claim_direction_key=claim_direction_key,
                direction_version=direction_version,
            )

        for idx, key in enumerate(keys):
            result[(direction_version, idx)] = key
        prev_items = curr_items
        prev_keys = keys

    return result


def build_legacy_source_ref(
    *,
    claim_direction_key: uuid.UUID,
    direction_version: int,
    item_index: int,
) -> str:
    return (
        f"claim_direction:{claim_direction_key}:v{direction_version}:idx{item_index}"
    )


def group_claim_direction_rows(
    rows: list[Any],
) -> dict[uuid.UUID, list[tuple[Any, list[dict[str, Any]]]]]:
    """Group DB rows by claim_direction_key preserving version order."""
    grouped: dict[uuid.UUID, list[tuple[Any, list[dict[str, Any]]]]] = {}
    for row in rows:
        payload = row.payload or {}
        items = payload.get("claims") or []
        if not items:
            continue
        key = row.claim_direction_key
        grouped.setdefault(key, []).append((row, items))
    for key in grouped:
        grouped[key].sort(key=lambda x: x[0].version)
    return grouped


def insert_claims_from_claim_directions(
    conn: Any,
    rows: list[Any],
    *,
    include_provenance_columns: bool,
) -> None:
    """Insert Claim rows from ClaimDirection history using content-based identity."""
    import sqlalchemy as sa

    grouped = group_claim_direction_rows(rows)
    prev_row_id: dict[tuple[str, int], uuid.UUID] = {}

    for direction_key, version_entries in grouped.items():
        version_items = [(entry[0].version, entry[1]) for entry in version_entries]
        key_map = assign_claim_keys_for_direction(direction_key, version_items)

        for row, items in version_entries:
            for idx, item in enumerate(items):
                claim_key = key_map[(row.version, idx)]
                claim_id = uuid.uuid4()
                desc = item.get("description") or item.get("title") or "诉讼请求"
                title = desc[:500] if len(desc) > 500 else desc
                amount = item.get("amount")
                amount_suggested = amount is not None and row.status == "CANDIDATE"
                supersedes_id = prev_row_id.get((str(claim_key), row.version - 1))
                legacy_ref = build_legacy_source_ref(
                    claim_direction_key=direction_key,
                    direction_version=row.version,
                    item_index=idx,
                )

                params: dict[str, Any] = {
                    "id": claim_id,
                    "claim_key": claim_key,
                    "case_id": row.case_id,
                    "version": row.version,
                    "is_current": row.is_current,
                    "claim_type": item.get("claim_type") or "OTHER",
                    "title": title,
                    "statement": desc,
                    "amount": amount,
                    "currency": item.get("currency"),
                    "amount_is_suggested": amount_suggested,
                    "status": row.status,
                    "source_type": "LAWYER_CREATED",
                    "supersedes_id": supersedes_id,
                    "confirm_decision_id": row.confirm_decision_id,
                    "legacy_key": direction_key,
                    "stale": row.stale,
                    "stale_reason": row.stale_reason,
                    "stale_at": row.stale_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }

                if include_provenance_columns:
                    params["legacy_version"] = row.version
                    params["legacy_index"] = idx
                    params["legacy_source_ref"] = legacy_ref
                    sql = """
                        INSERT INTO claims (
                            id, claim_key, case_id, version, is_current,
                            claim_type, title, statement, amount, currency,
                            amount_is_suggested, status, source_type,
                            supersedes_id, confirm_decision_id,
                            legacy_claim_direction_key,
                            legacy_claim_direction_version,
                            legacy_claim_index, legacy_source_ref,
                            stale, stale_reason, stale_at,
                            created_at, updated_at
                        ) VALUES (
                            :id, :claim_key, :case_id, :version, :is_current,
                            :claim_type, :title, :statement, :amount, :currency,
                            :amount_is_suggested, :status, :source_type,
                            :supersedes_id, :confirm_decision_id,
                            :legacy_key, :legacy_version, :legacy_index,
                            :legacy_source_ref,
                            :stale, :stale_reason, :stale_at,
                            :created_at, :updated_at
                        )
                    """
                else:
                    sql = """
                        INSERT INTO claims (
                            id, claim_key, case_id, version, is_current,
                            claim_type, title, statement, amount, currency,
                            amount_is_suggested, status, source_type,
                            supersedes_id, confirm_decision_id,
                            legacy_claim_direction_key, stale, stale_reason, stale_at,
                            created_at, updated_at
                        ) VALUES (
                            :id, :claim_key, :case_id, :version, :is_current,
                            :claim_type, :title, :statement, :amount, :currency,
                            :amount_is_suggested, :status, :source_type,
                            :supersedes_id, :confirm_decision_id,
                            :legacy_key, :stale, :stale_reason, :stale_at,
                            :created_at, :updated_at
                        )
                    """

                conn.execute(sa.text(sql), params)
                prev_row_id[(str(claim_key), row.version)] = claim_id
