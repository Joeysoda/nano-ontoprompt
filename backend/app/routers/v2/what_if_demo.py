"""Idempotent prepared supplier study over the pinned frePPLe fixture."""
from fastapi import APIRouter, Depends, HTTPException
import json
from pathlib import Path

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioResource, ScenarioStudy, ScenarioStudyCase
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.object_query.data_views import as_response, build_live_view
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.scenarios import ScenarioService
from app.schemas.v2.scenario import CreateScenarioRequest
from app.services.v2.object_query.normalize import stable_hash

router = APIRouter()

DEMO_CASES = (("baseline", "Baseline", "baseline"), ("supplier_b", "Supplier B", "candidate"), ("supplier_c", "Supplier C", "candidate"))


def supplier_manifest():
    candidates = (Path("/state/whatif_synthetic_manifest.json"),
                  Path(__file__).resolve().parents[4] / "data/frepple_demo/whatif_synthetic_manifest.json")
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise RuntimeError("Supplier study manifest is unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_demo_ontology(db, user):
    query = db.query(OntologyProject).join(ConstructionRun, ConstructionRun.ontology_id == OntologyProject.id).filter(ConstructionRun.id.like("%:import"))
    if user.role != "admin":
        query = query.filter(OntologyProject.created_by == user.id)
    for ontology in query.order_by(OntologyProject.id).all():
        run = db.get(ConstructionRun, ontology.id + ":import")
        if run and (run.config or {}).get("adapter") == "frepple_fixture_v1":
            return ontology
    return None


def _ensure_base_view(db, ontology, user, graph):
    run = db.get(ConstructionRun, ontology.id + ":import")
    report = run.config.get("report", {}) if run else {}
    digest = report.get("sha256", "frepple-demo-missing-digest")
    existing = db.query(QueryDataView).filter_by(ontology_id=ontology.id, source_manifest_digest=digest, status="ready").order_by(QueryDataView.created_at.asc()).first()
    if existing:
        try:
            snapshot = FalkorReadAdapter(graph._graph(existing.graph_key), graph_ontology_id=existing.graph_key).read(ontology.id)
            if len(snapshot.objects) != existing.object_count:
                raise ValueError("Pinned graph projection is incomplete")
        except Exception:
            # PostgreSQL keeps the reviewed fixture report even when a local
            # FalkorDB container was restarted without its data mount.
            nodes, edges = report.get("nodes", []), report.get("edges", [])
            if not nodes or not edges:
                raise RuntimeError("Pinned fixture report cannot restore FalkorDB projection")
            for graph_key in (ontology.id, existing.graph_key):
                graph.upsert_instances(graph_key, [{"id": node["id"], "entity_type": node["entity_type"],
                    "properties": {key: value for key, value in node["properties"].items()
                                   if value is not None and not isinstance(value, (dict, list))}} for node in nodes])
                graph.upsert_relations(graph_key, edges)
    if existing:
        return existing
    metadata = load_sql_metadata(db, ontology.id)
    return build_live_view(db, graph, metadata, ontology.id, user.id, digest, 86400)


@router.post("/demo/bootstrap")
def bootstrap_demo(db=Depends(get_db), user=Depends(get_current_user)):
    ontology = _find_demo_ontology(db, user)
    if not ontology:
        raise HTTPException(409, detail={"code": "frepple_demo_not_imported", "message": "The pinned frePPLe demo fixture is not available"})
    graph = FalkorDBService()
    if not graph.available:
        raise HTTPException(503, detail={"code": "graph_unavailable", "message": "FalkorDB is unavailable"})
    try:
        view = _ensure_base_view(db, ontology, user, graph)
        manifest = supplier_manifest()
        from app.models.v2.logic_asset import LogicAsset
        from app.services.v2.logic_assets import SUPPLIER_RESILIENCE_ASSET
        definition = SUPPLIER_RESILIENCE_ASSET
        asset = db.query(LogicAsset).filter_by(ontology_id=ontology.id,
            asset_key=definition["asset_key"], version=definition["version"]).first()
        if asset is None:
            db.add(LogicAsset(ontology_id=ontology.id, **definition,
                config={"alias": manifest["model"]["config_alias"], "scenario_only": True}))
            db.flush()
        digest = stable_hash(manifest)
        study = db.query(ScenarioStudy).filter_by(ontology_id=ontology.id, source_manifest_digest=digest).first()
        if not study:
            study = ScenarioStudy(ontology_id=ontology.id, owner_id=user.id, name="Wood Supply Resilience",
                description="Supplier replacement on the pinned frePPLe manufacturing fixture",
                base_view_id=view.id, source_manifest_digest=digest,
                scenario_time=manifest["scenario_time"], scope_definition=manifest["scope"],
                scope_hash=stable_hash(manifest["scope"]), model_key=manifest["model"]["key"],
                model_version=manifest["model"]["version"], model_config_alias=manifest["model"]["config_alias"],
                parameter_projection=["lead_days", "minimum_order", "order_multiple", "capacity", "cost_multiplier"])
            db.add(study); db.flush()
        scenarios = []
        svc = ScenarioService(db, ontology.id, user)
        for order, (key, name, kind) in enumerate(DEMO_CASES):
            case_row = db.query(ScenarioStudyCase).filter_by(study_id=study.id, case_key=key).first()
            row = db.get(ScenarioResource, case_row.scenario_id) if case_row else None
            if not row:
                row = svc.create(CreateScenarioRequest(name=name, description=f"{name} wood supply profile", base_view_id=view.id, protected_demo=True))
            profile = manifest["profiles"][key]
            if not case_row:
                case_row = ScenarioStudyCase(study_id=study.id, scenario_id=row.id, case_key=key,
                    display_name=name, case_kind=kind, sort_order=order,
                    action_key=None if kind == "baseline" else "replace_wood_supplier_v1",
                    submitted_parameters=profile, parameters_hash=stable_hash(profile), synthetic_manifest_hash=digest)
                db.add(case_row); db.flush()
            scenarios.append({"scenario": {"id": row.id, "name": row.name, "description": row.description, "status": row.status,
                                             "base_view_id": row.base_view_id, "head_revision": row.head_revision},
                              "case_id": case_row.id, "case_key": key, "asset_key": study.model_key,
                              "parameters": case_row.submitted_parameters})
        db.commit()
        return {"ontology_id": ontology.id, "ontology_name": ontology.name, "study_id": study.id,
                "study_name": study.name, "base_view": as_response(view),
                "source": {"kind": "frepple_official_fixture", "synthetic_extensions": True,
                           "note": "Scenario parameters marked synthetic are demo inputs, not production observations."},
                "cases": scenarios}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, detail={"code": "demo_bootstrap_failed", "message": str(exc)}) from exc
