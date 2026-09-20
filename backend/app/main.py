"""
OntoPrompt API v2

架构：FastAPI + PostgreSQL + Neo4j + ChromaDB + MinIO + Celery/Redis
v2 新增：Pipelines 全链路（Connection→Dataset→Transform→Curated→Mapping）
v1 兼容：/api/v1/* 路由全部保留

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
import logging
from app.database import engine, Base, SessionLocal
from app.config import settings

logger = logging.getLogger(__name__)
from app.routers import auth, users, overview, ontologies, files, prompts, models, entities, logic, actions, extraction, graph, settings as settings_router, export, audit
from app.routers.v2 import connections as connections_v2
from app.routers.v2 import datasets as datasets_v2
from app.routers.v2 import pipelines as pipelines_v2
from app.routers.v2 import graph as graph_v2
from app.routers.v2 import search as search_v2
from app.routers.v2 import curated as curated_v2
from app.routers.v2 import mappings as mappings_v2
from app.routers.v2 import incremental as incremental_v2
from app.routers.v2 import logic_actions as logic_actions_v2
from app.routers.v2 import construction_runs as construction_runs_v2
from app.routers.v2 import benchmarks as benchmarks_v2
from app.routers.v2 import multimodal as multimodal_v2
from app.routers.v2 import dashboard as dashboard_v2
from app.routers.v2 import revisions as revisions_v2
from app.routers.v2 import audits as audits_v2
from app.routers.v2 import construction_drafts as construction_drafts_v2
from app.routers.v2 import temporal as temporal_v2
from app.routers.v2 import model_routes as model_routes_v2
from app.routers.v2 import dynamic_ontology as dynamic_ontology_v2
from app.routers.v2 import what_if as what_if_v2
from app.routers.v2 import temporal_replays as temporal_replays_v2
from app.routers.v2 import temporal_streams as temporal_streams_v2
from app.routers.v2 import dynamic_data as dynamic_data_v2

def _run_schema_migration():
    """统一 schema 迁移入口。

    生产环境通过 Alembic 管理迁移；开发环境若库未纳入 Alembic 版本管理
    （如由 create_all 建起的旧库），则回退到 create_all 兜底并 stamp 到最新版本。
    """
    import os
    from alembic import command
    from alembic.config import Config as AlembicConfig

    alembic_ini = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
    cfg = AlembicConfig(alembic_ini)
    if os.environ.get("DATABASE_URL"):
        cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

    try:
        # 已存在表但未纳入 alembic 版本管理时，stamp 到基线再升级
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
        has_alembic_version = "alembic_version" in existing_tables
        if existing_tables and not has_alembic_version:
            command.stamp(cfg, "0001_full_baseline")
        command.upgrade(cfg, "head")
    except Exception:
        # 开发环境兜底：alembic 失败时用 create_all 保证表结构就位
        logger.warning("alembic upgrade failed; falling back to Base.metadata.create_all", exc_info=True)
        Base.metadata.create_all(bind=engine)


def _seed_db():
    from app.services.auth_service import seed_admin
    from app.models.rules_config import RulesConfig
    import uuid

    db = SessionLocal()
    try:
        # Import all models to ensure tables are created
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, audit_task
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config
        from app.models.v2 import dataset as v2_dataset, pipeline as v2_pipeline, connection as v2_connection  # noqa: F401
        from app.models.ontology_revision import OntologyRevision  # noqa: F401
        from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine  # noqa: F401
        from app.models.v2.action import OntologyActionType, OntologyActionRun  # noqa: F401
        from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit  # noqa: F401
        from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping  # noqa: F401
        from app.models.v2.construction import ConstructionRun, EvidenceRef  # noqa: F401
        from app.models.v2.temporal_profile import TemporalDatasetProfile  # noqa: F401
        from app.models.v2.multimodal import ExtractedFragment  # noqa: F401
        from app.models.v2.multimodal_install import MultimodalInstallTask  # noqa: F401
        from app.models.v2.construction_draft import ConstructionDraft  # noqa: F401
        from app.models.v2.workbench_task import MappingTask, DataImportTask, ModelInvocation  # noqa: F401
        from app.models.v2.dynamic_ontology import OntologyChange, WhatIfScenario, WhatIfRun  # noqa: F401
        from app.models.v2.temporal_replay import (  # noqa: F401
            DataModelSnapshot,
            TemporalFact,
            TemporalReplay,
            TemporalReplayBatch,
            TemporalStreamEvent,
        )
        _run_schema_migration()

        seed_admin(db)

        # Opt-in MiniMax M3 bootstrap.  The key is read from the process
        # environment, verified against the provider model list, then stored
        # encrypted in the model registry; no secret is returned or logged.
        try:
            from app.services.minimax_bootstrap import bootstrap_minimax_model
            bootstrap_minimax_model(db)
        except Exception:
            logger.warning("MiniMax bootstrap skipped; configure it from Models when needed", exc_info=True)
        try:
            from app.services.local_model_bootstrap import bootstrap_local_model_slot
            bootstrap_local_model_slot(db)
        except Exception:
            logger.warning("Local Ollama model slot bootstrap skipped", exc_info=True)
        try:
            from app.services.v2.datasets.supply_chain_installer import ensure_cmapss_fd001_demo
            ensure_cmapss_fd001_demo(db)
        except Exception:
            logger.warning("Builtin supply-chain source bootstrap skipped", exc_info=True)
        # Older ontologies predate immutable revisions.  Create a revision-1
        # snapshot once per project after migrations; this never overwrites a
        # saved graph or removes historical metadata.
        try:
            from app.models.ontology import OntologyProject
            from app.models.ontology_revision import OntologyRevision
            from app.models.entity import Entity
            from app.services.v2.revision_service import create_revision
            published_status_changed = False
            for project in db.query(OntologyProject).all():
                if not db.query(OntologyRevision.id).filter(OntologyRevision.ontology_id == project.id).first():
                    create_revision(db, project.id)
                # Historical successful builds used the default ``draft``
                # label forever.  Preserve empty/failed drafts, but show an
                # actually materialised ontology as created without changing
                # its graph, revision, evidence, or ownership.
                if project.status == "draft" and db.query(Entity.id).filter(Entity.ontology_id == project.id).first():
                    project.status = "created"
                    published_status_changed = True
            if published_status_changed:
                db.commit()
        except Exception:
            logger.warning("Ontology revision backfill skipped", exc_info=True)

        # 重启时清理遗留的 running 任务 — daemon 线程被杀后 task 会永久卡在 85%
        from app.models.extraction_task import ExtractionTask
        stale = db.query(ExtractionTask).filter(ExtractionTask.status == "running").all()
        for t in stale:
            t.status = "failed"
            t.error  = "服务重启，任务中断。请重新触发提取。"
        if stale:
            db.commit()

        # Long-running workbench tasks are resumable.  When the API process is
        # replaced, a task that was marked ``running`` no longer has a worker
        # behind it.  Put it back in the durable queue and publish one Celery
        # message after the transaction commits; the installers/importers are
        # idempotent, so a retried message cannot create duplicate samples.
        if str(__import__("os").environ.get("CELERY_ENABLED", "")).lower() in {"1", "true", "yes"}:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
            def is_stale(item):
                timestamp = item.updated_at
                if timestamp is None:
                    return True
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                return timestamp < cutoff

            resumable_install = [item for item in db.query(MultimodalInstallTask).filter(MultimodalInstallTask.status == "running").all() if is_stale(item)]
            resumable_mapping = [item for item in db.query(MappingTask).filter(MappingTask.status == "running").all() if is_stale(item)]
            resumable_import = [item for item in db.query(DataImportTask).filter(DataImportTask.status == "running").all() if is_stale(item)]
            resumable_what_if = [item for item in db.query(WhatIfRun).filter(WhatIfRun.status == "running").all() if is_stale(item)]
            for item in resumable_install:
                item.status = "queued"
                item.updated_at = datetime.now(timezone.utc)
            for item in resumable_mapping:
                item.status = "queued"
                item.started_at = None
                item.completed_at = None
                item.updated_at = datetime.now(timezone.utc)
            for item in resumable_import:
                item.status = "queued"
                item.updated_at = datetime.now(timezone.utc)
            for item in resumable_what_if:
                item.status = "queued"
                item.stage = "prepare_baseline"
                item.progress = 0
                item.started_at = None
                item.completed_at = None
                item.error = None
                item.updated_at = datetime.now(timezone.utc)
            if resumable_install or resumable_mapping or resumable_import or resumable_what_if:
                db.commit()
                try:
                    from app.tasks.v2.workbench import run_ibadas_install_task, run_mapping_task
                    from app.tasks.v2.connection_sync import run_data_import_task
                    from app.tasks.v2.workbench import run_what_if_task
                    for item in resumable_install:
                        run_ibadas_install_task.delay(item.id)
                    for item in resumable_mapping:
                        run_mapping_task.delay(item.id)
                    for item in resumable_import:
                        run_data_import_task.delay(item.id)
                    for item in resumable_what_if:
                        run_what_if_task.delay(item.id)
                except Exception:
                    logger.warning("Resumable workbench tasks were queued but could not be published", exc_info=True)

        # A process restart can leave an event-at-a-time stream marked as
        # running even though its worker thread/Celery task has disappeared.
        # Requeue only stale active streams; the committed event watermark and
        # idempotent event keys let the next worker continue exactly where it
        # stopped.  This is deliberately best-effort so startup never fails
        # because a broker is unavailable.
        stream_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        def stream_is_stale(item):
            timestamp = item.updated_at
            if timestamp is None:
                return True
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return timestamp < stream_cutoff

        resumable_streams = [
            item for item in db.query(TemporalReplay).filter(
                TemporalReplay.status.in_(["running", "pausing"]),
            ).all() if stream_is_stale(item)
        ]
        for item in resumable_streams:
            item.status = "queued"
            item.pause_requested = False
            item.step_requested = False
            item.cancel_requested = False
            item.error = None
            item.updated_at = datetime.now(timezone.utc)
        if resumable_streams:
            db.commit()
            try:
                from app.services.v2.temporal_stream_service import dispatch_stream
                for item in resumable_streams:
                    dispatch_stream(item.id)
            except Exception:
                logger.warning("Resumable temporal streams were requeued but could not be dispatched", exc_info=True)

        # Seed confidence rules
        if db.query(RulesConfig).count() == 0:
            rules = [
                ("confidence_entity_min", "0.5", "实体最低置信度", "Entity min confidence"),
                ("confidence_logic_min", "0.6", "逻辑规则最低置信度", "Logic rule min confidence"),
                ("confidence_action_min", "0.6", "动作最低置信度", "Action min confidence"),
                ("confidence_relation_min", "0.5", "关系最低置信度", "Relation min confidence"),
                ("confidence_high_threshold", "0.9", "高置信度阈值", "High confidence threshold"),
                ("confidence_medium_threshold", "0.7", "中置信度阈值", "Medium confidence threshold"),
                ("confidence_low_threshold", "0.5", "低置信度阈值", "Low confidence threshold"),
                ("confidence_display_dashed_below", "0.7", "低于此值显示虚线边", "Show dashed edge below threshold"),
            ]
            for key, val, label_cn, label_en in rules:
                db.add(RulesConfig(id=str(uuid.uuid4()), rule_key=key, rule_value=val,
                                   rule_label_cn=label_cn, rule_label_en=label_en))
            db.commit()

        # Seed / update builtin prompts (upsert by name)
        from app.models.prompt import Prompt
        from app.models.user import User
        from app.routers.prompts import BUILTIN_PROMPTS
        admin = db.query(User).filter(User.role == "admin").first()
        if admin:
            for p in BUILTIN_PROMPTS:
                existing = db.query(Prompt).filter(Prompt.name == p["name"]).first()
                if existing:
                    existing.content = p["content"]
                    existing.domain = p["domain"]
                else:
                    db.add(Prompt(id=str(uuid.uuid4()), name=p["name"], domain=p["domain"],
                                  content=p["content"], version="v1.0", created_by=admin.id))
            db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_db()
    # 初始化 Neo4j 索引（后台执行，不阻塞启动）
    try:
        from app.services.v2.graph.index_setup import setup_indexes
        setup_indexes()
    except Exception:
        logger.warning("Neo4j index setup skipped (unavailable); startup continues", exc_info=True)
    yield

app = FastAPI(title="OntoPrompt API", version="0.1.0", lifespan=lifespan)

# 注册限流器 - 保护 Auth 等敏感端点
from app.limiter import limiter
from slowapi.middleware import SlowAPIMiddleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(overview.router, prefix="/api/v1/overview", tags=["overview"])
app.include_router(ontologies.router, prefix="/api/v1/ontologies", tags=["ontologies"])
app.include_router(files.router, prefix="/api/v1/ontologies/{ontology_id}/files", tags=["files"])
app.include_router(entities.router, prefix="/api/v1/ontologies/{ontology_id}/entities", tags=["entities"])
app.include_router(logic.router, prefix="/api/v1/ontologies/{ontology_id}/logic", tags=["logic"])
app.include_router(actions.router, prefix="/api/v1/ontologies/{ontology_id}/actions", tags=["actions"])
app.include_router(extraction.router, prefix="/api/v1/ontologies/{ontology_id}/execute", tags=["extraction"])
app.include_router(graph.router, prefix="/api/v1/ontologies/{ontology_id}/graph", tags=["graph"])
app.include_router(export.router, prefix="/api/v1/ontologies/{ontology_id}/export", tags=["export"])
app.include_router(audit.router, prefix="/api/v1/ontologies/{ontology_id}/audit", tags=["audit"])
app.include_router(prompts.router, prefix="/api/v1/prompts", tags=["prompts"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(connections_v2.router, prefix="/api/v2/connections", tags=["v2-connections"])
app.include_router(connections_v2.data_imports_router, prefix="/api/v2", tags=["v2-data-imports"])
app.include_router(datasets_v2.router, prefix="/api/v2/datasets", tags=["v2-datasets"])
app.include_router(datasets_v2.data_sources_router, prefix="/api/v2", tags=["v2-data-sources"])
app.include_router(pipelines_v2.router, prefix="/api/v2/pipelines", tags=["v2-pipelines"])
app.include_router(graph_v2.router, prefix="/api/v2/ontologies", tags=["v2-graph"])
app.include_router(search_v2.router, prefix="/api/v2/ontologies", tags=["v2-search"])
app.include_router(curated_v2.router, prefix="/api/v2/curated", tags=["v2-curated"])
app.include_router(mappings_v2.router, prefix="/api/v2/ontologies", tags=["v2-mappings"])
app.include_router(incremental_v2.router, prefix="/api/v2/incremental", tags=["v2-incremental"])
app.include_router(logic_actions_v2.router, prefix="/api/v2/ontologies", tags=["v2-logic-actions"])
app.include_router(construction_runs_v2.router, prefix="/api/v2/ontologies", tags=["v2-construction-runs"])
app.include_router(construction_runs_v2.construction_root_router, prefix="/api/v2", tags=["v2-construction-runs"])
app.include_router(construction_runs_v2.assertions_router, prefix="/api/v2", tags=["v2-provenance"])
app.include_router(benchmarks_v2.router, prefix="/api/v2", tags=["v2-benchmarks"])
app.include_router(multimodal_v2.router, prefix="/api/v2", tags=["v2-multimodal"])
app.include_router(dashboard_v2.router, prefix="/api/v2/dashboard", tags=["v2-dashboard"])
app.include_router(revisions_v2.router, prefix="/api/v2/ontologies", tags=["v2-revisions"])
app.include_router(audits_v2.router, prefix="/api/v2/audits", tags=["v2-audits"])
app.include_router(construction_drafts_v2.router, prefix="/api/v2", tags=["v2-construction-drafts"])
app.include_router(construction_drafts_v2.mapping_tasks_router, prefix="/api/v2", tags=["v2-mapping-tasks"])
app.include_router(temporal_v2.router, prefix="/api/v2", tags=["v2-temporal"])
app.include_router(temporal_v2.ontology_router, prefix="/api/v2/ontologies", tags=["v2-temporal"])
app.include_router(temporal_replays_v2.router, prefix="/api/v2", tags=["v2-temporal-replays"])
app.include_router(temporal_streams_v2.router, prefix="/api/v2", tags=["v2-temporal-streams"])
app.include_router(dynamic_data_v2.router, prefix="/api/v2", tags=["v2-dynamic-data"])
app.include_router(model_routes_v2.router, prefix="/api/v2/model-routes", tags=["v2-model-routes"])
app.include_router(model_routes_v2.invocations_router, prefix="/api/v2", tags=["v2-model-invocations"])
app.include_router(dynamic_ontology_v2.router, prefix="/api/v2/ontologies", tags=["v2-ontology-editor"])
app.include_router(what_if_v2.router, prefix="/api/v2/ontologies", tags=["v2-what-if"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health(db: Session = Depends(get_db)):
    checks = {
        "status": "ok",
        "auth_mode": settings.auth_mode,
        "db": "unknown",
        "neo4j": "unknown",
        "falkordb": "unknown",
        "minio": "unknown",
        "chroma": "unknown",
    }

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Neo4j check
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        driver.close()
        checks["neo4j"] = "ok"
    except Exception:
        checks["neo4j"] = "unavailable"

    # FalkorDB is the authoritative backend for new instance/temporal builds.
    try:
        from app.services.v2.graph.falkordb_service import FalkorDBService
        checks["falkordb"] = "ok" if FalkorDBService().available else "unavailable"
    except Exception:
        checks["falkordb"] = "unavailable"

    # MinIO check
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        client.list_buckets()
        checks["minio"] = "ok"
    except Exception:
        checks["minio"] = "unavailable"

    # ChromaDB check
    try:
        import chromadb
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        client.heartbeat()
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "unavailable"

    return checks


@app.get("/api/v1/runtime")
def runtime_info():
    """Small public capability probe used by the desktop shell.

    It intentionally contains no credentials or model secrets. The frontend
    uses it to keep the local single-user switch explicit rather than
    inferring authentication from a failed request.
    """
    return {
        "data": {
            "auth_mode": settings.auth_mode,
            "local_single_user": settings.auth_mode == "local_single_user",
            "desktop_only": True,
        },
        "message": "ok",
    }
