from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.sla import SlaActionType
from app.services import CaseService, SlaService

START = datetime(2026, 8, 23, 10, 0)


def settings(**overrides: int) -> Settings:
    values = {
        "client_reminder_1_minutes": 120,
        "client_reminder_2_minutes": 360,
        "client_escalation_minutes": 1440,
        "moderator_reminder_1_minutes": 30,
        "moderator_reminder_2_minutes": 120,
        "moderator_escalation_minutes": 240,
    }
    values.update(overrides)
    return Settings(
        app_name="test",
        environment="test",
        database_url="sqlite://",
        **values,
    )


def waiting_client(db_session: Session) -> tuple[CaseService, SlaService, str]:
    configured = settings()
    cases = CaseService(db_session, settings=configured)
    case = cases.create_case("Question")
    cases.mark_client_request_sent(case.public_id, now=START)
    return cases, SlaService(db_session, settings=configured), case.public_id


def waiting_moderator(db_session: Session) -> tuple[CaseService, SlaService, str]:
    cases, sla, public_id = waiting_client(db_session)
    cases.record_client_reply(
        public_id, "fixed-reply", "Answer", now=START + timedelta(hours=1)
    )
    return cases, sla, public_id


@pytest.mark.parametrize(
    ("elapsed", "action_type", "level"),
    [
        (timedelta(hours=2), SlaActionType.REMIND_CLIENT, 1),
        (timedelta(hours=6), SlaActionType.REMIND_CLIENT, 2),
        (timedelta(hours=24), SlaActionType.ESCALATE_CLIENT_WAIT, None),
        (timedelta(hours=25), SlaActionType.ESCALATE_CLIENT_WAIT, None),
    ],
)
def test_client_sla_returns_only_highest_due_action(
    db_session: Session,
    elapsed: timedelta,
    action_type: SlaActionType,
    level: int | None,
) -> None:
    _cases, sla, public_id = waiting_client(db_session)

    actions = sla.get_due_actions(now=START + elapsed)

    assert len(actions) == 1
    assert actions[0].case_public_id == public_id
    assert actions[0].action_type is action_type
    assert actions[0].level == level


def test_client_sla_boundary_and_acknowledged_reminder(
    db_session: Session,
) -> None:
    _cases, sla, public_id = waiting_client(db_session)

    assert sla.get_due_actions(START + timedelta(hours=2) - timedelta(seconds=1)) == []
    result = sla.acknowledge_action(
        public_id,
        SlaActionType.REMIND_CLIENT,
        level=1,
        now=START + timedelta(hours=2),
    )

    assert result.already_processed is False
    assert sla.get_due_actions(START + timedelta(hours=2)) == []
    assert sla.get_due_actions(START + timedelta(hours=6))[0].level == 2


@pytest.mark.parametrize(
    ("elapsed", "action_type", "level"),
    [
        (timedelta(minutes=30), SlaActionType.REMIND_MODERATOR, 1),
        (timedelta(hours=2), SlaActionType.REMIND_MODERATOR, 2),
        (timedelta(hours=4), SlaActionType.ESCALATE_MODERATOR_WAIT, None),
    ],
)
def test_moderator_sla_thresholds(
    db_session: Session,
    elapsed: timedelta,
    action_type: SlaActionType,
    level: int | None,
) -> None:
    _cases, sla, _public_id = waiting_moderator(db_session)
    moderator_started = START + timedelta(hours=1)

    actions = sla.get_due_actions(moderator_started + elapsed)

    assert len(actions) == 1
    assert actions[0].action_type is action_type
    assert actions[0].level == level


def test_moderator_sla_has_no_action_before_first_boundary(
    db_session: Session,
) -> None:
    _cases, sla, _public_id = waiting_moderator(db_session)
    assert sla.get_due_actions(START + timedelta(hours=1, minutes=29)) == []


def test_acknowledgements_are_idempotent_and_preserve_waiting_state(
    db_session: Session,
) -> None:
    cases, sla, public_id = waiting_client(db_session)
    due = START + timedelta(hours=24)

    first = sla.acknowledge_action(
        public_id, SlaActionType.ESCALATE_CLIENT_WAIT, now=due
    )
    second = sla.acknowledge_action(
        public_id, SlaActionType.ESCALATE_CLIENT_WAIT, now=due
    )
    events = cases.get_case_events(public_id)
    escalation_events = [
        event for event in events if event.event_type is CaseEventType.CASE_ESCALATED
    ]

    assert first.already_processed is False
    assert second.already_processed is True
    assert len(escalation_events) == 1
    assert escalation_events[0].metadata_json == {"wait_type": "client"}
    assert cases.get_case(public_id).status is CaseStatus.WAITING_CLIENT


