# Ontology Decision Workbench

> Latest integrated delivery: [`codex/merge-impact-chains`](https://github.com/Joeysoda/nano-ontoprompt/tree/codex/merge-impact-chains). This branch combines the FactoryNet temporal workbench and Plan A semantic-governance foundation with Plan B object exploration, governed operations, and scenario workflows.

**[中文原始说明](./README_zh.md)** · **[中文部署指南](./DEPLOYMENT_WORKBENCH_ZH.md)** · **[Plan A / Plan B integration contract](./docs/PLAN_A_PLAN_B_INTEGRATION_CONTRACT.md)**

The workbench is an open-source, Palantir Foundry-inspired platform for turning structured, temporal, and multimodal data into a governed ontology that users can query, inspect, operate, and test in what-if scenarios. It preserves the original pipeline and LLM extraction paths while adding a complete operational layer around stable semantic identities, provenance, object sets, actions, functions, and scenarios.

## Integrated scope

The current delivery joins two complementary layers:

| Layer | Responsibility | Current capabilities |
|---|---|---|
| **Plan A — semantic and data foundation** | Define what ontology resources mean, who may see them, and which data version produced a result | Versioned semantic resources, stable resource IDs, source mappings, authorization projections, execution/result manifests, schema migration planning, snapshot and graph-projection status |
| **Plan B — operational workbench** | Let users explore objects and turn governed data into decisions | Object Query, Object Sets and saved views; Object Explorer; Action and Function governance; scenario creation, preview, comparison, tracking and merge; temporal and graph analysis |

The merge also retains the existing construction platform: connectors, transformations, curated datasets, ontology mapping, quality audit, temporal replay, multimodal evidence, rule reasoning, and decision proposals.

## End-to-end workflow

```text
Source data
   -> connection / upload
   -> transform and curate
   -> ontology mapping and semantic review
   -> immutable revision + source evidence
   -> live or pinned data view
   -> Object Query / Object Explorer
   -> Action, Function, or What-If Scenario
   -> governed result, audit trail, and optional merge
```

Three prepared demonstration domains are included:

- **C-MAPSS FD001** — regular equipment-degradation data.
- **FactoryNet CNC** — event-at-a-time temporal construction, replay, snapshots, and dynamic ontology evolution.
- **I-BADAS** — multimodal evidence installation and inspection.

The repository also contains a public frePPLe supply-chain fixture used to demonstrate supplier resilience, Object Sets, actions, and scenario comparison.

## Major capabilities

### Data construction and ontology modeling

- Visual pipelines for file, SQL, MongoDB, and REST sources.
- Structured, semi-structured, and document transformation routes.
- Curated datasets with quality scoring, review, versioning, and provenance.
- Candidate entity/property/link mappings with human confirmation.
- Relation discovery through foreign keys, normalized values, alternate keys, and optional LLM assistance.
- Dynamic ontology editing with immutable revisions, dependency checks, batch validation, and history restore.

### Semantic governance and security

- Stable semantic resources for Object Types, Properties, Links, Interfaces, Value Types, and Struct Types.
- Versioned definitions bound to ontology revisions and metadata digests.
- `AuthorizationContext -> AuthorizationProjection` contracts for object, relation, and field visibility.
- Explicit live, pinned, snapshot, scenario, and degraded execution states.
- Result manifests that carry metadata, permission, source, adapter, and consistency identities.
- Dry-run and recoverable schema migrations for rename, cast, drop, source replacement, edit movement, and graph-projection rebuilds.

### Object Query and Object Explorer

- Typed filters, nested Boolean expressions, sorting, keyset pagination, aggregation, and comparison.
- Saved Object Sets, query views, immutable data views, and background query jobs.
- Object catalog, configurable columns, distributions, pivots, saved explorations, and multi-select operations.
- Object detail panels with properties, links, graph context, temporal snapshots, and time-series charts.
- Stable errors for permission, context, capability, and consistency failures instead of ambiguous empty results.

### Actions, Functions, and Logic

- Shared execution contexts for live and scenario operations.
- Action validation, conflict preview, execution, audit, and revert support.
- Function input/output contracts and governed binding to Object Sets and objects.
- Versioned Logic Assets with runtime contract checks.
- Semantica-backed bounded rule reasoning and explainable decision proposals.

### Scenarios and temporal operations

- What-if scenarios pinned to ontology revision, source/view identity, and execution context.
- Assumption editing, action preview, graph comparison, merge preview, and tracked scenario runs.
- FactoryNet replay with single-step, play, pause, resume, cancellation, and configurable event speed.
- Idempotent pushed events, watermarks, validity intervals, evidence references, and manually published snapshots.
- Scenario results remain isolated until an explicit governed merge; simulation does not silently mutate the live graph.

### Audit and verification

- ReAct-style ontology quality audit with bounded inspection tools.
- Unit, contract, migration, integration, browser, restart, and visual evidence suites.
- Plan B persistence and runtime benchmark scripts.
- Generated TypeScript semantic contracts checked against the backend Pydantic definitions.

## Architecture

| Concern | Implementation |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, Cytoscape.js, ECharts |
| API and services | FastAPI, Pydantic, SQLAlchemy, Alembic |
| Authoritative metadata and fact ledger | PostgreSQL; SQLite is supported for lightweight development and contract tests |
| Graph projection | FalkorDB; Neo4j remains available for compatibility paths |
| Object and file storage | MinIO with local development fallback |
| Analytical processing | DuckDB, pandas, PyArrow |
| Vector search | ChromaDB |
| Background work | Celery and Redis |
| Model gateway | LiteLLM; OpenAI, Anthropic, and compatible providers |
| Local reasoning model | Ollama, with `qwen3.5:0.8b` used by the current demo setup |

PostgreSQL owns revisions, facts, policies, mappings, and execution records. FalkorDB is a rebuildable graph projection used for graph traversal and operational reads. A revision label alone is not treated as a data snapshot; pinned reads also carry their source, view, metadata, and permission identities.

## Quick start

### Full Docker workbench

Prerequisites: Git, Docker with Compose, and Bash. Ollama is optional unless local model audit/reasoning is required.

```bash
git clone --branch codex/merge-impact-chains https://github.com/Joeysoda/nano-ontoprompt.git
cd nano-ontoprompt
./scripts/bootstrap_deployment.sh --seed-demo
```

The bootstrap script creates a local `.env` with generated secrets, prepares the external PostgreSQL and FalkorDB volumes, builds the service stack, applies migrations, and imports the public frePPLe demo fixture. It is designed to be rerunnable and does not commit credentials or live data.

Open:

- Workbench: [http://127.0.0.1:15173/overview](http://127.0.0.1:15173/overview)
- Backend health: [http://127.0.0.1:18080/health](http://127.0.0.1:18080/health)
- API documentation: [http://127.0.0.1:18080/docs](http://127.0.0.1:18080/docs)

The local compose overlay uses `local_single_user` mode, so it opens without a login screen. Do not expose that mode to a shared or public network. See [DEPLOYMENT_WORKBENCH_ZH.md](./DEPLOYMENT_WORKBENCH_ZH.md) for ports, model setup, data recovery, and server operations.

### Manual development

Prerequisites: Python 3.12 and Node.js 22.

```bash
# Backend
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-reasoning.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend, in another terminal
cd frontend
npm install
npm run dev
```

Manual development can use SQLite and synchronous execution for smaller tasks, but snapshot, projection, recovery, and deployment acceptance require the PostgreSQL/FalkorDB stack.

## Demo path

For a short leadership demo:

1. Open **Data Construction** and show one prepared regular, temporal, or multimodal source.
2. Open an ontology and show its semantic resources, source evidence, and revision history.
3. Use **Object Query** to filter, aggregate, compare, or save an Object Set.
4. Open an object to show its details, links, graph neighborhood, and temporal/time-series context.
5. Preview and run a governed Action, or create a What-If Scenario and compare it with the live baseline.
6. Show the result manifest, action/scenario audit trail, and explicit merge boundary.

FactoryNet's **Dynamic Evolution** page can additionally demonstrate event-at-a-time updates, replay controls, watermarks, historical validity, and manual snapshot publication.

## Repository layout

```text
nano-ontoprompt/
├── backend/
│   ├── alembic/                 # Versioned database migrations
│   ├── app/
│   │   ├── models/v2/           # Semantic, security, query, scenario, and temporal records
│   │   ├── routers/v2/          # Current REST API surface
│   │   ├── schemas/v2/          # Shared request/response contracts
│   │   ├── services/v2/         # Construction, governance, query, graph, action, and scenario logic
│   │   └── tasks/v2/            # Celery jobs
│   ├── scripts/                 # Contract export, verification, and benchmarks
│   └── tests/                   # Unit, contract, integration, and migration tests
├── frontend/
│   └── src/
│       ├── pages/data-management/
│       ├── pages/ontologies/
│       ├── pages/what-if/
│       └── test/e2e/            # Browser acceptance workflows
├── data/frepple_demo/           # Public supply-chain demonstration fixture
├── docs/                        # ADRs, integration contracts, plans, and acceptance guidance
├── scripts/                     # Bootstrap, imports, and deployment verification
├── docker-compose.yml           # Base service topology
├── docker-compose.local.yml     # Local ports and single-user demo mode
└── DEPLOYMENT_WORKBENCH_ZH.md   # Complete Chinese deployment guide
```

## Verification

Run checks appropriate to the area changed:

```bash
# Backend tests
cd backend
pytest

# Generated Plan A contract freshness
python scripts/export_plan_a_contract.py --check

# Frontend static checks and build
cd ../frontend
npm run lint:src
npm run build

# Browser workflows, with the stack running
npx playwright test
```

The repository also provides focused scripts for branch wiring, persistence across restart, supply-chain fixtures, Object Query/FalkorDB integration, and Plan B runtime benchmarks.

## Current boundaries

- The Docker configuration is a demonstration/development delivery. A shared production deployment must enable JWT, HTTPS, strong external secret management, network restrictions, backups, and monitoring.
- Plan A's core contracts and SQLite test coverage are present. Real PostgreSQL/FalkorDB migration recovery, projection reconciliation, and performance percentiles still require environment-specific acceptance evidence.
- Some legacy and Plan B endpoints still need full migration to the unified Plan A authorization projection and stable semantic resource IDs; consult the unchecked items in the [integration contract](./docs/PLAN_A_PLAN_B_INTEGRATION_CONTRACT.md).
- Rule-based scenarios demonstrate bounded logical consequences. They do not by themselves establish causal effects or maintenance-outcome predictions.
- Running database volumes, uploaded files, `.env`, and API credentials are intentionally excluded from Git.
- The independent `codex/next-stage-ontology-modules` branch is not part of this integrated delivery unless it is merged separately.

## Security and data handling

- Never commit `.env`, API keys, passwords, database volumes, uploads, or copied production data.
- Use `AUTH_MODE=jwt` outside an isolated local demo.
- Treat Object Set or Scenario sharing as resource access only; it must not expand access to underlying objects or fields.
- Keep masked values out of traces, logs, errors, and model prompts.
- Do not run `docker compose down -v` against an environment whose data volumes must be retained.

## License

MIT
