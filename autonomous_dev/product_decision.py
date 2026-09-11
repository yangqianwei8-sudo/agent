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
    recommendation_reason: str = ""

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

    def to_chinese_markdown(self) -> str:
        return (
            "【需要产品决策】\n\n"
            f"问题：\n{self.question}\n\n"
            f"为什么必须由你决定：\n{self.why_owner_required}\n\n"
            f"方案 A：\n{self.option_a}\n"
            f"产品影响：\n{self.impact_a}\n\n"
            f"方案 B：\n{self.option_b}\n"
            f"产品影响：\n{self.impact_b}\n\n"
            f"我的推荐：\n{self.recommended_option}\n"
            f"推荐理由：\n{self.recommendation_reason or self.recommended_option}\n\n"
            "你只需要回复：A / B / 其他"
        )
