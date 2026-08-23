from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.common import utc_now
from app.domain.enums import CaseEventType

if TYPE_CHECKING:
    from app.db.models.case import CaseRecord


class CaseEventRecord(Base):
    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[CaseEventType] = mapped_column(
        Enum(
            CaseEventType,
            name="case_event_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
            validate_strings=True,
        ),
        nullable=False,
    )
    actor_type: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now, nullable=False
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    deduplication_key: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )

    case: Mapped["CaseRecord"] = relationship(back_populates="events")
