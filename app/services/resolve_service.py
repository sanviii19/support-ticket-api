from sqlalchemy.orm import Session

from app.config import settings
from app.models import Ticket


def resolve(db: Session, ticket_id: str, effort_logged: int) -> dict:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise ValueError("ticket_not_found")
    if ticket.quantity <= 0:
        raise ValueError("out_of_stock")
    if effort_logged < ticket.complexity:
        raise ValueError("insufficient_effort", ticket.complexity, effort_logged)
    # No validation that effort_logged or overtime use STANDARD_EFFORT_BLOCKS
    overtime = effort_logged - ticket.complexity
    ticket.quantity -= 1
    if ticket.queue:      # Bug 5 fix: added check for ticket.queue to avoid AttributeError
        ticket.queue.current_ticket_count -= 1
    db.commit()
    db.refresh(ticket)
    return {
        "ticket": ticket.title,
        "complexity": ticket.complexity,
        "effort_logged": effort_logged,
        "overtime_returned": overtime,
        "remaining_quantity": ticket.quantity,
        "message": "Ticket resolved successfully",
    }


def overtime_breakdown(overtime: int) -> dict:
    blocks = sorted(settings.STANDARD_EFFORT_BLOCKS, reverse=True)
    result: dict[str, int] = {}
    remaining = overtime
    for b in blocks:
        if remaining <= 0:
            break
        count = remaining // b
        if count > 0:
            result[str(b)] = count
            remaining -= count * b
    return {"overtime": overtime, "blocks": result}
