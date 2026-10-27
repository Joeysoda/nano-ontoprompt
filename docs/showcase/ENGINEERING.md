# Three engineering decisions

These notes describe behavior supported by the current code and tests. They do not invent development incidents or claim that every integration check has run in this checkout.

## 1. An order either gets all required material or releases its tentative allocation

An order that cannot ship should not strand a scarce component and prevent another feasible order from completing. The allocator gathers BOM leaves and tentative supplies, then releases those allocations if the full order is infeasible.

An alternative is to commit partial allocations immediately. That is simpler, but would turn allocation order into an accidental source of blocked inventory. Partial fulfillment could also be valid in another business domain; the current model explicitly chooses all-or-nothing orders.

The regression case in [test_blocked_order_does_not_strand_scarce_material](../../backend/unit_tests/test_material_shortage_decision.py) reduces available stock below one order's requirement and gives a smaller order a feasible demand. The smaller order must remain fulfillable. Other cases verify reservations, expiring commitments and BOM cycles.

Every response uses the same [simulation](../../backend/app/services/v2/material_shortage.py) on transformed input, so baseline and response metrics share semantics. The consequence is a bounded, explainable heuristic, not a proof of optimal scheduling. Hand-derived fixture answers are in [the known-answer tests](../../backend/unit_tests/test_l3harris_known_answer.py).

## 2. Approval must name an independent actor and a specific package version

Two requests can read the same package and both attempt to approve it. Checking a status in application memory alone does not serialize their writes.

The [transition code](../../backend/app/services/v2/decision_packages.py) requires an expected ETag, rejects the author's own review and records the reviewer. The API transaction serializes package transitions with a database row lock. Repeated request IDs have an explicit replay path.

SQLite is useful for the transition/API behavior tests but cannot establish PostgreSQL row-lock correctness. The [concurrent-review test](../../backend/tests/test_material_decision_package_flow.py) therefore requires a throwaway PostgreSQL database and expects exactly one success and one stale-ETag result. The integration CI job supplies PostgreSQL and rejects skipped tests.

Apply is a separate operation: an approval event cannot itself write arbitrary live edits. The server recompiles the selected response, validates its evidence and scenario version, and stages changes through the Scenario merge path.

## 3. Restoring object state cannot unsend an external notification

The ontology database and a procurement system cannot share one ordinary database transaction. Sending a notification before committing the object change risks an external action for a failed change; sending it afterward without a durable record risks losing it on a crash.

Decision Packages use an outbox after the merge and retain connector receipts and retry state. The included connector is explicitly a [fake procurement service](../../backend/app/services/v2/decision_packages.py), not a production ERP integration or a claim of exactly-once delivery to arbitrary systems.

Revert restores the ontology baseline and lists effects that cannot be undone automatically. The [real-service test](../../backend/tests/v2/test_material_decision_apply_real_falkor.py) exercises application, re-running on live state, receipts and reversion. Connector failure and replay behavior must remain visible rather than be hidden behind a successful UI message.

## Where the model belongs

Models can draft commentary from selected evidence. They do not determine numeric planning outcomes or waive a qualification constraint. Numeric occurrence is not semantic entailment: “protected revenue: 550000” cannot establish “revenue at risk increases to 550000.” The [regression case](../../backend/unit_tests/test_decision_explanations.py) keeps that distinction explicit.

The legacy natural-language Cypher execution endpoint is retired with HTTP 410 because a keyword validator cannot enforce ontology, object and field policies. A future natural-language query path should compile a restricted representation through the existing authorized Object Query machinery before execution.

Development uses AI assistance. These boundaries, examples and executable checks are the evidence a reviewer can inspect; no blanket claim of manually authored code, semantic verification or measured model superiority is made.
