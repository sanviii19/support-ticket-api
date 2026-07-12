# Support Ticket API — Bug Explanation Report

> All bugs are numbered **exactly as annotated in the source code comments**, traced through the git commit history.  
> Each entry shows: the buggy code before the fix, the root cause, the observable symptom, and the exact change that fixed it.

---

## Project Architecture (Quick Reference)

| Layer | File | Responsibility |
|---|---|---|
| Config | `app/config.py` | System-wide constants (limits, effort blocks, DB URL) |
| Models | `app/models.py` | SQLAlchemy ORM — `Queue`, `Ticket` |
| Schemas | `app/schemas.py` | Pydantic request/response validation |
| Services | `app/services/` | Business logic |
| Routers | `app/routers/` | HTTP endpoints |
| DB | `app/db.py` | Engine, session, transaction config |

---

## Bug 1 — Wrong Pydantic Version Pin

**File:** `requirements.txt` · **Comment:** `# Bug 1 fix: used pydantic v2`

### What the code looked like (`CHORE: added pydantic package` commit)
```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
sqlalchemy>=2.0.0
pydantic-settings>=2.0.0
pydantic==1.10.13          ← pinned to v1!
```

### What Went Wrong
When `pydantic` was explicitly added to the project, it was pinned to version `1.10.13` — the last release of **Pydantic v1**.

The rest of the project was written entirely using **Pydantic v2** APIs:
- `model_config = {"from_attributes": True}` — this is the v2 syntax (v1 used inner `class Config`)
- `pydantic-settings>=2.0.0` — this package **requires** Pydantic v2 as a dependency

With `pydantic==1.10.13` installed alongside `pydantic-settings>=2.0.0`, pip resolves a **version conflict** and the app either fails to install or runs with broken schema validation, as v1 and v2 are not API-compatible.

### Observable Symptom
Running `pip install -r requirements.txt` would either throw a dependency resolution error, or install mismatched packages causing `ImportError` / `ValidationError` at runtime on any endpoint that uses the schemas.

### Fix
```diff
- pydantic==1.10.13
+ pydantic>=2.0.0
```

---

## Bug 2 — Wrong Operator: Missing Global Max-Tickets Check in `add_ticket_to_queue`

**File:** `app/services/ticket_service.py` · **Comment:** `# Bug 2 - fix: check total tickets does not exceed MAX_TICKETS_PER_QUEUE config value`

### What the code looked like (initial commit)
```python
def add_ticket_to_queue(db, queue_id, data):
    queue = db.query(Queue).filter(Queue.id == queue_id).first()
    if not queue:
        raise ValueError("queue_not_found")
    if queue.current_ticket_count + data.quantity > queue.capacity:
        raise ValueError("capacity_exceeded")
    if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:  # ← BUG
        raise ValueError("capacity_exceeded")
```

### What Went Wrong
There are **two capacity checks**:
1. Against `queue.capacity` — the per-queue limit set at creation (correct, uses `>`)
2. Against `settings.MAX_TICKETS_PER_QUEUE` — a system-wide hard cap (wrong, uses `<`)

The second check uses the `<` (less-than) operator — **the complete inverse of the intended logic**.

- **Intended meaning:** Reject if the total would **exceed** `MAX_TICKETS_PER_QUEUE`
- **Actual behaviour:** Reject if the total would be **below** `MAX_TICKETS_PER_QUEUE`

So valid adds (where total < 10) are incorrectly blocked, and adds that overflow the limit (total > 10) silently pass through.

### Observable Symptom
`POST /queues/{queue_id}/tickets` responds with `400 capacity_exceeded` for every valid request where the queue hasn't filled up yet (the normal case), while letting overflowing requests succeed.

### Fix
```diff
- if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:
+ if queue.current_ticket_count + data.quantity > settings.MAX_TICKETS_PER_QUEUE:
      raise ValueError("capacity_exceeded")
```

---

## Bug 3 — Bulk Add Has No Upfront Capacity Check (Partial Overflow)

**File:** `app/services/ticket_service.py` · **Comment:** `# Bug 3 fix: check total capacity before adding anything`

