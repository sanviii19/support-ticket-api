# Support Ticket API — Bug Fixes by API Endpoint

> Bugs are grouped by the **API endpoint and handler** they affect.  
> Each section shows the endpoint, the files involved (router → service → schema/config), all bugs found in that flow, and their fixes.

---

## API Surface Overview

| Method | Endpoint | Handler | Router File |
|---|---|---|---|
| `POST` | `/tickets` | `create_ticket` | `routers/tickets.py` |
| `GET` | `/tickets/{ticket_id}` | `get_ticket` | `routers/tickets.py` |
| `PATCH` | `/tickets/{ticket_id}/complexity` | `update_ticket_complexity` | `routers/tickets.py` |
| `POST` | `/queues` | `create_queue` | `routers/queues.py` |
| `GET` | `/queues` | `list_queues` | `routers/queues.py` |
| `GET` | `/queues/full-view` | `full_view` | `routers/queues.py` |
| `DELETE` | `/queues/{queue_id}` | `delete_queue` | `routers/queues.py` |
| `POST` | `/queues/{queue_id}/tickets` | `add_ticket_to_queue` | `routers/queues.py` |
| `POST` | `/queues/{queue_id}/tickets/bulk` | `bulk_add_tickets` | `routers/queues.py` |
| `GET` | `/queues/{queue_id}/tickets` | `list_queue_tickets` | `routers/queues.py` |
| `DELETE` | `/queues/{queue_id}/tickets/{ticket_id}` | `remove_ticket_from_queue` | `routers/tickets.py` |
| `DELETE` | `/queues/{queue_id}/tickets` | `bulk_remove_tickets` | `routers/tickets.py` |
| `POST` | `/resolve` | `resolve_ticket` | `routers/resolve.py` |
| `GET` | `/resolve/overtime-breakdown` | `overtime_breakdown` | `routers/resolve.py` |

---

## 🔧 Cross-Cutting Fix — Dependency / Environment

> This bug affects **every endpoint** because the entire app fails to start correctly without it.

---

### Bug 1 — Wrong Pydantic Version Pin

**File:** `requirements.txt` → affects all schemas used by every endpoint  
**Comment in code:** `# Bug 1 fix: used pydantic v2`

#### Buggy Code
```
pydantic==1.10.13     ← pinned to Pydantic v1
```

#### What Went Wrong
The entire codebase uses **Pydantic v2** APIs (e.g., `model_config = {"from_attributes": True}`), but `pydantic` was explicitly pinned to `1.10.13` (the last v1 release). `pydantic-settings>=2.0.0` also requires Pydantic v2, so a direct version conflict exists.

#### Symptom
`pip install -r requirements.txt` fails with a dependency conflict, or installs mismatched packages that cause `ImportError` / `ValidationError` on every schema-using endpoint at runtime.

#### Fix
```diff
- pydantic==1.10.13
+ pydantic>=2.0.0
```

---

## 📌 `POST /queues/{queue_id}/tickets` — Add Ticket to Queue

**Handler:** `add_ticket_to_queue` in `routers/queues.py`  
**Service:** `ticket_service.add_ticket_to_queue()` in `services/ticket_service.py`

---

### Bug 2 — Wrong Operator for `MAX_TICKETS_PER_QUEUE` Check

**File:** `app/services/ticket_service.py`  
**Comment in code:** `# Bug 2 - fix: check total tickets does not exceed MAX_TICKETS_PER_QUEUE config value`

#### Buggy Code
```python
def add_ticket_to_queue(db, queue_id, data):
    ...
    if queue.current_ticket_count + data.quantity > queue.capacity:
        raise ValueError("capacity_exceeded")
    if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:  # ← BUG
        raise ValueError("capacity_exceeded")
```

#### What Went Wrong
The second check uses `<` (less-than) — the **complete inverse** of the intended logic:
- **Intended:** Reject when total **exceeds** `MAX_TICKETS_PER_QUEUE` (the system-wide hard cap)
- **Actual:** Reject when total is **below** `MAX_TICKETS_PER_QUEUE` — i.e., every valid request

