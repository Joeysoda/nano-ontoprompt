#!/usr/bin/env python3
"""Safely reduce the local demo runtime to the two supported showcase chains.

The script is intentionally explicit: it only removes records/objects that
are not in the BTS + C-MAPSS allow-list.  Run ``--dry-run`` first, then create
an external PostgreSQL/MinIO/FalkorDB backup before ``--apply``.

Typical invocation (inside the backend container)::

    python scripts/cleanup_demo_runtime.py --dry-run
    python scripts/cleanup_demo_runtime.py --apply --confirm BTS-CMAPSS
"""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

from sqlalchemy import func

from app.database import SessionLocal
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.models.v2.multimodal import ExtractedFragment
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
from app.services.storage_service import get_storage_service
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.graph.falkordb_service import graph_name_for_ontology


KEEP_ONTOLOGIES = OrderedDict(
    [
        ("35dd75f2-edc4-4761-aed9-3361c78df612", "BTS Site B 时序本体"),
        ("b072efac-5905-4933-b912-b7dea6a40627", "C-MAPSS FD001 Demo"),
    ]
)
KEEP_RUN = "09abbc1d-d49b-4dc9-8233-2acab493e367"
KEEP_PIPELINES = {
    "6472355b-2521-4861-aaa4-77de6a7b7b4d",  # equipment
    "22a7b809-d58f-445c-ab36-2114a46bef0e",  # sensor readings
}
KEEP_DATASETS = {
    "17491922-e3c2-45f5-9b67-fa78387cbe59",  # raw equipment
    "df896eae-9a53-442b-a697-b0aff6b52af1",  # raw sensor readings
    "360d0bba-9372-4a77-9adf-02a55a1e3425",  # curated equipment
    "026e5241-1729-4c12-82e7-20453ea5d618",  # curated sensor readings
}
KEEP_CURATED = set(KEEP_DATASETS) - {
    "17491922-e3c2-45f5-9b67-fa78387cbe59",
    "df896eae-9a53-442b-a697-b0aff6b52af1",
}
DEBUG_GRAPHS = {"nano_healthcheck", "nano_test_label", "nano_nano_test_label"}


def _ids(items: Iterable[object]) -> list[str]:
    return [str(getattr(item, "id")) for item in items]


def build_plan(db) -> dict:
    ontologies = db.query(OntologyProject).all()
    runs = db.query(ConstructionRun).all()
    datasets = db.query(Dataset).all()
    curated = db.query(CuratedDataset).all()
    pipelines = db.query(Pipeline).all()
    present_ontology_ids = {x.id for x in ontologies}
    missing = [x for x in KEEP_ONTOLOGIES if x not in present_ontology_ids]
    if missing:
        raise RuntimeError(f"保护性检查失败：保留本体不存在 {missing}，拒绝执行清理")
    delete_ontology_ids = [x.id for x in ontologies if x.id not in KEEP_ONTOLOGIES]
    delete_run_ids = [x.id for x in runs if x.id != KEEP_RUN and (x.ontology_id not in KEEP_ONTOLOGIES or x.id != KEEP_RUN)]
    delete_dataset_ids = [x.id for x in datasets if x.id not in KEEP_DATASETS]
    delete_curated_ids = [x.id for x in curated if x.id not in KEEP_CURATED]
    delete_pipeline_ids = [x.id for x in pipelines if x.id not in KEEP_PIPELINES]
    return {
        "keep": {
            "ontologies": list(KEEP_ONTOLOGIES),
            "run": KEEP_RUN,
            "pipelines": sorted(KEEP_PIPELINES),
            "datasets": sorted(KEEP_DATASETS),
            "curated": sorted(KEEP_CURATED),
            "graphs": [graph_name_for_ontology(x) for x in KEEP_ONTOLOGIES],
        },
        "delete": {
            "ontologies": delete_ontology_ids,
            "construction_runs": delete_run_ids,
            "datasets": delete_dataset_ids,
            "curated_datasets": delete_curated_ids,
            "pipelines": delete_pipeline_ids,
            "debug_graphs": sorted(DEBUG_GRAPHS),
        },
        "counts": {
            "ontology": len(ontologies),
            "dataset": len(datasets),
            "curated": len(curated),
            "pipeline": len(pipelines),
            "construction_run": len(runs),
        },
    }


def _delete_objects(dataset_ids: Iterable[str]) -> int:
    storage = get_storage_service()
    deleted = 0
    # DatasetService stores raw versions under this deterministic prefix.
    for dataset_id in dataset_ids:
        try:
            uris = storage.list_prefix("raw-datasets", f"datasets/{dataset_id}/")
        except Exception:
            uris = []
        for uri in uris:
            try:
                storage.delete_object(uri)
                deleted += 1
            except Exception:
                # An already removed object should not make metadata cleanup
                # unsafe; the final manifest still records the DB deletion.
                pass
    return deleted