### What the code looked like (initial commit)
```python
def bulk_add_tickets(db, queue_id, entries):
    queue = db.query(Queue).filter(Queue.id == queue_id).first()
    if not queue:
        raise ValueError("queue_not_found")
    # ← No capacity check at all before the loop starts
    added = 0
    for e in entries:
        if e.quantity <= 0:
            continue
        ticket = Ticket(title=e.title, complexity=e.complexity, queue_id=queue_id, quantity=e.quantity)
        db.add(ticket)
        added += 1
        db.commit()     # commits inside the loop — partial state is written
```

### What Went Wrong
`add_ticket_to_queue` (the single-add endpoint) checks capacity before inserting. But `bulk_add_tickets` has **no capacity check at all** before it begins the loop.

This means:
- A batch of 10 entries can be submitted to a queue that only has space for 2
- The entries commit one by one, overflowing the queue with no error returned
- Because each entry is committed individually inside the loop (see also Bug 10), partial data is **permanently written** to the database with no way to roll back

### Observable Symptom
`POST /queues/{queue_id}/tickets/bulk` silently accepts batches that overflow the queue's capacity. The queue's actual count exceeds its declared capacity with no `400` error returned.

### Fix
```python
# Bug 3 fix: check total capacity before adding anything
total_quantity = sum(e.quantity for e in entries if e.quantity > 0)
if queue.current_ticket_count + total_quantity > queue.capacity:
    raise ValueError("capacity_exceeded")
```
This pre-flight check runs **before** any ticket is added. If the batch doesn't fit, nothing is committed.

---

## Bug 4 — Bulk Add Never Updates `current_ticket_count`

**File:** `app/services/ticket_service.py` · **Comment:** `# Bug 4 fix: update count per ticket`

### What the code looked like (initial commit)
```python
for e in entries:
    if e.quantity <= 0:
        continue
    ticket = Ticket(title=e.title, complexity=e.complexity, queue_id=queue_id, quantity=e.quantity)
    db.add(ticket)
    added += 1
    db.commit()
    # ← queue.current_ticket_count is NEVER updated
```

### What Went Wrong
The single-add function `add_ticket_to_queue` correctly increments the counter:
```python
queue.current_ticket_count += data.quantity
```

But `bulk_add_tickets` **never updates `current_ticket_count`** at all. Tickets are physically inserted into the `tickets` table, but the queue's counter column stays at whatever value it was before the bulk operation.

**Consequences:**
- `GET /queues` shows a stale, incorrect `current_ticket_count`
- Bug 3's capacity pre-check reads a stale counter and cannot enforce capacity correctly
- Bug 9's deletion guard reads a stale counter, so a non-empty queue appears empty and gets deleted

### Observable Symptom
After `POST /queues/{queue_id}/tickets/bulk`, the queue's `current_ticket_count` in `GET /queues` stays unchanged despite tickets being added.

### Fix
```python
queue.current_ticket_count += e.quantity  # Bug 4 fix: update count per ticket
```

---

## Bug 5 — `AttributeError` When Resolving a Standalone Ticket

**File:** `app/services/resolve_service.py` · **Comment:** `# Bug 5 fix: added check for ticket.queue to avoid AttributeError`

### What the code looked like (initial commit)
```python
def resolve(db, ticket_id, effort_logged):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    ...
    ticket.quantity -= 1
    ticket.queue.current_ticket_count -= 1   # ← no null check on ticket.queue
    db.commit()
```

### What Went Wrong
The system supports **standalone tickets** — tickets created via `POST /tickets` with `queue_id = None`, not belonging to any queue. For such tickets, `ticket.queue` resolves to `None` via the SQLAlchemy relationship.

When `POST /resolve` is called on a standalone ticket, the line `ticket.queue.current_ticket_count -= 1` is equivalent to `None.current_ticket_count -= 1`, which raises:
```
AttributeError: 'NoneType' object has no attribute 'current_ticket_count'
```

### Observable Symptom
Resolving any standalone ticket returns `500 Internal Server Error` instead of successfully resolving it.

### Fix
```python
ticket.quantity -= 1
if ticket.queue:      # Bug 5 fix: added check for ticket.queue to avoid AttributeError
    ticket.queue.current_ticket_count -= 1
db.commit()
```

---

## Bug 6 — `insufficient_effort` ValueError Never Caught by the Router

**File:** `app/routers/resolve.py` · **Comment:** `# Bug 6 fix: check for 'insufficient_effort' in the first argument of the tuple`