#### Symptom
Every valid `POST /queues/{queue_id}/tickets` call where the queue is not yet full returns `400 capacity_exceeded`. Requests that genuinely overflow the limit pass through silently.

#### Fix
```diff
- if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:
+ if queue.current_ticket_count + data.quantity > settings.MAX_TICKETS_PER_QUEUE:
      raise ValueError("capacity_exceeded")
```

---

## 📌 `POST /queues/{queue_id}/tickets/bulk` — Bulk Add Tickets

**Handler:** `bulk_add_tickets` in `routers/queues.py`  
**Service:** `ticket_service.bulk_add_tickets()` in `services/ticket_service.py`

---

### Bug 3 — No Upfront Capacity Check Before Bulk Insert

**File:** `app/services/ticket_service.py`  
**Comment in code:** `# Bug 3 fix: check total capacity before adding anything`

#### Buggy Code
```python
def bulk_add_tickets(db, queue_id, entries):
    queue = db.query(Queue).filter(Queue.id == queue_id).first()
    if not queue:
        raise ValueError("queue_not_found")
    # ← no capacity check at all
    added = 0
    for e in entries:
        ticket = Ticket(...)
        db.add(ticket)
        added += 1
        db.commit()   # commits inside loop — partial data is permanently saved
```

#### What Went Wrong
`add_ticket_to_queue` (single-ticket endpoint) validates capacity before inserting. `bulk_add_tickets` has **no capacity check** before the loop. A batch that overflows the queue commits entries one-by-one until they're all in — no error is raised.

#### Symptom
`POST /queues/{queue_id}/tickets/bulk` silently overflows the queue. `current_ticket_count` exceeds `capacity` with a `200 OK` response.

#### Fix
```python
# Bug 3 fix: check total capacity before adding anything
total_quantity = sum(e.quantity for e in entries if e.quantity > 0)
if queue.current_ticket_count + total_quantity > queue.capacity:
    raise ValueError("capacity_exceeded")
```

---

### Bug 4 — `current_ticket_count` Never Updated During Bulk Add

**File:** `app/services/ticket_service.py`  
**Comment in code:** `# Bug 4 fix: update count per ticket`

#### Buggy Code
```python
for e in entries:
    ticket = Ticket(title=e.title, complexity=e.complexity, queue_id=queue_id, quantity=e.quantity)
    db.add(ticket)
    added += 1
    db.commit()
    # ← queue.current_ticket_count never updated
```

#### What Went Wrong
`add_ticket_to_queue` correctly does `queue.current_ticket_count += data.quantity` after every add. `bulk_add_tickets` **never updates the counter**. Tickets are physically inserted into the DB, but the queue's live counter stays stale.

This also breaks Bug 3's pre-check: if the counter is stale (always 0), the capacity check always passes even on a full queue.

#### Symptom
After `POST /queues/{queue_id}/tickets/bulk`, `GET /queues` shows `current_ticket_count` unchanged despite new tickets being present. Bug 9's deletion guard also fails as a consequence.

#### Fix
```python
queue.current_ticket_count += e.quantity  # Bug 4 fix: update count per ticket
```

---

### Bug 10 — `db.commit()` Inside the Loop (Non-Atomic Batch)

**File:** `app/services/ticket_service.py`  
**Comment in code:** `# Bug 10 fix: commit once after all entries so count is saved atomically`

#### Buggy Code
```python
added = 0
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    added += 1
    db.commit()          # ← individual commit per entry
    time.sleep(0.05)     # demo: widens race window vs resolve
return added
```

#### What Went Wrong
Three problems from one loop:

1. **Non-atomic:** Each entry is its own transaction. If entry 3 of 5 fails, entries 1 and 2 are already committed with no rollback possible.

2. **Race window:** Between each `db.commit()`, another thread (e.g., `POST /resolve`) can read a partially-committed `current_ticket_count` and make decisions on stale data. `time.sleep(0.05)` was **deliberately placed here** to widen this window and make the race condition reliably demonstrable.

3. **Wrong response count:** `added += 1` counts *entries*, not *tickets*. An entry `{"quantity": 5}` increments `added` by 1, so `added_count` in the response is wrong.

