"""Unit tests — legacy ClaimDirection → Claim identity matching."""

from __future__ import annotations

import uuid

from backend.migration.claim_identity import (
    LegacyClaimItem,
    assign_claim_keys_for_direction,
    match_items_to_prior_keys,
    new_claim_key,
)


def _item(
    claim_type: str,
    description: str,
    *,
    amount: float | None = None,
    index: int = 0,
) -> LegacyClaimItem:
    return LegacyClaimItem(
        claim_type=claim_type,
        description=description,
        amount=amount,
        currency="CNY" if amount is not None else None,
        index=index,
    )


def _payload(*descriptions: str, claim_type: str = "PAYMENT") -> list[dict]:
    return [
        {
            "claim_type": claim_type,
            "description": d,
            "amount": 100000 if claim_type == "PAYMENT" else None,
            "currency": "CNY" if claim_type == "PAYMENT" else None,
        }
        for d in descriptions
    ]


def test_case_a_reorder_preserves_identity() -> None:
    direction = uuid.uuid4()
    key_map = assign_claim_keys_for_direction(
        direction,
        [
            (1, _payload("诉请A支付服务费", "诉请B违约金")),
            (2, _payload("诉请B违约金", "诉请A支付服务费")),
        ],
    )
    a_v1, b_v1 = key_map[(1, 0)], key_map[(1, 1)]
    b_v2, a_v2 = key_map[(2, 0)], key_map[(2, 1)]  # reordered in payload
    assert a_v1 == a_v2
    assert b_v1 == b_v2
    assert a_v1 != b_v1


def test_case_b_insert_preserves_ab_new_c() -> None:
    direction = uuid.uuid4()
    key_map = assign_claim_keys_for_direction(
        direction,
        [
            (1, _payload("诉请A", "诉请B")),
            (2, _payload("诉请A", "诉请C新增", "诉请B")),
        ],
    )
    a1, b1 = key_map[(1, 0)], key_map[(1, 1)]
    a2, c2, b2 = key_map[(2, 0)], key_map[(2, 1)], key_map[(2, 2)]
    assert a1 == a2
    assert b1 == b2
    assert c2 not in {a1, b1}


def test_case_c_delete_preserves_b_not_a_to_b() -> None:
    direction = uuid.uuid4()
    key_map = assign_claim_keys_for_direction(
        direction,
        [
            (1, _payload("诉请A", "诉请B")),
            (2, _payload("诉请B")),
        ],
    )
    a1, b1 = key_map[(1, 0)], key_map[(1, 1)]
    b2 = key_map[(2, 0)]
    assert b1 == b2
    assert a1 != b2


def test_case_d_material_rewrite_new_identity() -> None:
    direction = uuid.uuid4()
    key_map = assign_claim_keys_for_direction(
        direction,
        [
            (1, _payload("支付服务费", claim_type="PAYMENT")),
            (2, _payload("解除合同", claim_type="TERMINATION")),
        ],
    )
    pay_v1 = key_map[(1, 0)]
    term_v2 = key_map[(2, 0)]
    assert pay_v1 != term_v2


def test_case_e_uncertain_match_splits() -> None:
    direction = uuid.uuid4()
    prev_items = [_item("PAYMENT", "诉请甲", amount=100000, index=0)]
    prev_keys = [
        new_claim_key(
            claim_direction_key=direction,
            direction_version=1,
            item=prev_items[0],
            slot="init:0",
        )
    ]
    curr_items = [
        _item("PAYMENT", "诉请乙完全不同", amount=200000, index=0),
        _item("PAYMENT", "诉请丙也不同", amount=300000, index=1),
    ]
    keys = match_items_to_prior_keys(
        prev_items,
        prev_keys,
        curr_items,
        claim_direction_key=direction,
        direction_version=2,
    )
    assert keys[0] != prev_keys[0]
    assert keys[1] != prev_keys[0]
    assert keys[0] != keys[1]
