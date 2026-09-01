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
from app.models.v2.dataset import Dataset, DatasetVersion  # register FK target in worker metadata
from app.models.entity import Entity
from app.models.relation import Relation
from app.services.storage_service import get_storage_service
from app.services.v2.construction_service import update_run
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.temporal_service import (
    TemporalConfig,
    build_bts_instances,
    build_factorynet_instances,
    build_observation_instances,
    normalize_temporal_rows,
    summarize_temporal_rows,
)
from app.services.v2.datasets.icews_adapter import (
    ICEWS_SOURCE_ID,
    build_icews_instances,
    icews_summary,
    normalize_icews_rows,
    parse_icews_tsv,
)

try:
    from app.tasks.celery_app import celery_app
except Exception:  # pragma: no cover - import fallback for lightweight tests
    celery_app = None


def _read_rows(run: ConstructionRun) -> tuple[list[dict[str, Any]], str]:
    source = (run.config or {}).get("source_id") or (run.config or {}).get("source") or "bts_site_b"
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
    if source in {ICEWS_SOURCE_ID, "icews"}:
        return parse_icews_tsv(raw), str(version.storage_uri)
    from app.routers.v2.temporal import parse_temporal_bytes
    return parse_temporal_bytes(raw), str(version.storage_uri)


def _source_manifest(db, dataset_id: str | None) -> dict[str, Any]:
    if not dataset_id:
        return {}
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    return dict(dataset.schema_json or {}) if dataset else {}


