#!/usr/bin/env python3
"""Reset generated temporal runtime data before installing ICEWS.

Only ontologies that are temporal builds (plus the historical BTS id) and
their construction/evidence/schema records are selected.  Regular C-MAPSS
datasets, pipelines, model configurations, users and settings are outside the
delete set.  Always run ``--dry-run`` first.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / ".runtime-backups"
BTS_ONTOLOGY_ID = "35dd75f2-edc4-4761-aed9-3361c78df612"
DEBUG_GRAPHS = {"nano_healthcheck", "nano_test_label", "nano_nano_test_label"}


def _imports():
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    # Import model modules so SQLAlchemy knows all relationships before a
    # bulk delete is issued against a legacy database.
    from app.models import user, ontology, entity, relation, logic, action, file, prompt, model_config, extraction_task, rules_config, audit_task  # noqa: F401
    from app.models.v2 import dataset, pipeline, connection, curated, mapping, logic as v2_logic, action as v2_action, construction, multimodal  # noqa: F401
    from app.database import SessionLocal
    from app.models.ontology import OntologyProject
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.logic import LogicRule
    from app.models.action import Action
    from app.models.file import UploadedFile
    from app.models.extraction_task import ExtractionTask
    from app.models.v2.construction import ConstructionRun, EvidenceRef
    from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
    from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine
    from app.models.v2.action import OntologyActionType, OntologyActionRun
    from app.models.v2.multimodal import ExtractedFragment
    from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
    from app.services.storage_service import get_storage_service
    from app.services.v2.graph.falkordb_service import FalkorDBService, graph_name_for_ontology
    return locals()


def build_plan(db: Any) -> dict[str, Any]:
    m = _imports()
    OntologyProject, ConstructionRun = m["OntologyProject"], m["ConstructionRun"]
    ontologies = db.query(OntologyProject).all()
    temporal_ids = {run.ontology_id for run in db.query(ConstructionRun).filter(ConstructionRun.mode == "temporal").all()}
    temporal_ids.update(o.id for o in ontologies if (o.build_mode or "").startswith("temporal"))
    temporal_ids.add(BTS_ONTOLOGY_ID)
    present = {o.id for o in ontologies}
    temporal_ids.intersection_update(present)
    run_ids = [run.id for run in db.query(ConstructionRun).filter(ConstructionRun.mode == "temporal").all()]
    evidence_count = db.query(m["EvidenceRef"]).filter(m["EvidenceRef"].ontology_id.in_(temporal_ids)).count() if temporal_ids else 0
    schema_entities = db.query(m["Entity"]).filter(m["Entity"].ontology_id.in_(temporal_ids)).count() if temporal_ids else 0
    schema_relations = db.query(m["Relation"]).filter(m["Relation"].ontology_id.in_(temporal_ids)).count() if temporal_ids else 0
    return {
        "scope": "temporal runtime only",
        "keep": {"regular_ontology_excluded": True, "regular_datasets_excluded": True, "repository_bts_assets": True},
        "delete": {
            "ontology_ids": sorted(temporal_ids), "construction_run_ids": sorted(run_ids),
            "graph_names": sorted({m["graph_name_for_ontology"](x) for x in temporal_ids} | DEBUG_GRAPHS),
        },
        "counts": {
            "temporal_ontologies": len(temporal_ids), "temporal_runs": len(run_ids),
            "evidence_refs": evidence_count, "schema_entities": schema_entities,
            "schema_relations": schema_relations,
            "all_ontologies": len(ontologies),
            "all_datasets": db.query(m["Dataset"]).count(),
        },
    }


def _backup_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_ROOT / f"temporal-reset-{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def backup_database(path: Path) -> None:
    """Create a logical dump, using the Compose Postgres container if needed."""
    url = os.environ.get("DATABASE_URL", "")
    dump = path / "postgres.sql"
    if not url.startswith("postgresql"):
        (path / "postgres-backup-note.txt").write_text("DATABASE_URL is not PostgreSQL; no logical dump was required.\n", encoding="utf-8")
        return
    try:
        host_pg_dump = subprocess.run(["pg_dump", "--version"], capture_output=True).returncode == 0
    except OSError:
        host_pg_dump = False
    if host_pg_dump:
        result = subprocess.run(["pg_dump", url, "--file", str(dump)], capture_output=True, text=True)
        if result.returncode == 0:
            return
        (path / "postgres-dump-error.txt").write_text(result.stderr or "pg_dump failed", encoding="utf-8")
    parsed = urlparse(url)
    user = unquote(parsed.username or "postgres")
    database = unquote(parsed.path.lstrip("/") or "postgres")
    try:
        names_result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    except OSError:
        names_result = None
    names = [name.strip() for name in (names_result.stdout if names_result else "").splitlines() if name.strip()]
    configured = os.environ.get("POSTGRES_CONTAINER", "")
    candidates = [configured] if configured else []
    candidates += [name for name in names if name not in candidates and (name.endswith("-db-1") or "postgres" in name.lower())]
    for container in candidates:
        try:
            inspect = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container], capture_output=True, text=True)
        except OSError:
            continue
        if inspect.returncode != 0 or inspect.stdout.strip().lower() != "true":
            continue
        with dump.open("wb") as stream:
            try:
                result = subprocess.run(["docker", "exec", container, "pg_dump", "-U", user, "-d", database], stdout=stream, stderr=subprocess.PIPE)
            except OSError:
                continue
        if result.returncode == 0 and dump.stat().st_size > 0:
            return
    (path / "postgres-backup-note.txt").write_text("pg_dump was unavailable both on the host and in a running Compose container.\n", encoding="utf-8")


def backup_storage(path: Path) -> None:
    m = _imports()
    try:
        storage = m["get_storage_service"]()
        items: list[str] = []
        for bucket in ("raw-datasets", "curated-datasets", "media", "intermediate"):
            try: items.extend(storage.list_prefix(bucket, ""))
            except Exception: pass
        (path / "minio-objects.json").write_text(json.dumps(sorted(items), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        (path / "minio-objects-error.txt").write_text(str(exc), encoding="utf-8")


def backup_graphs(path: Path, graph_names: list[str]) -> None:
    m = _imports()
    service = m["FalkorDBService"]()
    manifest: dict[str, Any] = {}
    if service.available and service._db:
        for name in graph_names:
            try:
                graph = service._db.select_graph(name)
                nodes = []
                for row in graph.query("MATCH (n) RETURN n").result_set:
                    node = row[0]
                    nodes.append({"id": dict(getattr(node, "properties", {}) or {}).get("_instance_id"), "properties": dict(getattr(node, "properties", {}) or {})})
                edges = []
                for row in graph.query("MATCH (a)-[r]->(b) RETURN a._instance_id, b._instance_id, type(r), r").result_set:
                    edges.append({"source": row[0], "target": row[1], "type": row[2], "properties": dict(getattr(row[3], "properties", {}) or {})})
                manifest[name] = {"nodes": nodes, "edges": edges}
            except Exception as exc:
                manifest[name] = {"error": str(exc)}
    else:
        manifest["_status"] = "FalkorDB unavailable; no graph was deleted"
    (path / "falkordb-graphs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def delete_runtime_objects(db: Any, ontology_ids: list[str], run_ids: list[str]) -> list[str]:
    """Delete only objects owned by the temporal runs being removed.

    Raw files belonging to regular C-MAPSS datasets are intentionally not
    touched.  A temporal run's artifact, source version and evidence URI are
    safe to remove after the backup has been written.
    """
    if not run_ids:
        return []
    m = _imports()
    run_rows = db.query(m["ConstructionRun"]).filter(m["ConstructionRun"].id.in_(run_ids)).all()
    dataset_ids = {run.dataset_id for run in run_rows if run.dataset_id}
    uris: set[str] = {run.artifact_uri for run in run_rows if run.artifact_uri and str(run.artifact_uri).startswith("s3://")}
    for evidence in db.query(m["EvidenceRef"]).filter(m["EvidenceRef"].construction_run_id.in_(run_ids)).all():
        if evidence.source_file and str(evidence.source_file).startswith("s3://"):
            uris.add(str(evidence.source_file))
    for version in db.query(m["DatasetVersion"]).filter(m["DatasetVersion"].dataset_id.in_(dataset_ids)).all() if dataset_ids else []:
        if version.storage_uri:
            uris.add(version.storage_uri)
        for media in db.query(m["MediaItem"]).filter(m["MediaItem"].dataset_version_id == version.id).all():
            if media.storage_uri:
                uris.add(media.storage_uri)
    storage = m["get_storage_service"]()
    deleted: list[str] = []
    for uri in sorted(uris):
        try:
            storage.delete_object(uri)
            deleted.append(uri)
        except Exception:
            # A missing object should not make the relational reset unsafe;
            # the backup manifest still records the intended target.
            continue
    return deleted


def apply_plan(db: Any, plan: dict[str, Any], backup: Path) -> dict[str, Any]:
    m = _imports()
    ontology_ids = plan["delete"]["ontology_ids"]
    run_ids = plan["delete"]["construction_run_ids"]
    service = m["FalkorDBService"]()
    deleted_graphs: list[str] = []
    for ontology_id in ontology_ids:
        if service.delete_graph(ontology_id): deleted_graphs.append(m["graph_name_for_ontology"](ontology_id))
    if service.available and service._db:
        for name in DEBUG_GRAPHS:
            try:
                service._db.connection.execute_command("GRAPH.DELETE", name)
                deleted_graphs.append(name)
            except Exception: pass

    deleted_objects = delete_runtime_objects(db, ontology_ids, run_ids)

    # Explicit child deletion works on both PostgreSQL and the legacy SQLite
    # database used by tests, regardless of FK cascade configuration.
    if run_ids:
        db.query(m["EvidenceRef"]).filter(m["EvidenceRef"].construction_run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(m["ConstructionRun"]).filter(m["ConstructionRun"].id.in_(run_ids)).delete(synchronize_session=False)
    if ontology_ids:
        for model in (m["OntologyMapping"], m["OntologyLinkMapping"], m["OntologyLogicRule"], m["OntologyStateMachine"], m["OntologyActionType"], m["OntologyActionRun"], m["EvidenceRef"]):
            db.query(model).filter(model.ontology_id.in_(ontology_ids)).delete(synchronize_session=False)
        for model in (m["Relation"], m["Entity"], m["LogicRule"], m["Action"], m["UploadedFile"], m["ExtractionTask"]):
            db.query(model).filter(model.ontology_id.in_(ontology_ids)).delete(synchronize_session=False)
        db.query(m["OntologyProject"]).filter(m["OntologyProject"].id.in_(ontology_ids)).delete(synchronize_session=False)
    db.commit()
    (backup / "apply-result.json").write_text(json.dumps({"deleted_graphs": deleted_graphs, "deleted_objects": deleted_objects, "deleted_ontology_ids": ontology_ids, "deleted_run_ids": run_ids}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"deleted_graphs": deleted_graphs, "deleted_objects": deleted_objects, "deleted_ontology_ids": ontology_ids, "deleted_run_ids": run_ids}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", help="must equal ICEWS-TEMPORAL for --apply")
    args = parser.parse_args()
    if args.dry_run == args.apply: parser.error("choose exactly one of --dry-run or --apply")
    if args.apply and args.confirm != "ICEWS-TEMPORAL": parser.error("--apply requires --confirm ICEWS-TEMPORAL")
    m = _imports()
    db = m["SessionLocal"]()
    try:
        plan = build_plan(db)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if args.dry_run: return 0
        backup = _backup_dir()
        (backup / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        backup_database(backup); backup_storage(backup); backup_graphs(backup, plan["delete"]["graph_names"])
        result = apply_plan(db, plan, backup)
        print(json.dumps({"backup_dir": str(backup), "result": result}, ensure_ascii=False, indent=2))
        return 0
    finally: db.close()


if __name__ == "__main__": raise SystemExit(main())
