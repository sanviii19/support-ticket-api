from pydantic import BaseModel, Field


# --- Queue ---
class QueueCreate(BaseModel):
    name: str
    capacity: int = Field(..., gt=0)


class QueueResponse(BaseModel):
    id: str
    name: str
    capacity: int
    current_ticket_count: int

    model_config = {"from_attributes": True}


# --- Ticket ---
class TicketCreate(BaseModel):
    title: str
    complexity: int = Field(..., ge=0)  # Allow any non-negative complexity
    quantity: int = Field(..., gt=0)


class TicketCreateStandalone(BaseModel):
    title: str
    complexity: int = Field(..., ge=0)
    quantity: int = Field(..., gt=0)
    queue_id: str | None = None


class TicketBulkEntry(BaseModel):
    title: str
    complexity: int = Field(..., ge=0)
    quantity: int = Field(..., gt=0)


class TicketBulkRequest(BaseModel):
    tickets: list[TicketBulkEntry]


class TicketResponse(BaseModel):
    id: str
    title: str
    complexity: int
    quantity: int

    model_config = {"from_attributes": True}


class TicketDetailResponse(TicketResponse):
    queue_id: str | None  # Bug 8 fix: standalone tickets have queue_id = None


class TicketComplexityUpdate(BaseModel):
    complexity: int = Field(..., ge=0)  # Bug 11 fix: Complexity can be 0


# --- Queue full view ---
class QueueFullViewTicket(BaseModel):
    id: str
    title: str
    complexity: int
    quantity: int

    model_config = {"from_attributes": True}


class QueueFullView(BaseModel):
    id: str
    name: str
    capacity: int
    tickets: list[QueueFullViewTicket]

    model_config = {"from_attributes": True}


# --- Resolve ---
class ResolveRequest(BaseModel):
    ticket_id: str
    effort_logged: int = Field(..., ge=0)


class ResolveResponse(BaseModel):
    ticket: str
    complexity: int
    effort_logged: int
    overtime_returned: int
    remaining_quantity: int
    message: str


class InsufficientEffortError(BaseModel):
    error: str = "Insufficient effort logged"
    required: int
    logged: int


class OutOfStockError(BaseModel):
    error: str = "Ticket batch already resolved"


# --- Generic message responses ---
class MessageResponse(BaseModel):
    message: str


class BulkAddResponse(BaseModel):
    message: str = "Tickets added successfully"
    added_count: int


class BulkRemoveBody(BaseModel):
    ticket_ids: list[str] | None = None


# --- Overtime breakdown (bonus) ---
class OvertimeBreakdownResponse(BaseModel):
    overtime: int
    blocks: dict[str, int]
