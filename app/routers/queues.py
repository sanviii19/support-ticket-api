from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    BulkAddResponse,
    TicketBulkRequest,
    TicketCreate,
    TicketResponse,
    MessageResponse,
    QueueCreate,
    QueueFullView,
    QueueResponse,
)
from app.services import ticket_service, queue_service

router = APIRouter()


def _queue_404():
    raise HTTPException(status_code=404, detail="Queue not found")


@router.post("/queues", response_model=QueueResponse, status_code=201)
def create_queue(data: QueueCreate, db: Session = Depends(get_db)):
    try:
        queue = queue_service.create_queue(db, data)
        return QueueResponse(
            id=queue.id,
            name=queue.name,
            capacity=queue.capacity,
            current_ticket_count=queue.current_ticket_count,
        )
    except ValueError as e:
        if str(e) == "queue_limit_reached":
            raise HTTPException(status_code=400, detail="Queue limit reached")
        if str(e) == "queue_name_exists":
            raise HTTPException(status_code=409, detail="Queue name already exists")
        raise


@router.get("/queues", response_model=list[QueueResponse])
def list_queues(db: Session = Depends(get_db)):
    queues = queue_service.list_queues(db)
    return [
        QueueResponse(
            id=q.id,
            name=q.name,
            capacity=q.capacity,
            current_ticket_count=q.current_ticket_count,
        )
        for q in queues
    ]


@router.get("/queues/full-view", response_model=list[QueueFullView])
def full_view(db: Session = Depends(get_db)):
    return queue_service.get_full_view(db)


@router.delete("/queues/{queue_id}", response_model=MessageResponse)
def delete_queue(queue_id: str, db: Session = Depends(get_db)):
    try:
        queue_service.delete_queue(db, queue_id)
        return MessageResponse(message="Queue removed successfully")
    except ValueError as e:
        if str(e) == "queue_not_found":
            _queue_404()
        if str(e) == "queue_has_tickets":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete a queue that still contains tickets",
            )
        raise


@router.post("/queues/{queue_id}/tickets", response_model=TicketResponse, status_code=201)
def add_ticket_to_queue(queue_id: str, data: TicketCreate, db: Session = Depends(get_db)):
    try:
        ticket = ticket_service.add_ticket_to_queue(db, queue_id, data)
        return TicketResponse(
            id=ticket.id,
            title=ticket.title,
            complexity=ticket.complexity,
            quantity=ticket.quantity,
        )
    except ValueError as e:
        if str(e) == "queue_not_found":
            _queue_404()
        if str(e) == "capacity_exceeded":
            raise HTTPException(
                status_code=400,
                detail="Total tickets would exceed queue capacity",
            )
        raise


@router.post("/queues/{queue_id}/tickets/bulk", response_model=BulkAddResponse)
def bulk_add_tickets(queue_id: str, body: TicketBulkRequest, db: Session = Depends(get_db)):
    try:
        added = ticket_service.bulk_add_tickets(db, queue_id, body.tickets)
        return BulkAddResponse(added_count=added)
    except ValueError as e:
        if str(e) == "queue_not_found":
            _queue_404()
        if str(e) == "capacity_exceeded":
            raise HTTPException(
                status_code=400,
                detail="Total tickets would exceed queue capacity",
            )
        raise


@router.get("/queues/{queue_id}/tickets", response_model=list[TicketResponse])
def list_queue_tickets(queue_id: str, db: Session = Depends(get_db)):
    try:
        tickets = ticket_service.list_tickets_by_queue(db, queue_id)
        return [
            TicketResponse(id=t.id, title=t.title, complexity=t.complexity, quantity=t.quantity)
            for t in tickets
        ]
    except ValueError as e:
        if str(e) == "queue_not_found":
            _queue_404()
        raise
