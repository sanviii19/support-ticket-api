# Support Ticket API — Bug Explanation Report

> Each section is organized around a **feature area or operation** — not just raw endpoint paths.
> For each bug you will find: what the feature is supposed to do, what was broken, why it broke, what symptom it caused, and exactly what was changed to fix it.

---

## Table of Contents

1. [Project Setup — Broken Dependency (Bug 1)](#1-project-setup--broken-dependency)
2. [Adding a Single Ticket to a Queue (Bug 2)](#2-adding-a-single-ticket-to-a-queue)
3. [Adding Multiple Tickets at Once — Bulk Add (Bugs 3, 4, 10)](#3-adding-multiple-tickets-at-once--bulk-add)
4. [Fetching a Ticket's Details (Bug 8)](#4-fetching-a-tickets-details)
5. [Updating a Ticket's Complexity (Bug 11)](#5-updating-a-tickets-complexity)
6. [Deleting a Queue (Bug 9)](#6-deleting-a-queue)
7. [Resolving a Ticket (Bugs 5, 6, and the Race Condition)](#7-resolving-a-ticket)
8. [Calculating Overtime Breakdown (Bug 7)](#8-calculating-overtime-breakdown)
9. [Quick Reference Summary Table](#9-quick-reference-summary-table)

---

## 1. Project Setup — Broken Dependency

**Feature:** The application must install and start correctly before any endpoint can work.  
**Files involved:** `requirements.txt`

### What this is about

Before a single line of business logic runs, Python needs to install the correct packages. The project uses **Pydantic v2** throughout — for request body validation, response serialization, and settings management. If the wrong version is installed, the entire app fails.

---

### Bug 1 — Pydantic Was Pinned to the Wrong Major Version

**Code comment:** `# Bug 1 fix: used pydantic v2`

#### The Problem

When `pydantic` was explicitly added to `requirements.txt`, it was pinned to an exact version from the **old** major release:

```
pydantic==1.10.13
```

This is Pydantic **v1** — but every schema in the codebase (`schemas.py`) is written using Pydantic **v2** syntax. For example, `model_config = {"from_attributes": True}` is a v2-only feature. In v1 the equivalent was an inner `class Config`. These two versions are not compatible.

On top of that, `pydantic-settings>=2.0.0` (used for loading `.env` files) explicitly requires Pydantic v2. So there is a hard conflict: one line in `requirements.txt` demands v1 while another demands v2.

#### What You Would See

Running `pip install -r requirements.txt` would either:
- Fail immediately with a dependency conflict error, **or**
- Install a mismatched combination that causes `ImportError` or `ValidationError` when the server starts

Every single endpoint in the application would be broken because none of the schemas would work.

#### The Fix

```diff
- pydantic==1.10.13
+ pydantic>=2.0.0
```

Changed from a hard pin on v1 to a minimum version constraint that accepts v2 and above — consistent with the rest of the stack.

---

## 2. Adding a Single Ticket to a Queue

**Feature:** A client can add a ticket (with a title, complexity, and quantity) to an existing queue via `POST /queues/{queue_id}/tickets`. The system must reject adds that would overflow the queue's capacity.  
**Files involved:** `routers/queues.py` → `services/ticket_service.py`

### What this is about

Every queue has two limits:
- `queue.capacity` — the custom limit set when the queue was created
- `settings.MAX_TICKETS_PER_QUEUE` — a global hard cap in `config.py` (default: 10) that no queue can ever exceed regardless of its declared capacity

Both limits need to be enforced before inserting a ticket.

---

### Bug 2 — The Global Ticket Cap Check Had the Wrong Comparison Operator

**Code comment:** `# Bug 2 - fix: check total tickets does not exceed MAX_TICKETS_PER_QUEUE config value`

#### The Problem

Inside `add_ticket_to_queue()`, there were two validation checks:

```python
# Check 1 — against the queue's own declared capacity (correct)
if queue.current_ticket_count + data.quantity > queue.capacity:
    raise ValueError("capacity_exceeded")

# Check 2 — against the global system cap (WRONG OPERATOR)
if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:
    raise ValueError("capacity_exceeded")
```

The second check uses `<` (less than) instead of `>` (greater than). This **inverts the entire logic**:

- The code **intended** to say: "Raise an error if the new total would exceed the system cap"
- The code **actually** said: "Raise an error if the new total is still below the system cap"

In plain terms: it was blocking all the valid requests (where the queue isn't full) and silently allowing all the overflow requests (where the queue is over the limit).

#### What You Would See

- `POST /queues/{queue_id}/tickets` always returns `400 capacity_exceeded` as long as there is still space in the queue — which is the normal, happy-path case
- If you tried to add tickets beyond the limit, the request would actually succeed when it should fail

#### The Fix

```diff
- if queue.current_ticket_count + data.quantity < settings.MAX_TICKETS_PER_QUEUE:
+ if queue.current_ticket_count + data.quantity > settings.MAX_TICKETS_PER_QUEUE:
      raise ValueError("capacity_exceeded")
```

A single character change — `<` to `>` — corrects the logic.

---

## 3. Adding Multiple Tickets at Once — Bulk Add

**Feature:** A client can submit a list of ticket entries in one request via `POST /queues/{queue_id}/tickets/bulk`. All entries should be added together as a single unit — either all succeed, or none of them do.  
**Files involved:** `routers/queues.py` → `services/ticket_service.py`

### What this is about

Bulk operations are fundamentally different from single-item operations. When you add 10 ticket entries in one call, the entire batch needs to be validated first, then committed as one atomic transaction. The original implementation got all three of these wrong — there was no upfront validation, the queue counter was never updated, and entries were committed one by one inside a loop.

---

### Bug 3 — No Capacity Check Before Inserting Anything

**Code comment:** `# Bug 3 fix: check total capacity before adding anything`

#### The Problem

The single-ticket endpoint checks whether the add would overflow the queue before doing anything. The bulk endpoint had **no equivalent check** — it just looped directly into inserting:

```python
def bulk_add_tickets(db, queue_id, entries):
    queue = db.query(Queue).filter(Queue.id == queue_id).first()
    if not queue:
        raise ValueError("queue_not_found")
    # ← No capacity check here at all

    added = 0
    for e in entries:
        ticket = Ticket(...)
        db.add(ticket)
        added += 1
        db.commit()   # committed permanently, one by one
```

Imagine the queue has capacity 5 and currently holds 4 tickets. You submit a bulk batch of 3 entries. The code would insert all 3, bringing the total to 7 — 2 over the limit — with no error raised and a successful `200 OK` response.

#### What You Would See

The queue `current_ticket_count` silently exceeds `capacity`. `GET /queues` shows a count greater than the declared maximum with no indication anything went wrong.

#### The Fix

```python
# Bug 3 fix: check total capacity before adding anything
total_quantity = sum(e.quantity for e in entries if e.quantity > 0)
if queue.current_ticket_count + total_quantity > queue.capacity:
    raise ValueError("capacity_exceeded")
```

This pre-flight check runs **before the loop**. If the entire batch doesn't fit, nothing is inserted and the queue state is unchanged.

---

### Bug 4 — The Queue's Live Ticket Counter Was Never Updated

**Code comment:** `# Bug 4 fix: update count per ticket`

#### The Problem

`Queue` has a `current_ticket_count` column that tracks how many tickets are in it at all times. The single-ticket add correctly increments it:

```python
# In add_ticket_to_queue — correct
queue.current_ticket_count += data.quantity
```

But in `bulk_add_tickets`, there was **no equivalent line**. Tickets were inserted into the `tickets` table, but the queue's counter was never touched:

```python
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    added += 1
    db.commit()
    # queue.current_ticket_count is never updated — always stays stale
```

This has a cascading effect on other features:
- Bug 3's capacity check reads `current_ticket_count` — if it's always stale (stuck at its old value), the check can never correctly detect overflow
- Bug 9's deletion guard checks `current_ticket_count > 0` — if the counter is 0 even after bulk add, the guard silently passes and lets you delete a non-empty queue

#### What You Would See

After a successful `POST /queues/{queue_id}/tickets/bulk`, calling `GET /queues` shows `current_ticket_count` unchanged — as if nothing was ever added. The tickets exist in the database, but the queue doesn't know about them.

#### The Fix

```python
queue.current_ticket_count += e.quantity  # Bug 4 fix: update count per ticket
```

One line added inside the loop. For every entry that gets inserted, the counter is incremented by that entry's quantity.

---

### Bug 10 — Committing to the Database Inside the Loop (Non-Atomic Writes)

**Code comment:** `# Bug 10 fix: commit once after all entries so count is saved atomically`

#### The Problem

The original loop called `db.commit()` after every single entry:

```python
added = 0
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    added += 1
    db.commit()          # ← permanent write after every entry
    time.sleep(0.05)     # deliberately added to widen race window
return added
```

This creates three separate problems:

**Problem 1 — Partial failures are silent and unrecoverable**  
A database transaction should be all-or-nothing. With a commit inside the loop, if entry 3 of 5 fails (e.g., a DB error), entries 1 and 2 are already permanently committed. There is no rollback. The caller gets an error response but the database is in a half-written state.

**Problem 2 — Wrong `added_count` in the response**  
`added += 1` counts the number of *entries* processed, not the number of *tickets* added. If entry has `quantity = 5`, that single entry represents 5 tickets, but `added` only increments by 1. The `added_count` field in the API response is therefore always wrong — it reports entries, not tickets.

**Problem 3 — Deliberate race condition window (the `time.sleep` calls)**  
The `time.sleep(0.05)` call between commits was **intentionally planted** to demonstrate a race condition. Between each `db.commit()`, there is a 50ms gap during which a concurrent `POST /resolve` request can read the partially-updated `current_ticket_count` and make incorrect decisions based on stale data. Without the sleep, this window is too small to trigger reliably in tests — with it, the race fires consistently every time.

#### What You Would See

- `added_count` in the `POST /queues/{queue_id}/tickets/bulk` response is always lower than the actual number of tickets added
- Under concurrent load, `current_ticket_count` becomes inconsistent mid-operation
- If any entry fails mid-loop, some tickets are permanently in the database with no way to clean up

#### The Fix

```python
for e in entries:
    ticket = Ticket(...)
    db.add(ticket)
    queue.current_ticket_count += e.quantity
    added += e.quantity       # count actual tickets, not entries

db.commit()                   # Bug 10 fix: single commit — entire batch is one transaction
```

The commit is moved outside the loop. All tickets are staged (via `db.add`) within the same session, and committed together in a single transaction. Either all 5 entries commit, or none of them do. The `time.sleep` is removed since it no longer serves a purpose once the race is fixed.

---

## 4. Fetching a Ticket's Details

**Feature:** A client can fetch full details of any ticket by ID via `GET /tickets/{ticket_id}`. This works for both queue-attached tickets and standalone tickets (not in any queue).  
**Files involved:** `routers/tickets.py` → `services/ticket_service.py` → `schemas.py`

### What this is about

The system supports two types of tickets: those that belong to a queue (`queue_id` is set) and standalone tickets (`queue_id` is `None`, created via `POST /tickets` without specifying a queue). The response schema must accommodate both types.

---

### Bug 8 — The Response Schema Didn't Allow `queue_id` to Be Absent

**Code comment:** `# Bug 8 fix: standalone tickets have queue_id = None`

#### The Problem

`TicketDetailResponse` is the Pydantic schema used to serialize the `GET /tickets/{ticket_id}` response. It was defined as:

```python
class TicketDetailResponse(TicketResponse):
    queue_id: str    # required, must be a string — cannot be None
```

The database model (`models.py`) declares `queue_id` as `nullable=True`:

```python
queue_id = Column(CHAR(36), ForeignKey("queues.id", ondelete="SET NULL"), nullable=True)
```

So `None` is a completely valid value in the database. But when Pydantic tries to build a `TicketDetailResponse` with `queue_id=None`, it fails validation because `str` does not accept `None`.

The mismatch is between what the database stores (optionally `None`) and what the schema expects (always a string).

#### What You Would See

`GET /tickets/{ticket_id}` returns `500 Internal Server Error` for any standalone ticket. Queue-attached tickets work fine — only standalone tickets are broken.

#### The Fix

```diff
- queue_id: str
+ queue_id: str | None  # Bug 8 fix: standalone tickets have queue_id = None
```

`str | None` (a Python Union type) tells Pydantic to accept either a string or `None`.

---

## 5. Updating a Ticket's Complexity

**Feature:** A client can update the complexity score of an existing ticket via `PATCH /tickets/{ticket_id}/complexity`. The new complexity value must be a valid non-negative integer.  
**Files involved:** `routers/tickets.py` → `services/ticket_service.py` → `schemas.py`

### What this is about

Complexity is an integer that represents how difficult a ticket is to resolve. A complexity of `0` is a perfectly valid value — it means the ticket requires no effort to resolve. The constraint on complexity should be "must be zero or greater" (`>= 0`), not "must be strictly positive" (`> 0`).

---

### Bug 11 — Complexity Zero Was Incorrectly Rejected on Update

**Code comment:** `# Bug 11 fix: Complexity can be 0`

#### The Problem

The update schema used a stricter validator than the create schemas:

```python
# In schemas.py — the UPDATE schema (buggy)
class TicketComplexityUpdate(BaseModel):
    complexity: int = Field(..., gt=0)   # gt = greater than — rejects 0

# In schemas.py — the CREATE schemas (correct)
class TicketCreate(BaseModel):
    complexity: int = Field(..., ge=0)   # ge = greater than or equal — allows 0

class TicketBulkEntry(BaseModel):
    complexity: int = Field(..., ge=0)   # also allows 0

class TicketCreateStandalone(BaseModel):
    complexity: int = Field(..., ge=0)   # also allows 0
```

The inconsistency means: you can **create** a ticket with `complexity = 0`, but you can **never update** any ticket's complexity back to `0`. The constraint is tighter on the update path for no reason.

#### What You Would See

`PATCH /tickets/{ticket_id}/complexity` with body `{"complexity": 0}` returns:
```json
422 Unprocessable Entity
```
Even though a ticket was created with `complexity = 0` in the first place.

#### The Fix

```diff
- complexity: int = Field(..., gt=0)
+ complexity: int = Field(..., ge=0)  # Bug 11 fix: Complexity can be 0
```

Changed `gt` (greater than) to `ge` (greater than or equal) to match all the create schemas.

---

## 6. Deleting a Queue

**Feature:** A client can delete a queue by ID via `DELETE /queues/{queue_id}`. A queue can only be deleted if it is empty — if it still contains tickets, the operation must be rejected.  
**Files involved:** `routers/queues.py` → `services/queue_service.py`

### What this is about

Deleting a queue that still holds tickets is a data-integrity problem. The database model uses `ondelete="SET NULL"` on the `queue_id` foreign key — meaning if a queue is deleted, all its tickets have their `queue_id` silently set to `NULL`. They become orphan records that no longer belong to anything. The API must refuse to delete a non-empty queue.

---

### Bug 9 — The Delete Operation Had No Guard Against Non-Empty Queues

**Code comment:** `# Bug 9 fix: Cannot delete a queue that still contains tickets`

#### The Problem

The original `delete_queue()` function only checked whether the queue existed, then deleted it unconditionally:

```python
def delete_queue(db, queue_id):
    queue = get_queue_by_id(db, queue_id)
    if not queue:
        raise ValueError("queue_not_found")
    db.delete(queue)    # ← deletes immediately, even if queue has 100 tickets
    db.commit()
```

Because the FK has `ondelete="SET NULL"`, the database doesn't throw a constraint error — it silently sets `queue_id = NULL` on all orphaned tickets. The caller receives `200 OK` and has no idea that tickets were left behind.

There is a second consequence: this bug interacts with Bug 4. Since `bulk_add_tickets` never updated `current_ticket_count`, after a bulk add the counter would still be `0`. Even if you added the guard `if queue.current_ticket_count > 0`, Bug 4 made it always read `0` — so the guard would pass and the queue would still be deletable. Both bugs needed to be fixed together.

#### What You Would See

`DELETE /queues/{queue_id}` succeeds with `200 OK` for a queue that has tickets. The tickets remain in the `tickets` table with `queue_id = NULL` — they show up in the system but are no longer associated with any queue.

#### The Fix

**In `queue_service.py` — add the guard before deleting:**
```python
# Bug 9 fix: Cannot delete a queue that still contains tickets
if queue.current_ticket_count > 0:
    raise ValueError("queue_has_tickets")
```

**In `routers/queues.py` — handle the new error and return a proper 400:**
```python
if str(e) == "queue_has_tickets":
    raise HTTPException(
        status_code=400,
        detail="Cannot delete a queue that still contains tickets",
    )
```

---

## 7. Resolving a Ticket

**Feature:** A client resolves a ticket by submitting the effort they logged via `POST /resolve`. The system checks that the effort is sufficient, decrements the ticket's quantity by 1, adjusts the queue's counter, and returns the overtime (surplus effort beyond what was needed).  
**Files involved:** `routers/resolve.py` → `services/resolve_service.py` → `app/db.py`

### What this is about

Resolving a ticket is the most complex operation in the system. It involves:
- A null-safety check (the ticket might not belong to a queue)
- Correct exception handling in the router (passing extra data with the error)
- Concurrency safety (multiple users might try to resolve the same ticket at the same time)

Three separate bugs existed in this flow.

---

### Bug 5 — Crash When Resolving a Ticket That Doesn't Belong to Any Queue

**Code comment:** `# Bug 5 fix: added check for ticket.queue to avoid AttributeError`

#### The Problem

After decrementing the ticket's quantity, the code updated the parent queue's counter:

```python
ticket.quantity -= 1
ticket.queue.current_ticket_count -= 1   # ← assumes ticket.queue always exists
db.commit()
```

But the system allows **standalone tickets** — created via `POST /tickets` without specifying a `queue_id`. For these tickets, `ticket.queue` is `None` (the SQLAlchemy relationship returns `None` because there is no parent row to join to). Accessing `.current_ticket_count` on `None` raises:

```
AttributeError: 'NoneType' object has no attribute 'current_ticket_count'
```

Python can't find `current_ticket_count` on a `None` object and crashes.

#### What You Would See

`POST /resolve` with the ID of a standalone ticket returns `500 Internal Server Error`. The resolve never completes, and the quantity is not decremented (the transaction is rolled back on the crash).

#### The Fix

```python
ticket.quantity -= 1
if ticket.queue:      # Bug 5 fix: check ticket.queue is not None before accessing it
    ticket.queue.current_ticket_count -= 1
db.commit()
```

One `if` guard is enough. If the ticket has a queue, update the counter. If it doesn't, skip that step.

---

### Bug 6 — The "Insufficient Effort" Error Was Never Properly Caught

**Code comment:** `# Bug 6 fix: check for 'insufficient_effort' in the first argument of the tuple`

#### The Problem

When the logged effort is less than the ticket's complexity, `resolve_service.py` raises a `ValueError` with **multiple arguments** — the error key, the required complexity, and the logged effort — so the router can build a detailed error response:

```python
raise ValueError("insufficient_effort", ticket.complexity, effort_logged)
```

Python's `ValueError` stores all arguments in a tuple called `e.args`. So:
```python
e.args == ("insufficient_effort", 5, 3)
```

In the router, the code was trying to catch this with `str(e)`:

```python
if str(e) == "insufficient_effort":   # ← this never matches
    required, logged = e.args[1], e.args[2]
    ...
```

The issue is what `str(e)` actually returns for a multi-argument `ValueError`. It doesn't return just the first argument — it returns the **string representation of the entire tuple**:

```python
str(ValueError("insufficient_effort", 5, 3))
# returns: "('insufficient_effort', 5, 3)"
```

So `str(e)` is `"('insufficient_effort', 5, 3)"`, which never equals the plain string `"insufficient_effort"`. The `if` block is silently skipped every time insufficient effort is logged, and the bare `raise` at the end re-raises the `ValueError` as an unhandled exception.

> **Why does it work for the other errors?**  
> `"ticket_not_found"` and `"out_of_stock"` are raised as `ValueError("ticket_not_found")` — single-argument. For a single-argument `ValueError`, `str(e)` does return just that string. Only multi-argument ValueErrors have this tuple-repr behavior.

#### What You Would See

`POST /resolve` with `effort_logged = 2` on a ticket with `complexity = 5` should return:
```json
400 Bad Request
{ "error": "Insufficient effort logged", "required": 5, "logged": 2 }
```

Instead it returns `500 Internal Server Error`.

#### The Fix

```diff
- if str(e) == "insufficient_effort":
+ if e.args[0] == "insufficient_effort":   # Bug 6 fix: check first arg of the tuple directly
```

`e.args[0]` directly reads the first element of the tuple — the string `"insufficient_effort"` — which correctly matches.

---

### Race Condition — Multiple Users Resolving the Same Ticket at the Same Time

**Files involved:** `app/db.py`, `app/services/resolve_service.py`, `app/services/ticket_service.py`

#### The Problem

This is the most serious bug in the system — a **concurrency issue** that can cause the ticket quantity to go negative.

SQLite's default transaction mode is `BEGIN DEFERRED`. In this mode, a write lock is only acquired when the first write actually happens — not when the transaction starts. The sequence for a resolve is:

1. **Read** the ticket (`ticket.quantity = 3`)
2. _... some time passes ..._
3. **Write** `ticket.quantity = 2` and commit

The dangerous window is between step 1 and step 3. If another thread is also resolving the same ticket:

```
Thread A:  READ qty=3 ──────────────────── WRITE qty=2 → COMMIT
Thread B:          READ qty=3 → WRITE qty=2 → COMMIT
```

Both threads read `quantity = 3`. Both decrement to 2 and commit. The final quantity is `2`, but it should be `1` — **one resolve was lost**. With enough concurrent threads, the quantity goes **negative**.

#### The `time.sleep(0.05)` Calls — Deliberate Bug Demonstration

The original code had `time.sleep(0.05)` calls in both `resolve_service.py` and `ticket_service.py`:

```python
# In resolve_service.py
time.sleep(0.05)  # demo: widens race window for concurrent resolve/add

# In ticket_service.py
time.sleep(0.05)  # demo: widens race window vs resolve
```

These 50-millisecond delays were **deliberately planted** to make the race condition reliably reproducible. Without them, the READ-to-WRITE gap is measured in microseconds — hard to hit in a test. With the sleep, every concurrent test run triggers the race. Once the race condition is properly fixed, these sleeps serve no purpose and are removed.

#### What You Would See (Without Fix)

Running `test_atomicity.py` fires 20 concurrent `POST /resolve` requests at a ticket with `quantity = 3`. With the bug:
- More than 3 requests succeed (e.g., 5 or 6)
- The ticket quantity ends up at `-2` or `-3`
- The queue's `current_ticket_count` is also inconsistent

#### The Fix — Force SQLite to Acquire Write Lock Immediately

```python
# In app/db.py
from sqlalchemy import create_engine, event

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "begin")
    def set_sqlite_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
```

`BEGIN IMMEDIATE` tells SQLite to acquire the write lock **at the start of the transaction** — before any read happens. This means:
- Only one transaction can be in the "writing" state at a time
- All other concurrent transactions must wait for the lock to be released
- Every transaction sees a consistent snapshot from the beginning, so no two threads can read the same stale `quantity = 3` and both decrement it

The `time.sleep` calls are removed in the same commit because the race is now structurally impossible.

#### Verified by `test_atomicity.py`

The test fires 20 concurrent resolves at a ticket with `quantity = 3`:

| Metric | Expected | Before Fix | After Fix |
|---|---|---|---|
| Successful resolves (200 OK) | Exactly 3 | More than 3 (race) | ✅ Exactly 3 |
| Out-of-stock rejections (400) | Exactly 17 | Fewer than 17 | ✅ Exactly 17 |
| Final `ticket.quantity` | 0 | Negative number | ✅ 0 |
| Final `queue.current_ticket_count` | Decreased by 3 | Inconsistent | ✅ Correct |

---

## 8. Calculating Overtime Breakdown

**Feature:** A client can pass an overtime (surplus effort) value to `GET /resolve/overtime-breakdown?overtime=N` and receive a breakdown of how that overtime maps to the standard effort blocks defined in the system config.  
**Files involved:** `routers/resolve.py` → `services/resolve_service.py` → `config.py`

### What this is about

The overtime breakdown works like a **change-making algorithm** — given an overtime value, break it down into the fewest pieces using the available block sizes. For example, overtime of `27` breaks down as `20 + 5 + 2`. The available denominations are defined in `STANDARD_EFFORT_BLOCKS` in `config.py`. If those denominations are wrong, the breakdown will be incomplete.

---

### Bug 7 — Two Block Sizes Were Missing From the Configuration

**Code comment:** `# Bug 7 fix: Missing values 1 and 2 from api-specifications`

#### The Problem

The API specification states that the standard effort block sizes are `[1, 2, 5, 10, 20, 50, 100]`. The initial `config.py` was missing the two smallest values:

```python
STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]   # missing 1 and 2
```

The `overtime_breakdown()` function in `resolve_service.py` is a greedy algorithm:

```python
def overtime_breakdown(overtime):
    blocks = sorted(settings.STANDARD_EFFORT_BLOCKS, reverse=True)  # [100, 50, 20, 10, 5]
    remaining = overtime
    for b in blocks:
        count = remaining // b
        if count > 0:
            result[str(b)] = count
            remaining -= count * b
    return {"overtime": overtime, "blocks": result}
```

After the loop finishes, if there is still a `remaining` value that is non-zero and smaller than the smallest block (`5`), it simply gets dropped — there is no error, no warning, and the result silently under-counts.

| Overtime Value | Result With Bug | Correct Result |
|---|---|---|
| `7` | `{"5": 1}` — sum is 5, not 7 | `{"5": 1, "2": 1}` — sum is 7 ✓ |
| `3` | `{}` — sum is 0, not 3 | `{"2": 1, "1": 1}` — sum is 3 ✓ |
| `12` | `{"10": 1}` — sum is 10, not 12 | `{"10": 1, "2": 1}` — sum is 12 ✓ |
| `25` | `{"20": 1, "5": 1}` — sum is 25 ✓ | `{"20": 1, "5": 1}` — same ✓ |

Values divisible by 5 happen to produce correct results — the bug is invisible for those. For any other value, the breakdown silently drops the remainder.

#### What You Would See

`GET /resolve/overtime-breakdown?overtime=7` returns:
```json
{ "overtime": 7, "blocks": { "5": 1 } }
```
The blocks sum to `5`, not `7`. The missing `2` is silently gone.

#### The Fix

```diff
- STANDARD_EFFORT_BLOCKS: list[int] = [5, 10, 20, 50, 100]
+ STANDARD_EFFORT_BLOCKS: list[int] = [1, 2, 5, 10, 20, 50, 100]
```

Adding `1` and `2` means any integer overtime value can now be fully broken down, because `1` acts as a "catch-all" for any remaining amount.

---

## 9. Quick Reference Summary Table

| Feature / Operation | Endpoint | Bug # | File | What Was Broken |
|---|---|---|---|---|
| App starts correctly | (all endpoints) | Bug 1 | `requirements.txt` | `pydantic==1.10.13` — v1 pinned, whole app broken |
| Add single ticket to queue | `POST /queues/{queue_id}/tickets` | Bug 2 | `ticket_service.py` | `<` instead of `>` — capacity check inverted |
| Add multiple tickets at once | `POST /queues/{queue_id}/tickets/bulk` | Bug 3 | `ticket_service.py` | No capacity check before loop — silent overflow |
| Add multiple tickets at once | `POST /queues/{queue_id}/tickets/bulk` | Bug 4 | `ticket_service.py` | `current_ticket_count` never incremented in bulk |
| Add multiple tickets at once | `POST /queues/{queue_id}/tickets/bulk` | Bug 10 | `ticket_service.py` | Commit inside loop — not atomic, wrong count |
| Fetch ticket details | `GET /tickets/{ticket_id}` | Bug 8 | `schemas.py` | `queue_id: str` rejects `None` for standalone tickets |
| Update ticket complexity | `PATCH /tickets/{ticket_id}/complexity` | Bug 11 | `schemas.py` | `gt=0` wrongly rejects valid value `0` |
| Delete a queue | `DELETE /queues/{queue_id}` | Bug 9 | `queue_service.py` | No guard — tickets silently orphaned on delete |
| Resolve a ticket | `POST /resolve` | Bug 5 | `resolve_service.py` | `ticket.queue` not null-checked → `AttributeError` |
| Resolve a ticket | `POST /resolve` | Bug 6 | `routers/resolve.py` | `str(e)` on multi-arg `ValueError` returns tuple, not string |
| Resolve a ticket | `POST /resolve` | Race | `db.py` | SQLite deferred locks → concurrent resolves go negative |
| Overtime breakdown | `GET /resolve/overtime-breakdown` | Bug 7 | `config.py` | `1` and `2` missing from effort blocks — remainder dropped |