#### Symptom
`POST /queues/{queue_id}/tickets/bulk` returns incorrect `added_count`. Under concurrent load, `current_ticket_count` becomes inconsistent. Partial failures leave the database in a half-committed state.

#### Fix
```python
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    queue.current_ticket_count += e.quantity
    added += e.quantity       # count actual tickets, not entries
db.commit()                   # Bug 10 fix: single commit — entire batch is one transaction
db.refresh(queue)
```

---

## 📌 `GET /tickets/{ticket_id}` — Get Ticket by ID

**Handler:** `get_ticket` in `routers/tickets.py`  
**Service:** `ticket_service.get_ticket_by_id()` in `services/ticket_service.py`  
**Schema:** `TicketDetailResponse` in `schemas.py`

---

### Bug 8 — `TicketDetailResponse.queue_id` Cannot Be `None`

**File:** `app/schemas.py`  
**Comment in code:** `# Bug 8 fix: standalone tickets have queue_id = None`

#### Buggy Code
```python
class TicketDetailResponse(TicketResponse):
    queue_id: str    # ← required non-null — cannot serialize None
```

#### What Went Wrong
`POST /tickets` supports creating **standalone tickets** (no queue, `queue_id = None`). The DB model declares `queue_id` as `nullable=True`. But the response schema has `queue_id: str`, which Pydantic cannot serialize from a `None` value — it raises a validation error.

#### Symptom
`GET /tickets/{ticket_id}` returns `500 Internal Server Error` for any ticket created without a queue.

#### Fix
```diff
- queue_id: str
+ queue_id: str | None  # Bug 8 fix: standalone tickets have queue_id = None
```

---

## 📌 `PATCH /tickets/{ticket_id}/complexity` — Update Ticket Complexity

**Handler:** `update_ticket_complexity` in `routers/tickets.py`  
**Schema:** `TicketComplexityUpdate` in `schemas.py`

---

### Bug 11 — Complexity `0` Rejected in Update Schema

**File:** `app/schemas.py`  
**Comment in code:** `# Bug 11 fix: Complexity can be 0`

#### Buggy Code
```python
class TicketComplexityUpdate(BaseModel):
    complexity: int = Field(..., gt=0)   # strictly greater than 0
```

#### What Went Wrong
All create schemas (`TicketCreate`, `TicketBulkEntry`, `TicketCreateStandalone`) use `ge=0` — allowing `complexity = 0`. But the update schema uses `gt=0`, making `complexity = 0` invalid on update only. This inconsistency means you can create a ticket with complexity 0, but you can never update any ticket back to 0.

#### Symptom
`PATCH /tickets/{ticket_id}/complexity` with body `{"complexity": 0}` returns `422 Unprocessable Entity`, even though `0` is accepted at creation time.

#### Fix
```diff
- complexity: int = Field(..., gt=0)
+ complexity: int = Field(..., ge=0)  # Bug 11 fix: Complexity can be 0
```

---

## 📌 `DELETE /queues/{queue_id}` — Delete a Queue

**Handler:** `delete_queue` in `routers/queues.py`  
**Service:** `queue_service.delete_queue()` in `services/queue_service.py`

---

### Bug 9 — Non-Empty Queues Can Be Deleted

**File:** `app/services/queue_service.py`  
**Comment in code:** `# Bug 9 fix: Cannot delete a queue that still contains tickets`

#### Buggy Code
```python
def delete_queue(db, queue_id):
    queue = get_queue_by_id(db, queue_id)
    if not queue:
        raise ValueError("queue_not_found")
    db.delete(queue)    # ← no check — deletes even if tickets exist
    db.commit()
```

#### What Went Wrong
No guard prevents deleting a queue that still has tickets. The DB model uses:
```python
queue_id = Column(CHAR(36), ForeignKey("queues.id", ondelete="SET NULL"), nullable=True)
```
`ondelete="SET NULL"` means on queue deletion, all child tickets have their `queue_id` silently set to `NULL` — they become **orphaned standalone tickets** with no warning to the caller.