def test_reminder_acknowledgement_writes_exact_level_once(
    db_session: Session,
) -> None:
    cases, sla, public_id = waiting_client(db_session)
    due = START + timedelta(hours=2)

    first = sla.acknowledge_action(
        public_id, SlaActionType.REMIND_CLIENT, level=1, now=due
    )
    second = sla.acknowledge_action(
        public_id, SlaActionType.REMIND_CLIENT, level=1, now=due
    )
    reminders = [
        event
        for event in cases.get_case_events(public_id)
        if event.event_type is CaseEventType.CLIENT_REMINDER_SENT
    ]

    assert first.already_processed is False
    assert second.already_processed is True
    assert [event.metadata_json for event in reminders] == [{"level": 1}]


def test_moderator_escalation_acknowledgement_records_wait_type(
    db_session: Session,
) -> None:
    cases, sla, public_id = waiting_moderator(db_session)
    moderator_started = START + timedelta(hours=1)

    sla.acknowledge_action(
        public_id,
        SlaActionType.ESCALATE_MODERATOR_WAIT,
        now=moderator_started + timedelta(hours=4),
    )

    event = cases.get_case_events(public_id)[-1]
    assert event.event_type is CaseEventType.CASE_ESCALATED
    assert event.metadata_json == {"wait_type": "moderator"}
    assert cases.get_case(public_id).status is CaseStatus.WAITING_MODERATOR


def test_state_changes_switch_or_stop_sla_responsibility(
    db_session: Session,
) -> None:
    configured = settings()
    cases = CaseService(db_session, settings=configured)
    sla = SlaService(db_session, settings=configured)
    new_case = cases.create_case("New")
    assert sla.get_due_actions(START + timedelta(days=10)) == []

    waiting = cases.create_case("Switch")
    cases.mark_client_request_sent(waiting.public_id, now=START)
    assert cases.get_case(waiting.public_id).client_deadline == START + timedelta(
        hours=2
    )
    cases.record_client_reply(
        waiting.public_id, "switch-reply", "Reply", now=START + timedelta(hours=23)
    )
    switched = cases.get_case(waiting.public_id)
    assert switched.client_deadline is None
    assert switched.moderator_deadline == START + timedelta(hours=23, minutes=30)
    actions = sla.get_due_actions(START + timedelta(hours=23, minutes=30))
    assert len(actions) == 1
    assert actions[0].action_type is SlaActionType.REMIND_MODERATOR

    cases.confirm_user_answered(waiting.public_id, now=START + timedelta(days=2))
    closed = cases.get_case(waiting.public_id)
    assert closed.client_deadline is None
    assert closed.moderator_deadline is None
    cancelled = cases.create_case("Cancel")
    cases.mark_client_request_sent(cancelled.public_id, now=START)
    cases.cancel_case(cancelled.public_id, "No longer needed", now=START)
    assert cases.get_case(cancelled.public_id).client_deadline is None
    assert sla.get_due_actions(START + timedelta(days=10)) == []
    assert cases.get_case(new_case.public_id).status is CaseStatus.NEW


def test_due_query_is_repeatable_and_side_effect_free(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, sla, public_id = waiting_client(db_session)
    initial_events = len(cases.get_case_events(public_id))
    commit_count = 0

    def counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1

    monkeypatch.setattr(db_session, "commit", counted_commit)
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

    assert sla.get_due_actions(now) == sla.get_due_actions(now)
    assert commit_count == 0
    assert len(cases.get_case_events(public_id)) == initial_events


def test_acknowledgement_failure_rolls_back(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, sla, public_id = waiting_client(db_session)
    rollback_count = 0
    original_rollback = db_session.rollback

    def fail_event_write(_event: object) -> None:
        raise RuntimeError("simulated SLA event persistence failure")

    def counted_rollback() -> None:
        nonlocal rollback_count
        rollback_count += 1
        original_rollback()

    monkeypatch.setattr(sla.repository, "add_event", fail_event_write)
    monkeypatch.setattr(db_session, "rollback", counted_rollback)

    with pytest.raises(RuntimeError, match="SLA event persistence"):
        sla.acknowledge_action(
            public_id,
            SlaActionType.REMIND_CLIENT,
            level=1,
            now=START + timedelta(hours=2),
        )

    assert rollback_count == 1
    assert CaseEventType.CLIENT_REMINDER_SENT not in {
        event.event_type for event in cases.get_case_events(public_id)
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_reminder_2_minutes": 120},
        {"client_reminder_1_minutes": 0},
        {"moderator_escalation_minutes": 120},
    ],
)
def test_invalid_threshold_order_is_rejected(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        settings(**overrides)