def apply_plan(db, plan: dict) -> dict:
    delete = plan["delete"]
    # Delete graph data before metadata.  Each graph is isolated by ontology id.
    graph_service = FalkorDBService()
    deleted_graphs: list[str] = []
    for ontology_id in delete["ontologies"]:
        if graph_service.delete_graph(ontology_id):
            deleted_graphs.append(graph_name_for_ontology(ontology_id))
    # Remove known debug graphs even though they are not backed by a project.
    if graph_service.available and graph_service._db:
        for name in DEBUG_GRAPHS:
            try:
                deleter = getattr(graph_service._db, "delete_graph", None)
                if callable(deleter):
                    deleter(name)
                else:
                    connection = getattr(graph_service._db, "connection", None)
                    if connection:
                        connection.execute_command("GRAPH.DELETE", name)
                deleted_graphs.append(name)
            except Exception:
                pass

    # Delete objects before rows so storage URIs remain available for cleanup.
    deleted_objects = _delete_objects(delete["datasets"])

    # Explicitly delete construction runs that are duplicates on a kept BTS
    # ontology.  Other runs disappear through ontology cascade below.
    db.query(EvidenceRef).filter(EvidenceRef.construction_run_id.in_(delete["construction_runs"])).delete(synchronize_session=False)
    db.query(ConstructionRun).filter(ConstructionRun.id.in_(delete["construction_runs"])).delete(synchronize_session=False)

    # Legacy curated rows have independent foreign keys to pipelines/mappings.
    obsolete_curated = [x for x in delete["curated_datasets"] if x not in KEEP_CURATED]
    # Mapping FKs predate the newer cascade rules, so remove mappings before
    # their curated datasets and before deleting obsolete ontologies.
    if delete["ontologies"]:
        db.query(OntologyMapping).filter(OntologyMapping.ontology_id.in_(delete["ontologies"])).delete(synchronize_session=False)
        db.query(OntologyLinkMapping).filter(OntologyLinkMapping.ontology_id.in_(delete["ontologies"])).delete(synchronize_session=False)
    if obsolete_curated:
        db.query(OntologyMapping).filter(OntologyMapping.curated_dataset_id.in_(obsolete_curated)).delete(synchronize_session=False)
        db.query(OntologyLinkMapping).filter(
            (OntologyLinkMapping.src_dataset_id.in_(obsolete_curated)) |
            (OntologyLinkMapping.tgt_dataset_id.in_(obsolete_curated))
        ).delete(synchronize_session=False)
    if obsolete_curated:
        review_ids = [x[0] for x in db.query(CuratedReview.id).filter(CuratedReview.curated_dataset_id.in_(obsolete_curated)).all()]
        if review_ids:
            db.query(CuratedRowEdit).filter(CuratedRowEdit.review_id.in_(review_ids)).delete(synchronize_session=False)
            db.query(CuratedReview).filter(CuratedReview.id.in_(review_ids)).delete(synchronize_session=False)
        db.query(CuratedDataset).filter(CuratedDataset.id.in_(obsolete_curated)).delete(synchronize_session=False)

    # Pipeline versions/runs have cascade from Pipeline, but explicit deletes
    # work with older databases where the FK cascade was not enabled.
    obsolete_pipelines = delete["pipelines"]
    if obsolete_pipelines:
        db.query(PipelineRun).filter(PipelineRun.pipeline_id.in_(obsolete_pipelines)).delete(synchronize_session=False)
        db.query(PipelineVersion).filter(PipelineVersion.pipeline_id.in_(obsolete_pipelines)).delete(synchronize_session=False)
        db.query(Pipeline).filter(Pipeline.id.in_(obsolete_pipelines)).delete(synchronize_session=False)

    # Delete ontology metadata after graph deletion; PostgreSQL cascades its
    # entities, mappings, evidence, actions, and uploaded files.
    if delete["ontologies"]:
        db.query(OntologyProject).filter(OntologyProject.id.in_(delete["ontologies"])).delete(synchronize_session=False)

    # Media/fragment rows are normally cascaded by DatasetVersion.  Explicitly
    # remove them for SQLite/legacy PostgreSQL schemas before dataset delete.
    obsolete_versions = [x[0] for x in db.query(DatasetVersion.id).filter(DatasetVersion.dataset_id.in_(delete["datasets"])).all()]
    if obsolete_versions:
        media_ids = [x[0] for x in db.query(MediaItem.id).filter(MediaItem.dataset_version_id.in_(obsolete_versions)).all()]
        if media_ids:
            db.query(ExtractedFragment).filter(ExtractedFragment.media_item_id.in_(media_ids)).delete(synchronize_session=False)
            db.query(MediaItem).filter(MediaItem.id.in_(media_ids)).delete(synchronize_session=False)
        db.query(DatasetVersion).filter(DatasetVersion.id.in_(obsolete_versions)).delete(synchronize_session=False)
    if delete["datasets"]:
        db.query(Dataset).filter(Dataset.id.in_(delete["datasets"])).delete(synchronize_session=False)
    db.commit()
    return {"deleted_graphs": deleted_graphs, "deleted_storage_objects": deleted_objects}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the deletion plan without changing data")
    parser.add_argument("--apply", action="store_true", help="apply the deletion plan")
    parser.add_argument("--confirm", help="must equal BTS-CMAPSS when applying")
    parser.add_argument("--manifest", type=Path, help="write the plan/result JSON outside the repository")
    args = parser.parse_args()
    if args.apply and args.confirm != "BTS-CMAPSS":
        parser.error("--apply requires --confirm BTS-CMAPSS")
    if not args.apply and not args.dry_run:
        parser.error("choose --dry-run or --apply")
    db = SessionLocal()
    try:
        plan = build_plan(db)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            if args.manifest:
                args.manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        result = apply_plan(db, plan)
        output = {"plan": plan, "result": result}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.manifest:
            args.manifest.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