### How the error is raised in `resolve_service.py`
```python
raise ValueError("insufficient_effort", ticket.complexity, effort_logged)
```
Python's `ValueError` accepts multiple positional arguments and stores them all in `e.args` as a **tuple**:
```python
e.args == ("insufficient_effort", ticket.complexity, effort_logged)
```

### What the router code looked like (initial commit)
```python
except ValueError as e:
    if str(e) == "ticket_not_found":
        raise HTTPException(status_code=404, ...)
    if str(e) == "out_of_stock":
        raise HTTPException(status_code=400, ...)
    if str(e) == "insufficient_effort":    # ← BUG
        required, logged = e.args[1], e.args[2]
        raise HTTPException(status_code=400, ...)
    raise
```

### What Went Wrong
`str(e)` on a **multi-argument** ValueError does **not** return just the first string. It returns the `repr` of the entire args tuple:
```python
str(ValueError("insufficient_effort", 5, 3))
# → "('insufficient_effort', 5, 3)"
```
This string never equals `"insufficient_effort"`, so the `if` block is silently skipped. The `raise` at the bottom re-raises the raw unhandled `ValueError` as a 500.

> **Note:** `str(e) == "ticket_not_found"` and `str(e) == "out_of_stock"` work correctly because those are **single-argument** ValueErrors — `str()` of a single-arg ValueError returns just that string.

### Observable Symptom
When a user sends insufficient effort (e.g., `effort_logged = 2` for a ticket with `complexity = 5`), the API returns `500 Internal Server Error` instead of the correct `400` with the required/logged detail.

### Fix
```diff
- if str(e) == "insufficient_effort":
+ if e.args[0] == "insufficient_effort":   # Bug 6 fix: check first arg of the tuple
```

---

## Bug 7 — Missing Values `1` and `2` in `STANDARD_EFFORT_BLOCKS`

**File:** `app/config.py` · **Comment:** `# Bug 7 fix: Missing values 1 and 2 from api-specifications`

### What the code looked like (initial commit)
```python
STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]
```

### What Went Wrong
The API specification defines the valid effort block denominations as `[1, 2, 5, 10, 20, 50, 100]`. The initial config was missing `1` and `2`.

The `overtime_breakdown()` function works like a **change-making algorithm** — it greedily divides the overtime value into the largest blocks that fit. Without `1` and `2`, any overtime not divisible by 5 has a remainder that is **silently dropped**:

| Overtime | With Bug | Should Be |
|---|---|---|
| `7` | `{"5": 1}` — drops 2 | `{"5": 1, "2": 1}` |
| `3` | `{}` — nothing returned | `{"2": 1, "1": 1}` |
| `12` | `{"10": 1}` — drops 2 | `{"10": 1, "2": 1}` |

### Observable Symptom
`GET /resolve/overtime-breakdown?overtime=7` returns an incomplete result — the sum of block values is less than the overtime value passed in.

### Fix
```diff
- STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]
+ STANDARD_EFFORT_BLOCKS: list[int] = [1, 2, 5, 10, 20, 50, 100]
```

---

## Bug 8 — `TicketDetailResponse.queue_id` Cannot Be `None` (Standalone Tickets)

**File:** `app/schemas.py` · **Comment:** `# Bug 8 fix: standalone tickets have queue_id = None`

### What the code looked like (initial commit)
```python
class TicketDetailResponse(TicketResponse):
    queue_id: str       # ← required non-null string field
```

### What Went Wrong
The `GET /tickets/{ticket_id}` endpoint returns a `TicketDetailResponse`. When the ticket is a **standalone ticket** (created with `queue_id = None`), Pydantic tries to serialize the database value `None` into the `str` field and raises a validation error.

This is an inconsistency with the data model: `Ticket.queue_id` in `models.py` is declared `nullable=True`, meaning `None` is a perfectly valid database value. But the schema rejects it.

### Observable Symptom
`GET /tickets/{ticket_id}` returns `500 Internal Server Error` for any standalone ticket (created via `POST /tickets` without a `queue_id`).

### Fix
```diff
- queue_id: str
+ queue_id: str | None  # Bug 8 fix: standalone tickets have queue_id = None
```

---

## Bug 9 — Queues With Tickets Can Be Deleted

**File:** `app/services/queue_service.py` · **Comment:** `# Bug 9 fix: Cannot delete a queue that still contains tickets`