#### Symptom
`DELETE /queues/{queue_id}` returns `200 OK` even if the queue has tickets. Those tickets remain in the DB as orphans with `queue_id = NULL`.

#### Fix

**In `queue_service.py`:**
```python
# Bug 9 fix: Cannot delete a queue that still contains tickets
if queue.current_ticket_count > 0:
    raise ValueError("queue_has_tickets")
```

**In `routers/queues.py`:**
```python
if str(e) == "queue_has_tickets":
    raise HTTPException(
        status_code=400,
        detail="Cannot delete a queue that still contains tickets",
    )
```

---

## 📌 `POST /resolve` — Resolve a Ticket

**Handler:** `resolve_ticket` in `routers/resolve.py`  
**Service:** `resolve_service.resolve()` in `services/resolve_service.py`

---

### Bug 5 — `AttributeError` When Resolving a Standalone Ticket

**File:** `app/services/resolve_service.py`  
**Comment in code:** `# Bug 5 fix: added check for ticket.queue to avoid AttributeError`

#### Buggy Code
```python
def resolve(db, ticket_id, effort_logged):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    ...
    ticket.quantity -= 1
    ticket.queue.current_ticket_count -= 1   # ← no null guard
    db.commit()
```

#### What Went Wrong
For standalone tickets (`queue_id = None`), the SQLAlchemy relationship `ticket.queue` evaluates to `None`. Accessing `.current_ticket_count` on `None` raises:
```
AttributeError: 'NoneType' object has no attribute 'current_ticket_count'
```

#### Symptom
`POST /resolve` returns `500 Internal Server Error` for any standalone ticket.

#### Fix
```python
ticket.quantity -= 1
if ticket.queue:      # Bug 5 fix: added check for ticket.queue to avoid AttributeError
    ticket.queue.current_ticket_count -= 1
db.commit()
```

---

### Bug 6 — `insufficient_effort` ValueError Never Caught

**File:** `app/routers/resolve.py`  
**Comment in code:** `# Bug 6 fix: check for 'insufficient_effort' in the first argument of the tuple`

#### How the error is raised in `resolve_service.py`
```python
raise ValueError("insufficient_effort", ticket.complexity, effort_logged)
```
Python stores all args in `e.args` as a tuple: `("insufficient_effort", 5, 3)`

#### Buggy Code in Router
```python
if str(e) == "insufficient_effort":    # ← wrong
    required, logged = e.args[1], e.args[2]
    ...
```

#### What Went Wrong
`str(e)` on a **multi-argument** ValueError returns the full tuple representation:
```
"('insufficient_effort', 5, 3)"
```
This never matches the string `"insufficient_effort"`, so the `if` block is always skipped. The `raise` at the bottom re-raises the raw `ValueError`.

> `str(e) == "ticket_not_found"` works on the line above because that is a **single-argument** ValueError — `str()` of a single-arg ValueError returns just the string.

#### Symptom
`POST /resolve` with insufficient effort returns `500 Internal Server Error` instead of `400` with the required/logged breakdown.

#### Fix
```diff
- if str(e) == "insufficient_effort":
+ if e.args[0] == "insufficient_effort":   # Bug 6 fix: check first arg of the tuple
```

---

### Race Condition — Concurrent Resolves Drive Quantity Below Zero

**File:** `app/db.py`  
**Commit:** `feat: implement atomicity with BEGIN IMMEDIATE transactions`

#### What Went Wrong
SQLite's default **deferred transactions** (`BEGIN DEFERRED`) only acquire a write lock at the point of the first write — not when the transaction starts. Between READ and WRITE, another thread can read the same stale data and commit a conflicting write.

```
Thread A:  READ qty=3 → qty-=1 → COMMIT (qty=2)
Thread B:  READ qty=3 → qty-=1 → COMMIT (qty=2)  ← should be 1!
```

With 20 concurrent `POST /resolve` requests on a ticket with `quantity=3`, more than 3 succeed — the quantity goes **negative**.

The `time.sleep(0.05)` calls seeded into `resolve_service.py` and `ticket_service.py` were **deliberately injected** to widen the race window and make the bug reproducible on demand.

