"""Celery task for the first-class temporal ontology construction flow."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.models.v2.dataset import DatasetVersion  # register FK target in worker metadata
from app.models.entity import Entity
from app.models.relation import Relation
from app.services.storage_service import get_storage_service
from app.services.v2.construction_service import update_run
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.temporal_service import (
    TemporalConfig,
    build_bts_instances,
    build_observation_instances,
    normalize_temporal_rows,
    summarize_temporal_rows,
)

try:
    from app.tasks.celery_app import celery_app
except Exception:  # pragma: no cover - import fallback for lightweight tests
    celery_app = None


def _read_rows(run: ConstructionRun) -> tuple[list[dict[str, Any]], str]:
    source = (run.config or {}).get("source") or "bts_site_b"
    if source == "bts_site_b":
        path = Path(__file__).resolve().parents[3] / "data" / "bts_demo" / "observations.csv"
        raw = path.read_bytes()
        return list(csv.DictReader(io.StringIO(raw.decode("utf-8")))), "BTS Site B observations.csv"
    dataset_id = run.dataset_id
    if not dataset_id:
        raise ValueError("temporal run requires dataset_id or source=bts_site_b")
    db = SessionLocal()
    try:
        version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_no.desc()).first()
        if not version or not version.storage_uri:
            raise ValueError("dataset has no readable version")
        raw = get_storage_service().get_object(version.storage_uri)
    finally:
        db.close()
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    if text.lstrip().startswith("["):
        data = json.loads(text)
        return (data if isinstance(data, list) else [data]), str(version.storage_uri)
    return list(csv.DictReader(io.StringIO(text))), str(version.storage_uri)


def _ensure_bts_schema(db, ontology_id: str) -> None:
    """Persist the six Brick-oriented classes used by the demo.

    GraphTab's schema view is backed by Nano's relational schema store while
    instance nodes live in FalkorDB.  Keeping this small, deterministic schema
    in SQLite means the user can switch between schema and instance views
    without the two views drifting apart.  The IDs are stable, so retries are
    idempotent even though the legacy tables do not have a composite unique
    constraint.
    """
    classes = [
        ("Building", "建筑", "brick:Building"),
        ("Zone", "区域", "brick:Zone"),
        ("Equipment", "设备", "brick:Equipment"),
        ("Point", "测点", "brick:Point"),
        ("Observation", "观测", "sosa:Observation"),
        ("AnomalyEvent", "异常事件", "sosa:Event"),
    ]
    entities: dict[str, Entity] = {}
    for english, chinese, canonical in classes:
        entity_id = f"schema:{ontology_id}:{english}"
        entity = db.get(Entity, entity_id)
        if entity is None:
            entity = Entity(
                id=entity_id,
                ontology_id=ontology_id,
                name_cn=chinese,
                name_en=english,
                name_abbr=english[:3].upper(),
                canonical_id=canonical,
                type="Class",
                description=f"BTS 时序演示中的 {english} 类",
                properties={"source": "BTS Brick metadata", "schema_kind": "brick"},
                confidence=1.0,
            )
            db.add(entity)
        entities[english] = entity
    db.flush()
    relations = [
        ("Building", "Zone", "HAS_ZONE"),
        ("Building", "Equipment", "HAS_EQUIPMENT"),
        ("Zone", "Equipment", "HAS_EQUIPMENT"),
        ("Equipment", "Point", "HAS_POINT"),
        ("Point", "Observation", "OBSERVED_BY"),
        ("Observation", "AnomalyEvent", "INDICATES_ANOMALY"),
    ]
    for source, target, relation_type in relations:
        exists = db.query(Relation).filter(
            Relation.ontology_id == ontology_id,
            Relation.source_entity == entities[source].id,
            Relation.target_entity == entities[target].id,
            Relation.type == relation_type,
        ).first()
        if exists is None:
            db.add(Relation(
                id=f"schema:{ontology_id}:{relation_type}:{source}:{target}",
                ontology_id=ontology_id,
                source_entity=entities[source].id,
                target_entity=entities[target].id,
                type=relation_type,
                properties={"source": "BTS Brick metadata", "schema_kind": "brick"},
                confidence=1.0,
            ))


def run_temporal_construction(run_id: str) -> dict[str, Any]:
    db = SessionLocal()
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id, ConstructionRun.mode == "temporal").first()
    if not run:
        db.close()
        raise ValueError(f"temporal construction run not found: {run_id}")
    try:
        update_run(db, run, status="running", progress={"stage": "读取时序数据", "completed": 0, "total": 0})
        rows, source_file = _read_rows(run)
        config = run.config or {}
        temporal = config.get("time") or {}
        normalized, issues = normalize_temporal_rows(rows, TemporalConfig(
            time_kind=temporal.get("time_kind", "instant"),
            sequence_column=temporal.get("sequence_column", "event_seq"),
            event_time_column=temporal.get("event_time_column", "event_time"),
            valid_from_column=temporal.get("valid_from_column"),
            valid_to_column=temporal.get("valid_to_column"),
            timezone=temporal.get("timezone", "UTC"),
        ))
        update_run(db, run, progress={"stage": "标准化时序", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        if config.get("adapter", "bts") == "bts":
            nodes, edges = build_bts_instances(normalized)
        else:
            nodes, edges = build_observation_instances(
                normalized,
                entity_id_column=config.get("entity_id_column", "unit_id"),
                entity_type=config.get("entity_type", "Equipment"),
                observation_type=config.get("observation_type", "Observation"),
                reading_id_prefix=config.get("reading_id_prefix", "reading"),
            )
        falkor = FalkorDBService()
        if not falkor.available:
            raise RuntimeError("FalkorDB unavailable")
        _ensure_bts_schema(db, run.ontology_id)
        update_run(db, run, progress={"stage": "写入 FalkorDB", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        node_count = falkor.upsert_instances(run.ontology_id, nodes)
        edge_count = falkor.upsert_relations(run.ontology_id, edges)
        update_run(db, run, progress={"stage": "保存来源证据", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        # Evidence is inserted with SQLAlchemy's bulk path; a row-level ORM
        # flush for a 20k-point series makes an otherwise successful demo
        # unnecessarily slow.
        evidence_rows = []
        for index, row in enumerate(normalized):
            evidence_text = str({k: row.get(k) for k in ("stream_id", "brick_class", "event_time", "value")})
            evidence_rows.append({
                "id": str(uuid.uuid4()),
                "construction_run_id": run.id,
                "ontology_id": run.ontology_id,
                "assertion_id": f"BTS:Observation:{row.get('stream_id')}:{row.get('event_time') or index}",
                "assertion_kind": "node",
                "source_dataset_id": run.dataset_id,
                "source_version": "bts-site-b-v1",
                "source_file": source_file,
                "source_row_id": str(row.get("source_row_index", index)),
                "extractor": "rule",
                "confidence": 1.0,
                "confidence_method": "deterministic_temporal_normalization",
                "evidence_text": evidence_text,
                "content_hash": hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
            })
        db.bulk_insert_mappings(EvidenceRef, evidence_rows)
        db.commit()
        metrics = {
            "rows_in": len(rows),
            "rows_normalized": len(normalized),
            "temporal_issues": len(issues),
            "nodes_upserted": node_count,
            "edges_upserted": edge_count,
            "source": source_file,
            "summary": summarize_temporal_rows(normalized),
            "ontology_classes": ["Building", "Zone", "Equipment", "Point", "Observation", "AnomalyEvent"],
            "relations": ["HAS_EQUIPMENT", "HAS_POINT", "LOCATED_IN", "OBSERVED_ON", "INSTANCE_OF", "INDICATES_ANOMALY", "VALID_DURING"],
        }
        update_run(db, run, status="completed", progress={"stage": "构建完成", "completed": len(normalized), "total": len(rows), "issues": len(issues)}, metrics=metrics)
        return {"run_id": run.id, "status": "completed", "metrics": metrics, "issues": issues[:100]}
    except Exception as exc:
        db.rollback()
        update_run(db, run, status="failed", error=str(exc), progress={"stage": "构建失败"})
        raise
    finally:
        db.close()


if celery_app is not None:
    run_temporal_construction_task = celery_app.task(name="ontoprompt.temporal_construction")(run_temporal_construction)
else:  # pragma: no cover
    run_temporal_construction_task = None
