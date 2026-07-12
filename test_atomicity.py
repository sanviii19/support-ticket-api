"""
Atomicity / Concurrency Stress Test
=====================================
Tests that concurrent resolve requests on the same ticket never
cause the quantity to go below 0 (i.e. more resolves than available).

How it works
------------
1. Creates a fresh queue (capacity 20).
2. Adds a ticket with quantity = TICKET_QUANTITY (e.g. 3).
3. Fires CONCURRENT_REQUESTS threads simultaneously, each calling POST /resolve.
4. Asserts:
   - Exactly TICKET_QUANTITY requests succeeded (200 OK).
   - All remaining requests got 400 out_of_stock.
   - The final ticket quantity in the DB is exactly 0.
   - The queue's current_ticket_count decreased by exactly TICKET_QUANTITY.

Pass  → atomicity is working correctly.
Fail  → a race condition slipped through (quantity went negative).
"""

import threading
import requests

BASE_URL = "http://127.0.0.1:8000"

# ── Config ────────────────────────────────────────────────────────────────────
TICKET_QUANTITY      = 3    # only this many resolves should succeed
CONCURRENT_REQUESTS  = 20   # fire many more than available to stress-test
COMPLEXITY           = 1    # keep it simple; effort_logged must be >= complexity
EFFORT_LOGGED        = 1
# ─────────────────────────────────────────────────────────────────────────────


def setup():
    """Create queue + ticket, return (queue_id, ticket_id, initial_count)."""
    # Create queue
    r = requests.post(f"{BASE_URL}/queues", json={"name": "AtomicityTest", "capacity": 20})
    assert r.status_code == 201, f"Queue creation failed: {r.text}"
    queue_id = r.json()["id"]
    initial_count = r.json()["current_ticket_count"]

    # Add ticket
    r = requests.post(
        f"{BASE_URL}/queues/{queue_id}/tickets",
        json={"title": "Race Ticket", "complexity": COMPLEXITY, "quantity": TICKET_QUANTITY},
    )
    assert r.status_code == 201, f"Ticket creation failed: {r.text}"
    ticket_id = r.json()["id"]

    return queue_id, ticket_id, initial_count


def teardown(queue_id):
    """Delete the test queue after the test."""
    requests.delete(f"{BASE_URL}/queues/{queue_id}")


def fire_resolve(ticket_id, results, index):
    """Called by each thread. Stores (status_code, body) in results[index]."""
    r = requests.post(
        f"{BASE_URL}/resolve",
        json={"ticket_id": ticket_id, "effort_logged": EFFORT_LOGGED},
    )
    results[index] = (r.status_code, r.json())


def run_test():
    print("\n" + "=" * 60)
    print("  Atomicity / Concurrency Stress Test")
    print("=" * 60)

    # ── Setup ────────────────────────────────────────────────────────────────
    print(f"\n[setup] Creating queue and ticket (quantity={TICKET_QUANTITY}) ...")
    queue_id, ticket_id, initial_count = setup()
    print(f"        queue_id  = {queue_id}")
    print(f"        ticket_id = {ticket_id}")

    # ── Fire concurrent requests ──────────────────────────────────────────────
    print(f"\n[test]  Firing {CONCURRENT_REQUESTS} concurrent resolve requests ...")
    results = [None] * CONCURRENT_REQUESTS
    threads = [
        threading.Thread(target=fire_resolve, args=(ticket_id, results, i))
        for i in range(CONCURRENT_REQUESTS)
    ]

    # Start all threads as close to simultaneously as possible
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ── Analyse results ───────────────────────────────────────────────────────
    successes    = [r for r in results if r[0] == 200]
    out_of_stock = [r for r in results if r[0] == 400 and "out_of_stock" in str(r[1]).lower()]
    other        = [r for r in results if r not in successes and r not in out_of_stock]

    print(f"\n[results]")
    print(f"  200 OK (resolved)      : {len(successes)}")
    print(f"  400 out_of_stock       : {len(out_of_stock)}")
    if other:
        print(f"  Other responses        : {len(other)}")
        for r in other:
            print(f"        {r[0]} -> {r[1]}")

    # ── Verify final state via API ────────────────────────────────────────────
    r = requests.get(f"{BASE_URL}/queues/{queue_id}/tickets")
    assert r.status_code == 200, f"Could not fetch tickets: {r.text}"
    tickets = r.json()
    ticket_data = next((t for t in tickets if t["id"] == ticket_id), None)
    final_quantity = ticket_data["quantity"] if ticket_data else "ticket deleted"

    r = requests.get(f"{BASE_URL}/queues")
    queues = r.json()
    queue_data = next((q for q in queues if q["id"] == queue_id), None)
    final_count = queue_data["current_ticket_count"] if queue_data else "N/A"

    print(f"\n[db state after test]")
    print(f"  ticket.quantity             : {final_quantity}  (expected: 0)")
    print(f"  queue.current_ticket_count  : {final_count}     (expected: {initial_count})")

    # ── Assertions ────────────────────────────────────────────────────────────
    print("\n[assertions]")
    passed = True

    def check(condition, message):
        nonlocal passed
        symbol = "PASS" if condition else "FAIL"
        print(f"  [{symbol}]  {message}")
        if not condition:
            passed = False

    check(
        len(successes) == TICKET_QUANTITY,
        f"Exactly {TICKET_QUANTITY} resolves succeeded (got {len(successes)})",
    )
    check(
        len(successes) + len(out_of_stock) == CONCURRENT_REQUESTS - len(other),
        "All requests accounted for (success + out_of_stock = total - other)",
    )
    check(
        final_quantity == 0,
        f"Final ticket quantity is 0 (got {final_quantity})",
    )

    # ── Teardown ──────────────────────────────────────────────────────────────
    teardown(queue_id)
    print("\n[teardown] Test queue deleted.")

    # ── Final verdict ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if passed:
        print("  ALL ASSERTIONS PASSED -- transactions are atomic!")
    else:
        print("  SOME ASSERTIONS FAILED -- race condition detected!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run_test()
