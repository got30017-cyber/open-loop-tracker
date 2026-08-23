from dataclasses import dataclass

from app.domain.enums import CaseStatus


@dataclass(slots=True)
class Case:
    """Minimal domain representation of an operational case."""

    original_message: str
    moderator_id: str | None = None
    client_contact_id: str | None = None
    public_id: str | None = None
    status: CaseStatus = CaseStatus.NEW

    def transition_to(self, requested_status: CaseStatus) -> None:
        """Move the case through the domain state machine."""
        from app.domain.state_machine import transition_case

        transition_case(self, requested_status)
