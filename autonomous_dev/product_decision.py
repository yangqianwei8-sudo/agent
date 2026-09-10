"""Product decision packet builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProductDecisionPacket:
    question: str
    why_owner_required: str
    option_a: str
    impact_a: str
    option_b: str
    impact_b: str
    recommended_option: str
    blocked_task: str

    def to_markdown(self) -> str:
        return (
            "## Product Decision Required\n\n"
            f"**Question:** {self.question}\n\n"
            f"**Why owner decision is required:** {self.why_owner_required}\n\n"
            f"**Option A:** {self.option_a}\n\n"
            f"**Impact A:** {self.impact_a}\n\n"
            f"**Option B:** {self.option_b}\n\n"
            f"**Impact B:** {self.impact_b}\n\n"
            f"**Recommended option:** {self.recommended_option}\n\n"
            f"**Blocked task:** {self.blocked_task}\n"
        )