def _filter_icews_rows(rows: list[dict[str, Any]], filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Apply user-selected ICEWS filters before normalization and graph writes."""
    filters = filters or {}
    date_from = str(filters.get("date_from") or "")
    date_to = str(filters.get("date_to") or "")
    source_country = str(filters.get("source_country") or "").casefold()
    target_country = str(filters.get("target_country") or "").casefold()
    country = str(filters.get("country") or "").casefold()
    event_type = str(filters.get("event_type") or "").casefold()
    cameo = str(filters.get("cameo_code") or "").casefold()
    participant = str(filters.get("participant") or "").casefold()
    scenario = str(filters.get("scenario") or "").casefold()
    try:
        intensity_min = float(filters["intensity_min"]) if filters.get("intensity_min") not in (None, "") else None
    except (TypeError, ValueError):
        intensity_min = None
    try:
        intensity_max = float(filters["intensity_max"]) if filters.get("intensity_max") not in (None, "") else None
    except (TypeError, ValueError):
        intensity_max = None

    def keep(row: dict[str, Any]) -> bool:
        event_date = str(row.get("Event Date") or row.get("event_time") or "")
        if date_from and event_date < date_from:
            return False
        if date_to and event_date > date_to:
            return False
        src_country = str(row.get("Source Country") or row.get("source_country") or "").casefold()
        dst_country = str(row.get("Target Country") or row.get("target_country") or "").casefold()
        if scenario == "ru_ua":
            haystack = " ".join((src_country, dst_country, str(row.get("Country") or "").casefold()))
            if not ("ukraine" in haystack or "russian federation" in haystack or "russia" in haystack):
                return False
        elif scenario == "kr":
            haystack = " ".join((src_country, dst_country))
            if not ("korea" in haystack or "dprk" in haystack):
                return False
        if source_country and source_country not in src_country:
            return False
        if target_country and target_country not in dst_country:
            return False
        if country and country not in " ".join((src_country, dst_country, str(row.get("Country") or "").casefold())):
            return False
        if event_type and event_type not in str(row.get("Event Text") or row.get("event_type") or "").casefold():
            return False
        if cameo and cameo != str(row.get("CAMEO Code") or row.get("cameo_code") or "").casefold():
            return False
        if participant and participant not in " ".join((str(row.get("Source Name") or ""), str(row.get("Target Name") or ""))).casefold():
            return False
        intensity = row.get("Intensity") if row.get("Intensity") not in (None, "") else row.get("intensity")
        try:
            number = float(intensity) if intensity not in (None, "") else None
        except (TypeError, ValueError):
            number = None
        if intensity_min is not None and (number is None or number < intensity_min):
            return False
        if intensity_max is not None and (number is None or number > intensity_max):
            return False
        if scenario == "negative" and (number is None or number >= 0):
            return False
        return True

    selected = [row for row in rows if keep(row)]
    max_records = filters.get("max_records")
    try:
        if max_records not in (None, ""):
            selected = selected[:max(1, min(int(max_records), 100000))]
    except (TypeError, ValueError):
        pass
    return selected


def _filter_generic_rows(rows: list[dict[str, Any]], filters: dict[str, Any] | None, *, entity_column: str | None = None) -> list[dict[str, Any]]:
    """Apply typed generic filters and deterministic per-series sampling."""
    filters = filters or {}
    equals = filters.get("equals") or {}
    contains = filters.get("contains") or {}
    ranges = filters.get("ranges") or {}
    selected: list[dict[str, Any]] = []
    for row in rows:
        keep = True
        for column, expected in equals.items():
            values = expected if isinstance(expected, list) else [expected]
            if values and str(row.get(column, "")) not in {str(value) for value in values}:
                keep = False; break
        if not keep:
            continue
        for column, expected in contains.items():
            if expected and str(expected).casefold() not in str(row.get(column, "")).casefold():
                keep = False; break
        if not keep:
            continue
        for column, bounds in ranges.items():
            value = row.get(column)
            try:
                number = float(value)
                if bounds.get("min") not in (None, "") and number < float(bounds["min"]): keep = False
                if bounds.get("max") not in (None, "") and number > float(bounds["max"]): keep = False
            except (TypeError, ValueError):
                keep = False
            if not keep: break
        if keep:
            selected.append(row)
    max_records = filters.get("max_records")
    try:
        maximum = int(max_records) if max_records not in (None, "") else len(selected)
    except (TypeError, ValueError):
        maximum = len(selected)
    maximum = max(1, min(maximum, len(selected))) if selected else 0
    if maximum and len(selected) > maximum:
        # Deterministic time-uniform sampling by entity/series, preserving all
        # groups whenever the requested size permits it.
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in selected:
            key = str(row.get(entity_column or "_series_id") or "_series")
            groups.setdefault(key, []).append(row)
        chosen: list[dict[str, Any]] = []
        group_items = list(groups.items())
        for _, group in group_items:
            if len(chosen) >= maximum: break
            quota = max(1, int(round(maximum * len(group) / len(selected))))
            quota = min(quota, len(group), maximum - len(chosen))
            if quota == len(group):
                chosen.extend(group)
            else:
                step = len(group) / quota
                chosen.extend(group[min(len(group) - 1, int(index * step))] for index in range(quota))
        selected = chosen[:maximum]
    return selected


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


def _ensure_icews_schema(db, ontology_id: str) -> None:
    """Persist the compact ICEWS schema used by the temporal workbench."""
    # A failed migration from the old BTS preview may have left Brick schema
    # rows in an ontology that is now being reused for ICEWS.  Remove only
    # those deterministic legacy rows; user-authored classes remain intact.
    legacy_ids = [
        f"schema:{ontology_id}:{name}"
        for name in ("Building", "Zone", "Equipment", "Point", "Observation", "AnomalyEvent")
    ]
    db.query(Relation).filter(
        (Relation.source_entity.in_(legacy_ids)) | (Relation.target_entity.in_(legacy_ids))
    ).delete(synchronize_session=False)
    db.query(Entity).filter(Entity.id.in_(legacy_ids)).delete(synchronize_session=False)
    classes = [
        ("Actor", "参与者", "icews:Actor"),
        ("InteractionEvent", "互动事件", "icews:InteractionEvent"),
        ("EventCategory", "事件类别", "icews:EventCategory"),
        ("Country", "国家", "icews:Country"),
        ("Location", "地点", "icews:Location"),
    ]
    entities: dict[str, Entity] = {}
    for english, chinese, canonical in classes:
        entity_id = f"schema:{ontology_id}:{english}"
        entity = db.get(Entity, entity_id)
        if entity is None:
            entity = Entity(
                id=entity_id, ontology_id=ontology_id, name_cn=chinese,
                name_en=english, name_abbr=english[:3].upper(),
                canonical_id=canonical, type="Class",
                description=f"ICEWS 事件时序本体中的 {english} 类",
                properties={"source": "ICEWS official 2023 slice", "schema_kind": "icews"},
                confidence=1.0,
            )
            db.add(entity)
        entities[english] = entity
    db.flush()
    relation_defs = [
        ("Actor", "InteractionEvent", "INITIATED"),
        ("InteractionEvent", "Actor", "TARGETED"),
        ("InteractionEvent", "EventCategory", "CLASSIFIED_AS"),
        ("Actor", "Country", "ASSOCIATED_WITH"),
        ("InteractionEvent", "Location", "OCCURRED_IN"),
    ]
    for source, target, relation_type in relation_defs:
        exists = db.query(Relation).filter(
            Relation.ontology_id == ontology_id,
            Relation.source_entity == entities[source].id,
            Relation.target_entity == entities[target].id,
            Relation.type == relation_type,
        ).first()
        if exists is None:
            db.add(Relation(
                id=f"schema:{ontology_id}:{relation_type}:{source}:{target}",
                ontology_id=ontology_id, source_entity=entities[source].id,
                target_entity=entities[target].id, type=relation_type,
                properties={"source": "ICEWS official 2023 slice", "schema_kind": "icews"},
                confidence=1.0,
            ))


def _ensure_factorynet_schema(db, ontology_id: str) -> None:
    classes = [
        ("Machine", "机器"), ("Episode", "生产过程"), ("Observation", "观测"),
        ("ProcessPhase", "工序阶段"), ("ToolCondition", "刀具状态"),
        ("SensorChannel", "传感器通道"), ("InspectionResult", "检测结果"),
    ]
    entities: dict[str, Entity] = {}
    for english, chinese in classes:
        entity_id = f"schema:{ontology_id}:{english}"
        entity = db.get(Entity, entity_id)
        if entity is None:
            entity = Entity(id=entity_id, ontology_id=ontology_id, name_cn=chinese, name_en=english,
                            name_abbr=english[:3].upper(), canonical_id=f"factorynet:{english}", type="Class",
                            description=f"FactoryNet CNC 时序本体中的 {chinese}",
                            properties={"source": "FactoryNet CNC", "schema_kind": "factorynet"}, confidence=1.0)
            db.add(entity)
        entities[english] = entity
    db.flush()
    relations = [
        ("Machine", "Episode", "HAS_EPISODE"), ("Episode", "Observation", "HAS_OBSERVATION"),
        ("Observation", "Machine", "OBSERVED_ON"), ("Observation", "ProcessPhase", "IN_PHASE"),
        ("Observation", "Observation", "NEXT_OBSERVATION"), ("Observation", "ToolCondition", "HAS_TOOL_CONDITION"),
        ("Machine", "SensorChannel", "EXPOSES_CHANNEL"), ("Episode", "InspectionResult", "HAS_INSPECTION"),
    ]
    for source, target, relation_type in relations:
        rid = f"schema:{ontology_id}:{relation_type}:{source}:{target}"
        if db.get(Relation, rid) is None:
            db.add(Relation(id=rid, ontology_id=ontology_id, source_entity=entities[source].id,
                            target_entity=entities[target].id, type=relation_type,
                            properties={"source": "FactoryNet CNC", "schema_kind": "factorynet"}, confidence=1.0))


def run_temporal_construction(run_id: str) -> dict[str, Any]:
    db = SessionLocal()
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id, ConstructionRun.mode == "temporal").first()
    if not run:
        db.close()
        raise ValueError(f"temporal construction run not found: {run_id}")
    try:
        update_run(db, run, status="running", progress={"stage": "读取时序数据", "completed": 0, "total": 0})
        rows, source_file = _read_rows(run)
        source_row_count = len(rows)
        config = run.config or {}
        source = config.get("source_id") or config.get("source") or "bts_site_b"
        if source in {ICEWS_SOURCE_ID, "icews"}:
            rows = _filter_icews_rows(rows, config.get("filters"))
            try:
                rows = rows[:max(1, min(int(config.get("sample_limit") or len(rows)), len(rows)))]
            except (TypeError, ValueError):
                pass
            normalized, issues = normalize_icews_rows(rows)
        else:
            entity_column = config.get("entity_id_column") or (config.get("field_mapping") or {}).get("entity")
            rows = _filter_generic_rows(rows, config.get("filters"), entity_column=entity_column)
            normalized, issues = normalize_temporal_rows(rows, TemporalConfig(
                time_kind=(config.get("time") or {}).get("time_kind", "instant"),
                sequence_column=(config.get("time") or {}).get("sequence_column", "event_seq"),
                event_time_column=(config.get("time") or {}).get("event_time_column", "event_time"),
                valid_from_column=(config.get("time") or {}).get("valid_from_column"),
                valid_to_column=(config.get("time") or {}).get("valid_to_column"),
                timezone=(config.get("time") or {}).get("timezone", "UTC"),
            ))
        update_run(db, run, progress={"stage": "标准化时序", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        if source in {ICEWS_SOURCE_ID, "icews"}:
            nodes, edges = build_icews_instances(normalized)
        elif source == "factorynet_cnc" or config.get("adapter") == "factorynet":
            nodes, edges = build_factorynet_instances(normalized)
        elif config.get("adapter", "bts") == "bts":
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
        if source in {ICEWS_SOURCE_ID, "icews"}:
            _ensure_icews_schema(db, run.ontology_id)
        elif source == "factorynet_cnc" or config.get("adapter") == "factorynet":
            _ensure_factorynet_schema(db, run.ontology_id)
        else:
            _ensure_bts_schema(db, run.ontology_id)
        update_run(db, run, progress={"stage": "写入 FalkorDB", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        node_count = falkor.upsert_instances(run.ontology_id, nodes)
        edge_count = falkor.upsert_relations(run.ontology_id, edges)
        update_run(db, run, progress={"stage": "保存来源证据", "completed": len(normalized), "total": len(rows), "issues": len(issues)})
        # Evidence is inserted with SQLAlchemy's bulk path; a row-level ORM
        # flush for a 20k-point series makes an otherwise successful demo
        # unnecessarily slow.
        evidence_rows = []
        manifest = _source_manifest(db, run.dataset_id)
        source_version = str(manifest.get("file_id") or manifest.get("data_sha256") or "")
        for index, row in enumerate(normalized):
            if source in {ICEWS_SOURCE_ID, "icews"}:
                evidence_text = str({k: row.get(k) for k in ("Event ID", "Event Date", "Source Name", "Target Name", "Event Text", "CAMEO Code", "Story ID", "Publisher")})
                assertion_id = f"ICEWS:Event:{row.get('event_id')}"
                source_version_value = source_version or "icews-2023-demo"
            else:
                evidence_text = json.dumps({k: row.get(k) for k in row.keys() if not str(k).startswith("_")}, ensure_ascii=False, default=str)[:8000]
                prefix = "FactoryNet:Observation" if source == "factorynet_cnc" or config.get("adapter") == "factorynet" else "Temporal:Observation"
                assertion_id = f"{prefix}:{row.get('episode_id') or row.get(entity_id_column) or 'series'}:{row.get('_source_row_index', index)}"
                source_version_value = str(manifest.get("sha256") or manifest.get("source_id") or "temporal-upload")
            evidence_rows.append({
                "id": str(uuid.uuid4()),
                "construction_run_id": run.id,
                "ontology_id": run.ontology_id,
                "assertion_id": assertion_id,
                "assertion_kind": "node",
                "source_dataset_id": run.dataset_id,
                "source_version": source_version_value,
                "source_file": source_file,
                "source_row_id": str(row.get("source_row_index", index)),
                "extractor": "rule",
                "confidence": 1.0,
                "confidence_method": "deterministic_temporal_normalization",
                "evidence_text": evidence_text,
                "content_hash": hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
            })
        if source in {ICEWS_SOURCE_ID, "icews"}:
            # Every relationship assertion carries the event id and original
            # row index in FalkorDB.  Persisting the same anchor in EvidenceRef
            # lets the inspector resolve an edge back to the source line,
            # rather than only resolving InteractionEvent nodes.
            rows_by_event = {str(row.get("event_id")): row for row in normalized}
            for edge in edges:
                props = dict(edge.get("properties") or {})
                event_row = rows_by_event.get(str(props.get("event_id")))
                if event_row is None:
                    continue
                edge_text = str({
                    "event_id": props.get("event_id"), "relation": edge.get("type"),
                    "source": edge.get("source"), "target": edge.get("target"),
                    "event_time": event_row.get("event_time"),
                })
                evidence_rows.append({
                    "id": str(uuid.uuid4()), "construction_run_id": run.id,
                    "ontology_id": run.ontology_id, "assertion_id": edge.get("id"),
                    "assertion_kind": "edge", "source_dataset_id": run.dataset_id,
                    "source_version": source_version or "icews-2023-demo",
                    "source_file": source_file,
                    "source_row_id": str(event_row.get("source_row_index", 0)),
                    "extractor": "rule", "confidence": 1.0,
                    "confidence_method": "deterministic_temporal_normalization",
                    "evidence_text": edge_text,
                    "content_hash": hashlib.sha256(edge_text.encode("utf-8")).hexdigest(),
                })
        db.bulk_insert_mappings(EvidenceRef, evidence_rows)
        db.commit()
        if source in {ICEWS_SOURCE_ID, "icews"}:
            summary = icews_summary(normalized)
            ontology_classes = ["Actor", "InteractionEvent", "EventCategory", "Country", "Location"]
            relation_types = ["INITIATED", "TARGETED", "CLASSIFIED_AS", "ASSOCIATED_WITH", "OCCURRED_IN"]
        elif source == "factorynet_cnc" or config.get("adapter") == "factorynet":
            summary = {
                "rows": len(normalized),
                "episodes": len({str(row.get("episode_id")) for row in normalized if row.get("episode_id") not in (None, "")}),
                "machines": len({str(row.get("machine_type")) for row in normalized if row.get("machine_type") not in (None, "")}),
                "time_kind": "ordinal",
                "time_from": min((row.get("time_s") for row in normalized if row.get("time_s") is not None), default=None),
                "time_to": max((row.get("time_s") for row in normalized if row.get("time_s") is not None), default=None),
            }
            ontology_classes = ["Machine", "Episode", "Observation", "ProcessPhase", "ToolCondition", "SensorChannel", "InspectionResult"]
            relation_types = ["HAS_EPISODE", "HAS_OBSERVATION", "OBSERVED_ON", "IN_PHASE", "NEXT_OBSERVATION", "HAS_TOOL_CONDITION", "EXPOSES_CHANNEL", "HAS_INSPECTION"]
        else:
            summary = summarize_temporal_rows(normalized)
            ontology_classes = ["Building", "Zone", "Equipment", "Point", "Observation", "AnomalyEvent"]
            relation_types = ["HAS_EQUIPMENT", "HAS_POINT", "LOCATED_IN", "OBSERVED_ON", "INSTANCE_OF", "INDICATES_ANOMALY", "VALID_DURING"]
        metrics = {
            "rows_in": source_row_count,
            "rows_selected": len(rows),
            "rows_normalized": len(normalized),
            "temporal_issues": len(issues),
            "nodes_upserted": node_count,
            "edges_upserted": edge_count,
            "source": source_file,
            "summary": summary,
            "ontology_classes": ontology_classes,
            "relations": relation_types,
            "filters": config.get("filters") or {},
        }
        if not normalized:
            update_run(db, run, status="failed", progress={"stage": "没有有效时序记录", "completed": 0, "total": len(rows), "issues": len(issues)}, metrics=metrics, error="NO_VALID_TEMPORAL_ROWS: 没有一行通过时间语义校验")
            return {"run_id": run.id, "status": "failed", "metrics": metrics, "issues": issues[:100]}
        if node_count <= 0 or edge_count <= 0:
            update_run(db, run, status="failed", progress={"stage": "图谱没有有效关系", "completed": len(normalized), "total": len(rows), "issues": len(issues)}, metrics=metrics, error="NO_GRAPH_RELATIONS: 未生成节点或关系")
            return {"run_id": run.id, "status": "failed", "metrics": metrics, "issues": issues[:100]}
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
