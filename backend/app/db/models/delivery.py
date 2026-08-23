from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.common import utc_now

if TYPE_CHECKING:
    from app.db.models.case import CaseRecord


class DeliveryAttemptRecord(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            "attempt_number",
            name="uq_delivery_attempt_logical_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    delivery_type: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))

    case: Mapped["CaseRecord"] = relationship(back_populates="delivery_attempts")
