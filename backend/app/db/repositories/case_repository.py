from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import CaseEventRecord, CaseRecord, ClientReplyRecord
from app.domain.enums import CaseStatus


class CaseRepository:
    """Session-bound persistence operations without transaction commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_case(self, case: CaseRecord) -> None:
        self.session.add(case)
        self.session.flush()

    def get_case(self, public_id: str) -> CaseRecord | None:
        return self.session.scalar(
            select(CaseRecord).where(CaseRecord.public_id == public_id)
        )

    def add_event(self, event: CaseEventRecord) -> None:
        self.session.add(event)

    def get_event_by_deduplication_key(
        self, deduplication_key: str
    ) -> CaseEventRecord | None:
        return self.session.scalar(
            select(CaseEventRecord).where(
                CaseEventRecord.deduplication_key == deduplication_key
            )
        )

    def add_client_reply(self, reply: ClientReplyRecord) -> None:
        self.session.add(reply)

    def get_client_reply(self, external_message_id: str) -> ClientReplyRecord | None:
        return self.session.scalar(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == external_message_id
            )
        )

    def get_case_events(self, case_id: int) -> list[CaseEventRecord]:
        return list(
            self.session.scalars(
                select(CaseEventRecord)
                .where(CaseEventRecord.case_id == case_id)
                .order_by(CaseEventRecord.id)
            )
        )

    def get_waiting_cases_due(self, now: datetime) -> list[CaseRecord]:
        return list(
            self.session.scalars(
                select(CaseRecord)
                .where(
                    or_(
                        and_(
                            CaseRecord.status == CaseStatus.WAITING_CLIENT,
                            CaseRecord.client_deadline.is_not(None),
                            CaseRecord.client_deadline <= now,
                        ),
                        and_(
                            CaseRecord.status == CaseStatus.WAITING_MODERATOR,
                            CaseRecord.moderator_deadline.is_not(None),
                            CaseRecord.moderator_deadline <= now,
                        ),
                    )
                )
                .order_by(CaseRecord.public_id)
            )
        )
