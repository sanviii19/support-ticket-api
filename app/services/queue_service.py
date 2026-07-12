from sqlalchemy.orm import Session

from app.config import settings
from app.models import Queue
from app.schemas import QueueCreate, QueueFullView, QueueFullViewTicket, QueueResponse


def create_queue(db: Session, data: QueueCreate) -> Queue:
    count = db.query(Queue).count()
    if count >= settings.MAX_QUEUES:
        raise ValueError("queue_limit_reached")
    existing = db.query(Queue).filter(Queue.name == data.name).first()
    if existing:
        raise ValueError("queue_name_exists")
    queue = Queue(name=data.name, capacity=data.capacity, current_ticket_count=0)
    db.add(queue)
    db.commit()
    db.refresh(queue)
    return queue


def list_queues(db: Session) -> list[Queue]:
    return db.query(Queue).all()


def get_queue_by_id(db: Session, queue_id: str) -> Queue | None:
    return db.query(Queue).filter(Queue.id == queue_id).first()


def delete_queue(db: Session, queue_id: str) -> None:
    queue = get_queue_by_id(db, queue_id)
    if not queue:
        raise ValueError("queue_not_found")
    # Bug 9 fix: Cannot delete a queue that still contains tickets
    if queue.current_ticket_count > 0:
        raise ValueError("queue_has_tickets")
    db.delete(queue)
    db.commit()


def get_full_view(db: Session) -> list[QueueFullView]:
    queues = db.query(Queue).all()
    result = []
    for queue in queues:
        # queue.tickets loaded per queue (N+1)
        tickets = [
            QueueFullViewTicket(
                id=ticket.id,
                title=ticket.title,
                complexity=ticket.complexity,
                quantity=ticket.quantity,
            )
            for ticket in queue.tickets
        ]
        result.append(
            QueueFullView(
                id=queue.id,
                name=queue.name,
                capacity=queue.capacity,
                tickets=tickets,
            )
        )
    return result
