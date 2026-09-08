"""Workflow Runtime DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from backend.models import NodeRun, WorkflowInstance


@dataclass
class RuntimeResult:
    instance: WorkflowInstance
    node_run: NodeRun | None = None
    command_id: UUID | None = None
    idempotent_replay: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
