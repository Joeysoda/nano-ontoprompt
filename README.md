# nano-ontoprompt

> Current workbench delivery: `factorynet-temporal-workbench`. The local demo uses C-MAPSS FD001, FactoryNet CNC, and I-BADAS with Docker, Celery, LiteLLM, and Ollama. See the [Chinese Docker and backend deployment guide](./DEPLOYMENT_WORKBENCH_ZH.md) for the exact ports and startup commands.

**[中文文档](./README_zh.md)**

A lightweight, Palantir Foundry-inspired platform for building domain ontologies from raw data. Connect a source, select the fields and samples to process, confirm the ontology mapping, and inspect entity types, real instances, relations, logic rules, and evidence in one local workbench.

Two build paths are supported:

- **Pipeline Mapping** (v2) — full data-integration chain: `Data Connection → Raw Storage → Transform → Curated Dataset → Ontology Mapping`
- **Simple LLM Extraction** (v1) — legacy document extraction endpoint retained for compatibility

---

## What is an Ontology?

An ontology is a formal representation of knowledge in a specific domain — a shared vocabulary of concepts and the relationships between them. Think of it as the structured backbone that turns raw data into machine-readable, queryable knowledge.

In nano-ontoprompt, every ontology is made of these building blocks:

| Building Block | What it captures | Example |
|---|---|---|
| **Entity (Object Type)** | A key concept mapped from a curated dataset, one node per data row | `Supplier`, `PurchaseOrder` |
| **Relation (Link Type)** | An edge between entities, inferred from foreign keys and cross-dataset value overlap | `PurchaseOrder -[HAS_SUPPLIER]-> Supplier` |
| **Logic Rule** | The rule layer: mapping / validation / state / inference / automation rules discovered from schema, quality reports and relations | `amount > 0`, state machine on `库存状态` |
| **Evidence** | The traceable source material behind a type, property, relation, instance, or rule | source table, row, file, or multimodal asset |

**Typical use cases:** supply chain modeling, clinical concept extraction, financial compliance, legal document structuring — any domain where you need to turn heterogeneous data into structured knowledge.

---

## Features

### Pipelines (v2)
- **Visual pipeline builder** — connector / storage / transform / output nodes on a canvas, with per-node status and data preview
- **Three transform routes** — A: structured (CSV/Excel, schema inference + cleansing), B: semi-structured (JSON flatten / XML parse), C: unstructured (document → Markdown → LLM or rule-based structured extraction)
- **Connectors** — file upload, MySQL/PostgreSQL, MongoDB, REST API (with incremental sync)
- **Curated datasets** — quality scoring, human review (admin approval), versioning

### Ontology (v2)
- **Auto mapping engine** — dataset → entity type, column → property, FK → link type, with cardinality inference
- **Cross-dataset link inference** — exact FK matching, value normalization (`SUP-001` ↔ `SUP001`), alternate-key matching (e.g. document mentions of company names linking to Supplier entities), optional LLM-assisted semantic linking (`ENABLE_LLM_FK_DETECTION=1`)
- **Logic rule discovery** — confirmed rules are attached to the ontology revision and shown with their evidence
- **Ontology relation canvas** — interactive Cytoscape.js view with entity-type relations and a right-side inspector
- **Search** — search the selected entity's properties or the complete ontology by type, property, relation, rule, or source

### Quality Audit (ReAct Agent)
- **LLM-driven multi-step review** — an AI agent systematically checks ontology quality: isolated entities, broken references, missing relations, low-coverage entity types
- **Tool-calling architecture** — 8 built-in inspection tools (summary, coverage, ref-check, pattern inference) that the agent can chain together
- **Findings report** — severity-classified issues with actionable fix suggestions, persisted as audit tasks

### Platform
- **LLM extraction** — any OpenAI, Anthropic, or OpenAI-compatible model; defense-in-depth against fuzzy relation types
- **LiteLLM proxy** — optional LiteLLM integration for unified API-key management and cost tracking across multiple LLM providers
- **Prompt management** — versioned domain prompts with one-click template generation
- **Data management** — structured data browser with curated dataset detail panel, row-level editing, and review workflow
- **Export** — JSON, YAML, CSV, Turtle (RDF), HTML
- **Graceful degradation** — Neo4j / MinIO / ChromaDB / Redis are all optional; the system falls back to SQLite + local file storage + synchronous runs
- **Multi-language UI** — English / Chinese toggle
- **User management** — JWT auth, admin/editor roles; curated approval is admin-only

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Cytoscape.js |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Metadata DB | SQLite (dev) / PostgreSQL (prod) |
| Object storage | MinIO (optional, local-file fallback) |
| Graph DB | Neo4j (optional, SQLite fallback) |
| Vector DB | ChromaDB (optional) |
| Task queue | Celery + Redis (optional, synchronous fallback) |
| LLM clients | OpenAI SDK, Anthropic SDK |
| LLM proxy | LiteLLM (optional) |

---

## Architecture Guide

For a deep dive into the Ontology-as-a-Service architecture — including Object/Link/Function/Governance design patterns, multi-tenant isolation, clinical screening workflows, and production deployment checklists — see **[ONTOLOGY.md](./ONTOLOGY.md)** (2727 lines, in Chinese).

---

## Quick Start

### Option 1 — Docker Compose (local workbench)