#### Fix — `app/db.py`
```python
from sqlalchemy import create_engine, event

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "begin")
    def set_sqlite_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
```

`BEGIN IMMEDIATE` acquires a **write lock at transaction start**, serializing all writers. The `time.sleep` calls are removed in the same commit.

#### Verified by `test_atomicity.py`

| | Expected | With Bug | After Fix |
|---|---|---|---|
| Successful resolves | 3 | > 3 | Exactly 3 ✓ |
| `out_of_stock` 400s | 17 | < 17 | Exactly 17 ✓ |
| Final `ticket.quantity` | 0 | Negative | 0 ✓ |

---

## 📌 `GET /resolve/overtime-breakdown` — Overtime Breakdown

**Handler:** `overtime_breakdown` in `routers/resolve.py`  
**Service:** `resolve_service.overtime_breakdown()` in `services/resolve_service.py`  
**Config:** `settings.STANDARD_EFFORT_BLOCKS` in `config.py`

---

### Bug 7 — Missing Values `1` and `2` in `STANDARD_EFFORT_BLOCKS`

**File:** `app/config.py`  
**Comment in code:** `# Bug 7 fix: Missing values 1 and 2 from api-specifications`

#### Buggy Code
```python
STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]
```

#### What Went Wrong
The API spec defines denominations as `[1, 2, 5, 10, 20, 50, 100]`. The `overtime_breakdown()` function is a greedy change-making algorithm — it divides the overtime value into the largest blocks that fit, then recurses on the remainder. Without `1` and `2`, any overtime value that is not a multiple of 5 has a remainder that cannot be represented and is **silently dropped**.

| Overtime | With Bug | Correct |
|---|---|---|
| `7` | `{"5": 1}` — drops 2 | `{"5": 1, "2": 1}` |
| `3` | `{}` — drops everything | `{"2": 1, "1": 1}` |
| `12` | `{"10": 1}` — drops 2 | `{"10": 1, "2": 1}` |

#### Symptom
`GET /resolve/overtime-breakdown?overtime=7` returns a result whose block values sum to less than the overtime passed in.

#### Fix
```diff
- STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]
+ STANDARD_EFFORT_BLOCKS: list[int] = [1, 2, 5, 10, 20, 50, 100]
```

---

## Summary Table — Bugs by API Endpoint

| Endpoint | Bug # | File | Root Cause |
|---|---|---|---|
| **All endpoints** | Bug 1 | `requirements.txt` | `pydantic==1.10.13` — v1 pinned, codebase uses v2 |
| `POST /queues/{queue_id}/tickets` | Bug 2 | `ticket_service.py` | `<` instead of `>` for `MAX_TICKETS_PER_QUEUE` check |
| `POST /queues/{queue_id}/tickets/bulk` | Bug 3 | `ticket_service.py` | No upfront capacity check before bulk loop |
| `POST /queues/{queue_id}/tickets/bulk` | Bug 4 | `ticket_service.py` | `current_ticket_count` never updated during bulk add |
| `POST /queues/{queue_id}/tickets/bulk` | Bug 10 | `ticket_service.py` | `db.commit()` inside loop — non-atomic, wrong count, race window |
| `GET /tickets/{ticket_id}` | Bug 8 | `schemas.py` | `queue_id: str` rejects `None` for standalone tickets |
| `PATCH /tickets/{ticket_id}/complexity` | Bug 11 | `schemas.py` | `gt=0` rejects valid complexity value `0` |
| `DELETE /queues/{queue_id}` | Bug 9 | `queue_service.py` | No guard — non-empty queues deletable, tickets orphaned |
| `POST /resolve` | Bug 5 | `resolve_service.py` | No null guard on `ticket.queue` → `AttributeError` |
| `POST /resolve` | Bug 6 | `routers/resolve.py` | `str(e)` on multi-arg `ValueError` returns tuple repr, not key |
| `POST /resolve` | Race | `db.py` | SQLite deferred transactions → concurrent stale reads |
| `GET /resolve/overtime-breakdown` | Bug 7 | `config.py` | Missing `1` and `2` in `STANDARD_EFFORT_BLOCKS` |
