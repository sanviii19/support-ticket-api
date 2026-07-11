from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    InsufficientEffortError,
    OutOfStockError,
    ResolveRequest,
    ResolveResponse,
    OvertimeBreakdownResponse,
)
from app.services import resolve_service

router = APIRouter()


@router.post("/resolve", response_model=ResolveResponse)
def resolve_ticket(data: ResolveRequest, db: Session = Depends(get_db)):
    try:
        result = resolve_service.resolve(db, data.ticket_id, data.effort_logged)
        return ResolveResponse(**result)
    except ValueError as e:
        if str(e) == "ticket_not_found":
            raise HTTPException(status_code=404, detail="Ticket not found")
        if str(e) == "out_of_stock":
            raise HTTPException(
                status_code=400,
                detail=OutOfStockError().model_dump(),
            )
        # Bug 6 fix: check for 'insufficient_effort' in the first argument of the tuple
        if e.args[0] == "insufficient_effort":
            required, logged = e.args[1], e.args[2]
            raise HTTPException(
                status_code=400,
                detail=InsufficientEffortError(
                    required=required, logged=logged
                ).model_dump(),
            )
        raise


@router.get("/resolve/overtime-breakdown", response_model=OvertimeBreakdownResponse)
def overtime_breakdown(overtime: int = Query(..., ge=0)):
    result = resolve_service.overtime_breakdown(overtime)
    return OvertimeBreakdownResponse(**result)
