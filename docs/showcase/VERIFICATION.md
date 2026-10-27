# Verification record

Date: 2026-10-25. Scope: the current working tree, not a released tag, deployed service or completed GitHub Actions run.

## Fast checks

Use Python 3.12 in an isolated environment:

```bash
cd backend
pip install -r requirements.txt
python -m pytest unit_tests -q
```

`unit_tests/` is outside `tests/conftest.py`, so pure calculation/validation checks do not initialize the whole FastAPI application, its database or MCP server. They include hand-derived shortage outcomes, allocation counterexamples, limited explanation checks and query-draft rejection cases. Some tests import contract/model modules; this suite does not claim a dependency-free install.

Frontend:

```bash
cd frontend
npm ci
npm run lint:src
npm run build
```

The tracked npm lockfile is the frontend installation contract. TypeScript build metadata is stored under `.cache/`, outside installed dependencies.

## Service acceptance

[Verify workflow](../../.github/workflows/verify.yml) has separate unit, frontend and decision-integration jobs. The integration job supplies throwaway PostgreSQL and FalkorDB services, builds the isolated runner, then checks:

- Decision Package evidence, idempotency, stale versions, self-review and concurrent reviews.
- Applying a candidate through real graph edits, re-running and reverting.
- Runner network/filesystem/process limits, timeouts and output floods.
- The retired NL query endpoint returning 410 without opening a graph connection.

It fails if any selected test is skipped. Do not point `TEST_DATABASE_URL` at a real application database: the test fixtures create and drop tables. Linux runner subprocess tests are separate from container acceptance; only the latter can test the Compose trust boundary.

## Current local evidence

- `python -m pytest unit_tests -q`: **31 passed** after moving the pure tests out of app-wide fixtures.
- `python -m pytest unit_tests ../function_runner/tests -q`: **31 passed, 4 skipped** on Windows; the skipped tests require Linux resource limits.
- The runner image was built from this working tree and exercised on a disposable internal Docker network: **11 passed** in `tests/v2/test_function_runner_isolation.py`, including service-token enforcement, network, filesystem, subprocess, memory/CPU, output-flood and post-failure recovery checks.
- `npm run lint:src`: passed. `npm run build` initially hit a Windows permission error because Vite attempted to write its temporary bundled config under the existing `node_modules`; the build now uses Vite's runner config loader and passed after that change.
- Full-app tests initially failed to import in the host Python environment because it had MCP 1.29.0 instead of the repository's pinned 1.12.4. The container checks below use the repository dependencies; no broader full-suite pass is claimed.
- A backend image built from this working tree ran selected tests against disposable PostgreSQL 16 and FalkorDB 4.20.1 containers: **7 passed**. The selection covered package evidence/idempotency/concurrent review, real graph apply/re-run/revert, and the retired natural-language query endpoint.
- `bash scripts/bootstrap_deployment.sh --seed-decision-demo` built and started the full local Compose topology and completed the two-user seed against the existing development volumes. The seeder verified the known-answer baseline (**550,000** risk, **0** combined residual risk, **3,720** incremental cost) before publishing the private local credentials file.
- That run exposed a real legacy-volume migration failure: tables previously created by SQLAlchemy existed ahead of the recorded Alembic revision, causing later migrations to roll back the additive semantic columns. The affected migrations now explicitly reconcile the complete legacy table sets; the same database advanced from `0034_object_view_configs` to the current head. A fresh SQLite migration plus the pure suite then passed in the repository container: **32 passed**.
- A browser-control attempt timed out waiting for local window authorization. The full author/reviewer click-through, apply and revert sequence therefore remains a documented manual acceptance step, not a claimed automated pass. The Compose services are running locally after the successful seed.

CI definitions and test counts are not substitutes for a passing run on the commit being reviewed.

## Model evaluation

The known-answer planner checks are deterministic regressions, not an evaluation of model tool selection or retrieval quality. No model-versus-RAG improvement is claimed. Before publishing such a comparison, fix a held-out question set, gold evidence, model/prompt/token conditions, a specific baseline and a failure denominator; retain wrong answers, refusals, timeouts, latency and cost. No paid model evaluation was run for this change.
