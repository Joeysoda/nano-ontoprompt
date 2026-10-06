# Plan B acceptance status — 2026-10-05

Status: **engineering implementation and structural visual remediation complete; production acceptance awaits blind product validation**.

This record separates executable evidence from acceptance work that still
requires people or a dedicated end-to-end run. It must not be used to claim
that every gate in `docs/PLAN_PERSON_B_OPERATIONAL_RUNTIME_UX.md` has passed.

## Passed evidence

- Backend Plan B contract/integration suite: 38 passed. It covers Action
  compiler parity, revert, Function governance, Explorer catalog and sets,
  Object Views, Scenario tracking/merge behavior, temporal contracts and
  access control, and SQLite/PostgreSQL migration paths.
- The final combined Plan B plus infrastructure-health regression passed
  44 tests with one environment-gated skip. The skip is the isolated
  PostgreSQL migration test when `OBJECT_SET_TEST_POSTGRES_URL` is unset; the
  real PostgreSQL migration/restart evidence below covers that path.
- Browser Explorer contract/workflow suite: 14 passed. It covers filter/URL
  contracts, slow-load recovery, permission failure and retry, conflict and
  duplicate-submit prevention, filter/column/chart editing, multi-object
  preview, keyboard operation, and a 390 px viewport.
- The deterministic visual-evidence suite passes 9 browser scenarios and
  produces 12 current-state artifacts covering Results/save/columns, nested
  filters, editable Explore layout, multi-select, two-color Compare,
  Pivot/Search Around, Action preview/success/conflict, and narrow layout.
  Official Palantir assets, current screenshots, hashes, and
  state-by-state verdicts are recorded in
  `plan_b_object_explorer_visual_review_2026-10-05.md`.
- Frontend production build passed. Route and ontology-detail lazy loading
  keep the entry bundle at 349.08 kB.
- The full frontend source tree passes `npm run lint:src` with zero errors and
  zero warnings. The pre-existing 132 errors and 12 warnings were removed
  across multimodal, regular/temporal data management, Pipeline, Curated, and
  legacy domain E2E files. `npm run lint:scripts` also passes. Command results
  are recorded in `plan_b_static_validation_2026-10-05.json`.
- `git diff --check` passes.
- PostgreSQL upgraded to the single Alembic head
  `0034_object_view_configs`; a repeated upgrade preserves data. The Alembic
  bootstrap widens its version column before long revision identifiers are
  written, and migrations 0033/0034 tolerate tables created by old startup
  behavior.
- Real Docker JWT read chain passed against PostgreSQL and FalkorDB:
  unauthenticated 403, invalid token 401, authenticated catalog 200, and
  authenticated Demand load 200 with five objects.
- Real JWT write/restart verification passed with
  `python -m scripts.verify_plan_b_persistence`. Separate application
  processes executed full startup against the same PostgreSQL/FalkorDB.
  The second process recovered the saved Exploration definition, live status
  `reviewing`, isolated Scenario status `approved`, two completed Action logs,
  and compatible Object View configuration version 1. Disposable SQL rows
  and graph namespaces were removed successfully.
- The same fixture passed before and after a real
  `docker compose restart backend` while the service ran in JWT mode. The
  container was restored to `local_single_user`, `/health` returned database
  and FalkorDB `ok`, and no disposable Plan B projects remained.
- A JWT-authenticated real-browser run then reopened the same object after the
  Docker restart and rendered configured Object View version 1 with live
  status `reviewing`. The Playwright test is
  `frontend/src/test/e2e/object-explorer-real-restart.spec.ts`; its screenshot
  is `plan_b_object_panel_after_restart_2026-10-05.png`.
- Docker authentication is now explicitly overridable with
  `BACKEND_AUTH_MODE`/`FRONTEND_AUTH_MODE`. The backend container no longer
  uses Uvicorn hot reload because WatchFiles crashed on an unreadable Windows
  bind-mount test directory; Docker restart supervision is now stable.
- Optional Neo4j/MinIO/Chroma probes no longer block `/health` on Docker DNS
  retries. TCP resolution is bounded and cached, protocol clients use explicit
  timeouts, and FalkorDB uses socket timeouts. Against the restored Compose
  stack, the first health response completed in 0.622 seconds and the cached
  response in 0.011 seconds; its seven health tests pass.
- Runtime measurements are saved in
  `plan_b_runtime_benchmark_2026-10-05.json`. Measured p95 values were
  110.254 ms load, 111.025 ms pivot, 116.162 ms compare, 10.783 ms Action
  preview, and 19.023 ms Action submit. Each measured operation used one HTTP
  request. Observed cgroup memory stayed below 538.2 MiB.

## Open acceptance gates

1. **Blind product validation — pending.** The 12-task participant packet is
   `plan_b_blind_validation_packet_2026-10-05.md`; known answers are isolated
   in `plan_b_blind_known_answers_2026-10-05.json`. The new-user,
   manufacturing-user, and Agent result rows are intentionally empty until
   those participants run the tasks without rehearsal.

The paired screenshot review was repeated after remediation. Filter discovery,
chart-grid editing, multi-object preview, two-color Compare, save dialogs,
Results column controls, traversal chips, and the post-Action toast now pass
the structural gate. The remaining partial findings are exact-density or
direct-manipulation refinements and are recorded in the visual review rather
than treated as missing core states.

No result above substitutes automated tests for the remaining blind human
validation gate.