```bash
git clone https://github.com/Joeysoda/nano-ontoprompt.git
cd nano-ontoprompt
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

This starts PostgreSQL, Redis, Neo4j, MinIO, ChromaDB, Celery worker/beat, LiteLLM, backend, and frontend. Install Ollama and pull `qwen3.5:0.8b` on the host for local quality audits. See [DEPLOYMENT_WORKBENCH_ZH.md](./DEPLOYMENT_WORKBENCH_ZH.md) for the complete setup, health checks, and recovery commands.

Open [http://127.0.0.1:15173/overview](http://127.0.0.1:15173/overview). Local mode does not show a login page; the backend is available at `http://127.0.0.1:18080`.

### Option 2 — Manual setup (minimal, no external services)

**Prerequisites:** Python 3.11+, Node.js 18+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                                  # or rely on auto create_all in dev
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Neo4j / MinIO / ChromaDB / Redis are optional — without them the app uses SQLite graph fallback, local file storage and synchronous pipeline runs.

---

## Usage (Pipeline Mapping path)

1. **Choose a data class** — *Data construction* offers regular C-MAPSS FD001, temporal FactoryNet CNC, and multimodal I-BADAS.
2. **Select the source and scope** — choose a prepared case or import a file/connection, then select tables, columns, time range, samples, and modalities.
3. **Configure processing** — choose `standard` or `private`, review the fields and samples that may be sent to the model, and start mapping.
4. **Confirm the mapping** — review entity types, properties, relations, and logic rules returned by the mapping task.
5. **Build and inspect** — open the ontology relation canvas, select an entity type for its inspector, browse real instances and rules, and review the local audit trace.

Legacy extraction and pipeline endpoints remain available for compatibility, but the current local demo is centered on the five-step ontology construction flow above.

---

## Project Structure

```
nano-ontoprompt/
├── backend/
│   ├── alembic/               # DB migrations (0001_full_baseline covers all tables)
│   ├── app/
│   │   ├── routers/           # v1 + v2 REST API endpoints
│   │   ├── models/            # SQLAlchemy ORM models (v1 + v2)
│   │   ├── services/
│   │   │   ├── connection/    # File / SQL / Mongo / REST connectors
│   │   │   └── v2/
│   │   │       ├── pipeline/  # Transform engine, routes A/B/C, steps
│   │   │       ├── mapping/   # Auto mapper, FK & alternate-key link inference
│   │   │       ├── graph/     # Neo4j service, Cypher validation, analytics
│   │   │       ├── curated/   # Quality scoring, review workflow
│   │   │       └── vector/    # ChromaDB service
│   │   └── tasks/             # Celery tasks (pipeline run, sync, extraction)
│   ├── scripts/               # Maintenance scripts (orphan data cleanup, migration)
│   └── tests/                 # 300+ pytest cases
├── frontend/
│   ├── scripts/                # One-off debug / demo / test scripts
│   └── src/
│       ├── pages/pipelines/    # Pipeline list + canvas builder
│       ├── pages/ontologies/   # Ontology relation canvas / entities / logic rules / audit
│       ├── pages/data-management/  # Regular / temporal / multimodal five-step builders
│       └── api/                # Axios clients (v1 + v2)
├── scripts/
│   └── data/                   # Data import & entity-linking scripts (SNOMED, supply chain)
├── docker-compose.yml          # Base service definitions
├── docker-compose.local.yml    # Local ports, single-user mode, Celery and LiteLLM
├── litellm_config.yaml         # LiteLLM proxy configuration
├── ONTOLOGY.md                 # Comprehensive architecture guide
└── test_data/                  # Sample datasets and E2E acceptance scripts
```

---

## Environment Variables

See `.env.example` for the full list. Key settings:

```env
ENVIRONMENT=development        # "production" enforces non-default secrets at startup
DATABASE_URL=sqlite:///./ontoprompt.db
SECRET_KEY=change-me
ENCRYPTION_KEY=                # Fernet key for encrypting stored API keys
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=admin123

# Optional services (graceful fallback when absent)
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
MINIO_ENDPOINT=localhost:9000
CHROMA_HOST=localhost

# Upload limits
MAX_UPLOAD_MB=200
ALLOWED_UPLOAD_EXTENSIONS=csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt

# Optional: LLM-assisted semantic FK detection (needs a configured model)
ENABLE_LLM_FK_DETECTION=0
```

---

## Troubleshooting

**Login fails with `AggregateError [ECONNREFUSED]` in the frontend container.**
Pull the latest code — the Vite proxy now targets `http://backend:8000` inside Docker via `VITE_API_PROXY_TARGET`. Then rebuild: `docker compose up -d --build frontend`.

**Cannot login with `admin / admin123` on an existing deployment.**
The admin user was seeded with the old default password. Reset it:

```bash
# Docker
docker compose exec backend python scripts/reset_admin_password.py

# Manual setup
cd backend && python scripts/reset_admin_password.py
```

Options: `--user <username>` (default `admin`), `--password <new_pwd>` (default `admin123`).

**LLM extraction OOM-killed (macOS / low-memory environments).**
Parallel extraction with multiple LLM calls can exhaust memory on machines with limited RAM. The code now defaults to serial extraction (`max_workers=1`). If you still hit issues, extract one domain at a time, or reduce the number of uploaded files per ontology.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Joeysoda/nano-ontoprompt&type=Date)](https://star-history.com/#Joeysoda/nano-ontoprompt&Date)

---

## License

MIT