### What the code looked like (initial commit)
```python
def delete_queue(db, queue_id):
    queue = get_queue_by_id(db, queue_id)
    if not queue:
        raise ValueError("queue_not_found")
    db.delete(queue)    # ← no check — deletes even if tickets exist
    db.commit()
```

### What Went Wrong
There is no guard preventing the deletion of a queue that still has tickets inside it.

Looking at the `Ticket` model in `models.py`:
```python
queue_id = Column(CHAR(36), ForeignKey("queues.id", ondelete="SET NULL"), nullable=True)
```
The FK is configured with `ondelete="SET NULL"`. When a queue is deleted, all tickets that belonged to it have their `queue_id` set to `NULL` — they become **orphaned standalone tickets** in the database with no warning to the caller.

### Observable Symptom
`DELETE /queues/{queue_id}` on a queue containing tickets succeeds with `200 OK`, but all those tickets remain in the database as orphaned standalone tickets with `queue_id = NULL`.

### Fix

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

## Bug 10 — Bulk Add Commits Inside the Loop (Non-Atomic)

**File:** `app/services/ticket_service.py` · **Comment:** `# Bug 10 fix: commit once after all entries so count is saved atomically`

### What the code looked like (initial commit)
```python
def bulk_add_tickets(db, queue_id, entries):
    ...
    added = 0
    for e in entries:
        if e.quantity <= 0:
            continue
        ticket = Ticket(title=e.title, ...)
        db.add(ticket)
        added += 1
        db.commit()          # ← commits after every single entry!
        time.sleep(0.05)     # demo: widens race window vs resolve
    return added
```

### What Went Wrong
Calling `db.commit()` inside the loop means **each ticket entry is its own separate database transaction**. A "bulk" operation is supposed to be all-or-nothing (atomic). With per-entry commits:

1. **Partial failures are silent:** If entry 3 of 5 fails, entries 1 and 2 are already permanently committed — they cannot be rolled back.

2. **Race condition amplified:** Between each commit, there is a window where another concurrent request (e.g., `POST /resolve`) can read an intermediate, partially-committed state. The `time.sleep(0.05)` call was deliberately placed here to **widen this window** for demonstration purposes.

3. **Wrong `added` count:** `added += 1` counts *entries* processed, not *tickets* added. A bulk entry `{"quantity": 5}` incremented `added` by 1 instead of 5, producing a wrong `added_count` in the API response.

### Observable Symptom
`POST /queues/{queue_id}/tickets/bulk` returns an incorrect `added_count`. Under concurrent load, the queue count becomes inconsistent. If any entry fails mid-loop, partial data is committed with no rollback.

### Fix
```python
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    queue.current_ticket_count += e.quantity
    added += e.quantity     # count actual tickets, not entries
db.commit()                 # Bug 10 fix: single commit — entire batch is one transaction
db.refresh(queue)
```

---

## Bug 11 — Complexity `0` Rejected in `TicketComplexityUpdate`

**File:** `app/schemas.py` · **Comment:** `# Bug 11 fix: Complexity can be 0`

### What the code looked like (initial commit)
```python
class TicketComplexityUpdate(BaseModel):
    complexity: int = Field(..., gt=0)   # strictly greater than 0 — rejects 0
```

### What Went Wrong
The `PATCH .../complexity` endpoint uses `TicketComplexityUpdate` to validate the request body. Using `gt=0` means complexity `0` is rejected with `422 Unprocessable Entity`.

This is **inconsistent** with all other schemas:
- `TicketCreate` uses `ge=0` → allows 0 at creation
- `TicketBulkEntry` uses `ge=0` → allows 0 in bulk create
- `TicketCreateStandalone` uses `ge=0` → allows 0 for standalone create

So a ticket could be **created** with `complexity = 0`, but could **never be updated back** to `complexity = 0`. Complexity `0` is a valid value per the API spec — it means no effort is required to resolve the ticket.

### Observable Symptom
`PATCH /queues/{queue_id}/tickets/{ticket_id}/complexity` with body `{"complexity": 0}` returns `422 Unprocessable Entity` even though `0` is valid at creation time.

### Fix
```diff
- complexity: int = Field(..., gt=0)
+ complexity: int = Field(..., ge=0)  # Bug 11 fix: Complexity can be 0
```

---

## Final Fix — Race Condition: Concurrent Resolves Go Below Zero

