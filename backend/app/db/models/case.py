from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.common import utc_now
from app.domain.enums import CaseStatus

if TYPE_CHECKING:
    from app.db.models.delivery import DeliveryAttemptRecord
    from app.db.models.event import CaseEventRecord
    from app.db.models.reply import ClientReplyRecord


class CaseRecord(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[CaseStatus] = mapped_column(
        Enum(
            CaseStatus,
            name="case_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
            validate_strings=True,
        ),
        nullable=False,
    )
    original_message: Mapped[str] = mapped_column(Text, nullable=False)
    original_message_reference: Mapped[str | None] = mapped_column(Text)
    end_user_reference: Mapped[str | None] = mapped_column(Text)
    moderator_id: Mapped[str | None] = mapped_column(Text)
    client_contact_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now, onupdate=utc_now, nullable=False
    )
    client_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    moderator_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list["CaseEventRecord"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    client_replies: Mapped[list["ClientReplyRecord"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    delivery_attempts: Mapped[list["DeliveryAttemptRecord"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
