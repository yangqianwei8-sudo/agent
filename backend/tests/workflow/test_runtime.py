"""Phase 3 Workflow Runtime tests — via Runtime API only."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.services import DomainService
from backend.models import NodeRun, SystemCommand
from backend.tests.workflow.helpers import ensure_pleading_prep_template, node_by_code
from backend.workflow import WorkflowConflictError, WorkflowRuntime


@pytest.fixture
def case_id(db_session: Session, owner_id: uuid.UUID) -> uuid.UUID:
    case = DomainService(db_session).create_case(title="WF Case", owner_user_id=owner_id)
    return case.id


@pytest.fixture
def runtime(db_session: Session, case_id: uuid.UUID) -> WorkflowRuntime:
    ensure_pleading_prep_template(db_session)
    return WorkflowRuntime(db_session)


def test_n0_n9_mapping_seeded(db_session: Session) -> None:
    """N0–N9 is a refinement of the frozen pleading-prep pipeline (no semantic conflict)."""
    template = ensure_pleading_prep_template(db_session)
    expected = [
        "N0_CREATE",
        "N1_PARSE",
        "N2_ORGANIZE",
        "N3_CONFIRM_EVIDENCE",
        "N4_ANALYZE",
        "N5_CONFIRM_PARTIES",
        "N6_CONFIRM_FACTS",
        "N7_CONFIRM_CLAIMS",
        "N8_WRITE",
        "N9_REVIEW",
    ]
    from sqlalchemy import select

    from backend.models import WorkflowNode

    codes = list(
        db_session.scalars(
            select(WorkflowNode.code)
            .where(WorkflowNode.template_id == template.id)
            .order_by(WorkflowNode.order_index)
        )
    )
    assert codes == expected


def test_lifecycle_pending_to_succeeded(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID
) -> None:
    """Drive a short path: complete non-gates until first human gate, then resume+complete to end.

    For speed we use a mini progression: start → complete until WAITING_USER at N3,
    resume → complete gate → ... eventually SUCCEEDED by completing through remaining nodes.
    """
    inst = runtime.create_instance(case_id=case_id)
    assert inst.status == "PENDING"

    started = runtime.start_instance(inst.id)
    assert started.instance.status == "RUNNING"
    assert started.node_run is not None
    assert started.node_run.attempt == 1
    assert started.node_run.input_snapshot_ref

    # N0 complete → auto N1 start → complete → N2 → complete → wait at N3 gate
    r = runtime.complete_node(started.node_run.id)
    while r.instance.status == "RUNNING" and r.node_run is not None:
        r = runtime.complete_node(r.node_run.id)

    assert r.instance.status == "WAITING_USER"
    assert r.instance.waiting_reason == "EVIDENCE"

    # Resume and walk remaining nodes to SUCCEEDED
    r = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
    assert r.instance.status == "RUNNING"
    # After resume at gate: start_node for N3
    assert r.node_run is not None
    safety = 0
    while r.instance.status != "SUCCEEDED" and safety < 30:
        safety += 1
        if r.instance.status == "WAITING_USER":
            r = runtime.resume_instance(r.instance.id, command_id=uuid.uuid4())
            continue
        assert r.node_run is not None
        r = runtime.complete_node(r.node_run.id)

    assert r.instance.status == "SUCCEEDED"


def test_user_pause_and_resume(
    runtime: WorkflowRuntime, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(inst.id)
    assert started.node_run is not None

    paused = runtime.wait_for_user(inst.id, reason="user_pause", context={"note": "hold"})
    assert paused.instance.status == "WAITING_USER"
    assert paused.instance.waiting_reason == "user_pause"

    cmd = uuid.uuid4()
    resumed = runtime.resume_instance(inst.id, command_id=cmd)
    assert resumed.instance.status == "RUNNING"
    assert resumed.idempotent_replay is False
    # Same command replay
    again = runtime.resume_instance(inst.id, command_id=cmd)
    assert again.idempotent_replay is True


def test_retry_reuses_snapshot(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(
        inst.id, input_payload={"case_id": str(case_id), "marker": "same-input"}
    )
    assert started.node_run is not None
    snap1 = started.node_run.input_snapshot_ref
    attempt1 = started.node_run.attempt

    failed = runtime.fail_node(
        started.node_run.id,
        error_code="PARSE_TIMEOUT",
        error_detail="timeout",
        retryable=True,
    )
    assert failed.instance.status == "WAITING_RETRY"
    assert failed.node_run is not None
    assert failed.node_run.status == "FAILED"
    assert failed.node_run.attempt == attempt1

    cmd = uuid.uuid4()
    retried = runtime.retry_node(inst.id, command_id=cmd)
    assert retried.node_run is not None
    assert retried.node_run.attempt == attempt1 + 1
    assert retried.node_run.input_snapshot_ref == snap1
    assert retried.node_run.status == "RUNNING"

    # History preserved
    runs = runtime.list_node_runs(inst.id, node_id=started.node_run.node_id)
    assert len(runs) == 2
    assert runs[0].status == "FAILED"
    assert runs[1].status == "RUNNING"
    assert runs[0].input_snapshot_ref == runs[1].input_snapshot_ref

    # Command idempotency
    again = runtime.retry_node(inst.id, command_id=cmd)
    assert again.idempotent_replay is True
    runs_after = runtime.list_node_runs(inst.id, node_id=started.node_run.node_id)
    assert len(runs_after) == 2


def test_rerun_creates_new_snapshot(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID
) -> None:
    template = ensure_pleading_prep_template(db_session)
    n0 = node_by_code(db_session, template.id, "N0_CREATE")

    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(
        inst.id, input_payload={"case_id": str(case_id), "v": 1}
    )
    assert started.node_run is not None
    old_snap = started.node_run.input_snapshot_ref
    old_run_id = started.node_run.id

    # complete N0 so we have history, then rerun N0 with new payload
    runtime.complete_node(started.node_run.id, auto_advance=False)

    cmd = uuid.uuid4()
    rerun = runtime.rerun_from_node(
        inst.id,
        n0.id,
        command_id=cmd,
        input_payload={"case_id": str(case_id), "v": 2, "fresh": True},
    )
    assert rerun.node_run is not None
    assert rerun.node_run.id != old_run_id
    assert rerun.node_run.input_snapshot_ref != old_snap
    assert rerun.node_run.attempt >= 2

    # Old run retained
    old = db_session.get(NodeRun, old_run_id)
    assert old is not None
    assert old.status == "SUCCEEDED"

    payload_new = runtime.get_snapshot_payload(rerun.node_run.input_snapshot_ref)
    payload_old = runtime.get_snapshot_payload(old_snap)
    assert payload_new.get("v") == 2
    assert payload_old.get("v") == 1


def test_complete_node_idempotent(
    runtime: WorkflowRuntime, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(inst.id)
    assert started.node_run is not None
    first = runtime.complete_node(started.node_run.id, auto_advance=False)
    assert first.node_run is not None
    assert first.node_run.status == "SUCCEEDED"
    second = runtime.complete_node(started.node_run.id, auto_advance=False)
    assert second.idempotent_replay is True
    # Still only one SUCCEEDED run for that attempt
    runs = runtime.list_node_runs(inst.id, node_id=started.node_run.node_id)
    succeeded = [r for r in runs if r.status == "SUCCEEDED"]
    assert len(succeeded) == 1


def test_fatal_failure(
    runtime: WorkflowRuntime, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(inst.id)
    assert started.node_run is not None
    result = runtime.fail_node(
        started.node_run.id,
        error_code="FATAL_SCHEMA",
        error_detail="schema invalid",
        retryable=False,
    )
    assert result.node_run is not None
    assert result.node_run.status == "FAILED"
    assert result.instance.status == "FAILED"


def test_cancel_running_and_idempotent(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(inst.id)
    assert started.node_run is not None
    cmd = uuid.uuid4()
    cancelled = runtime.cancel_instance(inst.id, command_id=cmd)
    assert cancelled.instance.status == "CANCELLED"
    db_session.refresh(started.node_run)
    assert started.node_run.status == "FAILED"
    assert started.node_run.error_code == "CANCELLED"

    again = runtime.cancel_instance(inst.id, command_id=cmd)
    assert again.idempotent_replay is True

    # Second cancel without command still idempotent on instance
    third = runtime.cancel_instance(inst.id)
    assert third.idempotent_replay is True
    assert third.instance.status == "CANCELLED"


def test_service_restart_recovers_waiting_user(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID, engine
) -> None:
    """Persistence proof: new Runtime/session can resume WAITING_USER."""
    inst = runtime.create_instance(case_id=case_id)
    started = runtime.start_instance(inst.id)
    assert started.node_run is not None
    runtime.wait_for_user(inst.id, reason="user_pause")
    instance_id = inst.id
    db_session.flush()

    # Simulate process restart with a fresh session bound to same connection transaction
    SessionLocal = sessionmaker(
        bind=db_session.bind, autoflush=False, autocommit=False, future=True
    )
    new_session = SessionLocal()
    try:
        rt2 = WorkflowRuntime(new_session)
        loaded = rt2.get_instance(instance_id)
        assert loaded.status == "WAITING_USER"
        assert loaded.waiting_reason == "user_pause"
        resumed = rt2.resume_instance(instance_id, command_id=uuid.uuid4())
        assert resumed.instance.status == "RUNNING"
        new_session.flush()
    finally:
        new_session.close()


def test_resume_rejects_wrong_status(
    runtime: WorkflowRuntime, db_session: Session, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    runtime.start_instance(inst.id)
    bad_id = uuid.uuid4()
    with pytest.raises(WorkflowConflictError):
        runtime.resume_instance(inst.id, command_id=bad_id)
    cmd_row = db_session.get(SystemCommand, bad_id)
    assert cmd_row is not None
    assert cmd_row.status == "REJECTED"


def test_retry_rejects_when_not_waiting_retry(
    runtime: WorkflowRuntime, case_id: uuid.UUID
) -> None:
    inst = runtime.create_instance(case_id=case_id)
    runtime.start_instance(inst.id)
    with pytest.raises(WorkflowConflictError):
        runtime.retry_node(inst.id, command_id=uuid.uuid4())