**File:** `app/db.py`  
**Commit:** `feat: implement atomicity with BEGIN IMMEDIATE transactions`

### The Root Problem
This is the **architectural root cause** that all the `time.sleep(0.05)` calls were designed to demonstrate.

SQLite uses **deferred transactions** by default (`BEGIN DEFERRED`). In this mode, a write lock is only acquired at the moment of the first actual write — not when the transaction starts. Between the initial READ and the eventual WRITE, another thread's transaction can also READ the same stale data and commit a conflicting write.

### The Race Scenario (without fix)
```
Thread A (resolve):               Thread B (resolve):
  READ ticket → quantity = 3
                                    READ ticket → quantity = 3
  quantity -= 1                     quantity -= 1
  COMMIT → quantity = 2
                                    COMMIT → quantity = 2  ← should be 1!
```
With 20 concurrent threads all resolving a ticket with `quantity = 3`, more than 3 can succeed — the quantity goes **negative**.

### The `time.sleep(0.05)` Calls
These were **deliberately injected** into the original code to make the race window wide enough to reproduce reliably:
```python
# In resolve_service.py
time.sleep(0.05)  # demo: widens race window for concurrent resolve/add

# In ticket_service.py (bulk_add_tickets)
time.sleep(0.05)  # demo: widens race window vs resolve
```
Without artificial latency, the READ→WRITE gap is microseconds and the race is hard to trigger. With 50ms of sleep, every concurrent test run produces the bug reliably.

### The Fix
```python
# In app/db.py
from sqlalchemy import create_engine, event

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "begin")
    def set_sqlite_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
```

`BEGIN IMMEDIATE` tells SQLite to acquire a **write lock at the very start** of every transaction, not lazily. This means:
- Only one writer can be active at a time
- All concurrent writers are **queued and serialized**
- The READ and WRITE within any transaction see the same consistent state

The `time.sleep` calls are also **removed** — they are no longer meaningful once the race is eliminated.

### Verified with `test_atomicity.py`
The test fires 20 concurrent `POST /resolve` requests against a ticket with `quantity = 3`:

| | Expected | With Bug | After Fix |
|---|---|---|---|
| Successful resolves | 3 | > 3 (race) | Exactly 3 ✓ |
| `out_of_stock` 400s | 17 | < 17 | Exactly 17 ✓ |
| Final `ticket.quantity` | 0 | Negative | 0 ✓ |

---

## Complete Bug Summary Table

| # | File | Buggy Code | Problem | Symptom |
|---|---|---|---|---|
| 1 | `requirements.txt` | `pydantic==1.10.13` | v1 pinned while codebase uses v2 API | Install failure / runtime schema errors |
| 2 | `ticket_service.py` | `< MAX_TICKETS_PER_QUEUE` | Wrong operator — inverted logic | Valid adds rejected, overflows pass through |
| 3 | `ticket_service.py` | No pre-check in `bulk_add_tickets` | No upfront capacity validation | Batches silently overflow queue capacity |
| 4 | `ticket_service.py` | `current_ticket_count` not updated in bulk | Counter never incremented during bulk add | Queue count always stale after bulk add |
| 5 | `resolve_service.py` | `ticket.queue.current_ticket_count -= 1` | No null guard on `ticket.queue` | `AttributeError` → 500 on standalone ticket resolve |
| 6 | `routers/resolve.py` | `str(e) == "insufficient_effort"` | `str()` of multi-arg ValueError returns tuple repr | `insufficient_effort` always gives 500 |
| 7 | `config.py` | `[5, 10, 20, 50, 100]` | Missing `1` and `2` from spec | Overtime remainder silently dropped |
| 8 | `schemas.py` | `queue_id: str` | Can't serialize `None` for standalone tickets | 500 on `GET /tickets/{id}` for standalone |
| 9 | `queue_service.py` | No guard before `db.delete(queue)` | Non-empty queues deletable | Tickets orphaned silently on queue delete |
| 10 | `ticket_service.py` | `db.commit()` inside bulk loop | Non-atomic batch — partial failures commit | Wrong count, partial data, race window widened |
| 11 | `schemas.py` | `complexity: int = Field(..., gt=0)` | Stricter than create schemas, rejects 0 | Valid `complexity=0` update returns 422 |
| — | `db.py` | Default SQLite deferred transactions | Concurrent reads see stale data | Quantity goes negative under concurrency |
