from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.common import utc_now

if TYPE_CHECKING:
    from app.db.models.case import CaseRecord


class ClientReplyRecord(Base):
    __tablename__ = "client_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    external_message_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    sender_id: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now, nullable=False
    )

    case: Mapped["CaseRecord"] = relationship(back_populates="client_replies")
