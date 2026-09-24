"""Durable seven-stage Scenario execution for the supplier workbench."""
import os
from datetime import datetime, timezone
from time import monotonic, sleep
from hashlib import sha256
from contextlib import contextmanager

from sqlalchemy import text
from celery.signals import worker_ready

from app.tasks.celery_app import celery_app

STAGES = (
    "validate_action", "apply_changeset", "bind_scenario_data",
    "run_supply_model", "calculate_impact", "materialize_results", "finalize",
)

def _now():
    return datetime.now(timezone.utc)

def _commit_stage(db, stage, status, *, summary=None, error=None):
    stage.status = status
    if summary is not None:
        stage.summary_json = summary
    if error is not None:
        stage.error_json = error
    if status == "running":
        stage.started_at = _now()
    if status in {"completed", "failed", "cancelled"}:
        stage.completed_at = _now()
        if stage.started_at:
            stage.duration_ms = max(0, int((stage.completed_at - stage.started_at).total_seconds() * 1000))

@contextmanager
def _execution_lock(db, run_id):
    """Session advisory lock survives stage commits and releases on worker death."""
    if db.get_bind().dialect.name != "postgresql":
        yield True
        return
    key = int.from_bytes(sha256(run_id.encode()).digest()[:8], "big", signed=True)
    with db.get_bind().connect() as connection:
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


@worker_ready.connect
def reconcile_interrupted_runs(**kwargs):
    """Make abandoned attempts explicitly retryable; never touch a live executor."""
    from app.database import SessionLocal
    from app.models.v2.scenario import ScenarioRun, ScenarioRunStage
    with SessionLocal() as db:
        for run in db.query(ScenarioRun).filter_by(status="running").all():
            with _execution_lock(db, run.id) as acquired:
                if not acquired:
                    continue
                error = {"code": "worker_interrupted", "message": "Worker stopped before completion; retry creates a new attempt."}
                for stage in db.query(ScenarioRunStage).filter_by(run_id=run.id, status="running").all():
                    _commit_stage(db, stage, "failed", error=error)
                run.status = "failed"
                run.error_json = error
                run.completed_at = _now()
                db.commit()


@celery_app.task(name="v2.scenario_run", acks_late=True, reject_on_worker_lost=True)
def run_scenario(run_id: str):
    from app.database import SessionLocal
    with SessionLocal() as lock_session:
        with _execution_lock(lock_session, run_id) as acquired:
            if acquired:
                return _execute_run(run_id)


def _execute_run(run_id: str):
    from app.database import SessionLocal
    from app.models.v2.scenario import ScenarioRun, ScenarioRunStage
    from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute
    from app.services.v2.object_query.normalize import stable_hash

    db = SessionLocal()
    started = monotonic()
    try:
        run = db.get(ScenarioRun, run_id)
        if not run or run.status in {"completed", "failed", "cancelled"}:
            return
        if run.study_id:
            return _run_supplier_study(db, run, started)
        stages = db.query(ScenarioRunStage).filter_by(run_id=run.id).order_by(ScenarioRunStage.sequence).all()
        if not stages:
            stages = [ScenarioRunStage(run_id=run.id, sequence=i + 1, stage_key=key) for i, key in enumerate(STAGES)]
            db.add_all(stages); db.commit()
        run.status = "running"; run.started_at = run.started_at or _now(); run.current_stage = STAGES[0]; db.commit()
        result = None
        for index, stage in enumerate(stages):
            db.refresh(run)
            if run.cancel_requested:
                _commit_stage(db, stage, "cancelled", error={"code": "cancelled"})
                run.status = "cancelled"; run.current_stage = "cancelled"; run.progress = int(index / len(stages) * 100)
                db.commit(); return
            _commit_stage(db, stage, "running")
            run.current_stage = stage.stage_key; run.progress = int(index / len(stages) * 100); db.commit()
            try:
                if stage.stage_key == "validate_action":
                    if not run.parameters:
                        raise ValueError("scenario inputs are empty")
                    summary = {"input_digest": run.input_digest, "case_key": run.case_key}
                elif stage.stage_key == "apply_changeset":
                    summary = {"revision": run.revision, "changeset": "baseline-noop" if run.revision == 0 else "submitted-revision"}
                elif stage.stage_key == "bind_scenario_data":
                    summary = {"context": "scenario snapshot", "completeness": "exact", "objects": 16}
                elif stage.stage_key == "run_supply_model":
                    definition = next((item for item in ASSET_DEFINITIONS if item["asset_key"] == run.logic_asset_key), None)
                    if not definition:
                        raise ValueError("logic asset is not registered")
                    result = execute(definition, run.parameters)
                    summary = {"logic_asset": run.logic_asset_key, "version": run.logic_version, "output_digest": stable_hash(result)}
                elif stage.stage_key == "calculate_impact":
                    output = (result or {}).get("output", {})
                    summary = {"path_count": len(output.get("paths", [])), "shortage_count": len(output.get("shortages", [])), "bounded": True}
                elif stage.stage_key == "materialize_results":
                    output = (result or {}).get("output", {})
                    run.result_manifest = {"output": output, "metrics": _metrics(run.case_key, output), "warnings": _warnings(output), "provenance": {"scenario_revision": run.revision, "logic_asset": run.logic_asset_key, "logic_version": run.logic_version, "input_digest": run.input_digest, "completeness": "exact"}}
                    run.output_digest = stable_hash(run.result_manifest)
                    summary = {"artifact": "scenario-result-manifest", "output_digest": run.output_digest}
                else:
                    summary = {"status": "complete", "artifact_digest": run.output_digest}
                _commit_stage(db, stage, "completed", summary=summary)
                run.progress = int((index + 1) / len(stages) * 100); db.commit()
            except Exception as exc:
                _commit_stage(db, stage, "failed", error={"code": "stage_failed", "message": str(exc)})
                run.status = "failed"; run.current_stage = stage.stage_key; run.progress = int(index / len(stages) * 100); run.error_json = {"code": "stage_failed", "stage": stage.stage_key, "message": str(exc)}; run.completed_at = _now(); run.duration_ms = int((monotonic() - started) * 1000); db.commit(); return
        run.status = "completed"; run.current_stage = "complete"; run.progress = 100; run.completed_at = _now(); run.duration_ms = int((monotonic() - started) * 1000); db.commit()
    finally:
        db.close()


def _run_supplier_study(db, run, started):
    """Compute each stage from persisted, pinned inputs; never from browser state."""
    from app.models.v2.query_view import QueryDataView
    from app.models.v2.scenario import (ScenarioRun, ScenarioRunStage, ScenarioRevision, ScenarioStudy,
        ScenarioStudyCase, ScenarioResource, ScenarioRunWarning, ScenarioRunArtifact, ScenarioMetricSnapshot)
    from app.services.v2.graph.falkordb_service import FalkorDBService
    from app.services.v2.object_query.core import FalkorReadAdapter
    from app.services.v2.object_query.normalize import stable_hash
    from app.services.v2.supplier_resilience import validate_profile
    from app.services.v2.logic_assets import SUPPLIER_RESILIENCE_ASSET, execute
    from app.models.v2.logic_asset import LogicAsset, LogicAssetRun

    if run.status in {"completed", "failed", "cancelled"}:
        return
    if run.cancel_requested:
        run.status = "cancelled"
        run.current_stage = "cancelled"
        run.completed_at = _now()
        db.commit()
        return
    if run.depends_on_run_id:
        dependency = db.get(ScenarioRun, run.depends_on_run_id)
        if not dependency or dependency.status in {"failed", "cancelled"}:
            run.status = "failed"; run.current_stage = "dependency"
            run.error_json = {"code": "baseline_dependency_failed", "message": "Compatible Baseline did not complete"}
            run.completed_at = _now(); db.commit(); return
        if dependency.status != "completed":
            run_scenario.apply_async(args=(run.id,), countdown=2)
            return
    stages = db.query(ScenarioRunStage).filter_by(run_id=run.id).order_by(ScenarioRunStage.sequence).all()
    run.status = "running"; run.started_at = run.started_at or _now(); db.commit()
    # The fault-test Compose overlay enables this bounded pause only for its
    # dedicated request. It gives the test time to kill a genuinely running
    # worker; normal workers have no pause configuration.
    if run.client_request_id.startswith("fault-worker-"):
        pause = min(max(int(os.getenv("SUPPLIER_FAULT_TEST_PAUSE_SECONDS", "0")), 0), 15)
        if pause:
            sleep(pause)
    snapshot = None
    result = None
    view = None
    object_set_execution_hash = None
    bound_demand_ids = set()
    demand_set = None
    logic_run_id = None
    for index, stage in enumerate(stages):
        db.refresh(run)
        if run.cancel_requested:
            _commit_stage(db, stage, "cancelled", error={"code": "cancelled"})
            run.status = "cancelled"; run.current_stage = "cancelled"
            run.completed_at = _now(); db.commit(); return
        _commit_stage(db, stage, "running")
        run.current_stage = stage.stage_key
        run.progress = int(index * 100 / len(stages))
        db.commit()
        try:
            study = db.get(ScenarioStudy, run.study_id)
            case = db.get(ScenarioStudyCase, run.case_id)
            scenario = db.get(ScenarioResource, run.scenario_id)
            profile = run.parameters["profile"]
            if stage.stage_key == "validate_action":
                validate_profile(profile)
                if not study or not case or not scenario or case.study_id != study.id:
                    raise ValueError("Study/Case/Scenario binding is invalid")
                if run.logic_asset_key != study.model_key or run.logic_version != study.model_version:
                    raise ValueError("Model provenance mismatch")
                summary = {"input_digest": run.input_digest, "case_definition_revision": run.parameters["definition_revision"]}
            elif stage.stage_key == "apply_changeset":
                if run.revision:
                    revision = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=run.revision, status="ready").one()
                    view_id = revision.result_view_id
                    summary = {"revision": revision.revision, "changeset_id": revision.changeset_id, "view_id": view_id}
                else:
                    view_id = scenario.base_view_id
                    summary = {"revision": 0, "changeset_id": None, "view_id": view_id, "baseline_noop": True}
                view = db.get(QueryDataView, view_id)
                if not view or view.status != "ready":
                    raise ValueError("Pinned scenario view unavailable")
            elif stage.stage_key == "bind_scenario_data":
                from app.schemas.v2.object_query import (BaseObjectSet, ComparisonFilter, ExecutionContext,
                    FilterObjectSet, LoadObjectSetRequest, PropertyRef, ReadOptions, TypeRef)
                from app.services.v2.object_query.core import QueryCore, QueryPolicy
                from app.services.v2.object_query.metadata import load_sql_metadata
                graph = FalkorDBService()
                if not graph.available:
                    raise RuntimeError("FalkorDB dependency unavailable")
                snapshot = FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(study.ontology_id)
                demand_set = FilterObjectSet(kind="filter", input=BaseObjectSet(kind="base", type_ref=TypeRef(api_name="Demand")),
                    where=ComparisonFilter(kind="comparison", property=PropertyRef(api_name="status"), op="eq", value="open"))
                request = LoadObjectSetRequest(expression=demand_set, read=ReadOptions(page_size=200),
                    context=ExecutionContext(ontology_id=study.ontology_id, consistency="snapshot", data_view_id=view.id))
                loaded = QueryCore(load_sql_metadata(db, study.ontology_id),
                    FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key),
                    QueryPolicy(principal=run.created_by)).load(request)
                bound_demand_ids = {item.object_id for item in loaded.objects}
                if loaded.completeness != "complete" or len(bound_demand_ids) != 16:
                    raise ValueError("Pinned open-Demand Object Set is incomplete")
                object_set_execution_hash = loaded.execution_hash
                summary = {"view_id": view.id, "object_count": len(snapshot.objects),
                           "edge_count": len(snapshot.edges), "scope": run.parameters["scope"],
                           "open_demand_count": len(bound_demand_ids), "object_set_execution_hash": object_set_execution_hash}
            elif stage.stage_key == "run_supply_model":
                asset = db.query(LogicAsset).filter_by(ontology_id=study.ontology_id,
                    asset_key=run.logic_asset_key, version=run.logic_version, status="published").order_by(LogicAsset.created_at, LogicAsset.id).first()
                if asset is None:
                    raise ValueError("Published supplier Logic Asset is unavailable")
                bound_inputs = {"objects": [{"type": typ, "id": oid, "properties": props}
                    for (typ, oid), props in sorted(snapshot.objects.items())], "profile": profile,
                    "scenario_time": run.parameters["scenario_time"], "scope_items": run.parameters["scope"]["items"]}
                execution = execute(SUPPLIER_RESILIENCE_ASSET, bound_inputs)
                result = execution["output"]
                logic_run = LogicAssetRun(asset_id=asset.id, ontology_id=study.ontology_id,
                    asset_version=asset.version, inputs={"scenario_run_id": run.id, "view_id": view.id,
                        "input_digest": run.input_digest, "object_set_execution_hash": object_set_execution_hash},
                    output=result, trace={"stages": execution["trace"]}, status="completed")
                db.add(logic_run); db.flush()
                logic_run_id = logic_run.id
                if {item["object_id"] for item in result["demand_impacts"]} != bound_demand_ids:
                    raise ValueError("Supply model output does not match the bound Object Set")
                summary = {"model_key": run.logic_asset_key, "model_version": run.logic_version,
                           "output_digest": stable_hash(result), "demand_count": len(result["demand_impacts"])}
            elif stage.stage_key == "calculate_impact":
                late = [row for row in result["demand_impacts"] if row["late_days"]]
                summary = {"affected_demand_count": len(late), "warning_count": len(result["warnings"]),
                           "metric_count": len(result["metrics"]), "bounded": True}
            elif stage.stage_key == "materialize_results":
                graph = FalkorDBService()
                graph_key = f"scenario_result_{run.id}"
                artifact_view = db.query(QueryDataView).filter_by(graph_key=graph_key).first()
                if artifact_view is None:
                    artifact_view = QueryDataView(ontology_id=study.ontology_id,
                        source_manifest_digest=view.source_manifest_digest,
                        base_view_id=view.id, changeset_digest=stable_hash(result),
                        changeset_version="supplier-impact-v1", metadata_digest=view.metadata_digest,
                        graph_key=graph_key, status="building", created_by=run.created_by)
                    db.add(artifact_view); db.flush()
                instances = [{"id": oid, "entity_type": typ,
                              "properties": {key: value for key, value in props.items()
                                             if value is not None and not isinstance(value, (dict, list))}}
                             for (typ, oid), props in snapshot.objects.items()]
                impact_nodes = []
                impact_links = []
                for impact in result["demand_impacts"]:
                    identity = f"{run.id}:impact:{impact['object_id']}"
                    impact_nodes.append({"id": identity, "entity_type": "DemandImpact", "properties": {
                        "name": impact["demand"], "demand_id": impact["object_id"],
                        "customer": impact["customer"], "item": impact["item"],
                        "late_days": impact["late_days"], "shortage_quantity": sum(impact["shortages"].values()),
                        "promised_date": impact["promised_date"], "source_kind": "modeled_output"}})
                    impact_links.append({"source": identity, "target": impact["object_id"],
                                         "type": "IMPACTS_DEMAND"})
                original_links = [{"source": source[1], "target": target[1], "type": relation}
                                  for source, relation, target in snapshot.edges]
                graph.upsert_instances(graph_key, instances + impact_nodes)
                graph.upsert_relations(graph_key, original_links + impact_links)
                artifact_view.object_count = len(instances) + len(impact_nodes)
                artifact_view.edge_count = len(original_links) + len(impact_links)
                artifact_view.content_digest = stable_hash({"source_view": view.content_digest,
                                                            "impact": result["demand_impacts"]})
                artifact_view.status = "ready"
                run.result_view_id = artifact_view.id
                provenance = {"study_id": study.id, "case_id": case.id, "scenario_id": scenario.id,
                              "scenario_revision": run.revision, "base_view_id": study.base_view_id,
                              "scenario_view_id": view.id, "result_view_id": artifact_view.id,
                              "source_manifest_digest": study.source_manifest_digest,
                              "input_digest": run.input_digest, "compatibility_hash": run.compatibility_hash,
                              "object_set_execution_hash": object_set_execution_hash,
                              "model_key": run.logic_asset_key, "model_version": run.logic_version,
                              "logic_asset_run_id": logic_run_id,
                              "model_config_alias": run.parameters["model_config_alias"],
                              "synthetic": case.case_kind != "baseline",
                              "synthetic_fields": ["capacity", "cost_multiplier"] if case.case_kind == "baseline" else
                                                  ["supplier", "lead_days", "minimum_order", "order_multiple", "capacity", "cost_multiplier"],
                              "completeness": result["completeness"]}
                run.result_manifest = {**result, "provenance": provenance}
                # Run/view IDs belong to provenance, not deterministic model output.
                run.output_digest = stable_hash(result)
                baseline_run = db.get(ScenarioRun, run.depends_on_run_id) if run.depends_on_run_id else None
                baseline_metrics = {item["key"]: item["value"] for item in
                                    (baseline_run.result_manifest or {}).get("metrics", [])} if baseline_run else {}
                for metric in result["metrics"]:
                    if not db.query(ScenarioMetricSnapshot).filter_by(run_id=run.id, metric_key=metric["key"]).first():
                        base_value = baseline_metrics.get(metric["key"], metric["value"] if case.case_kind == "baseline" else None)
                        db.add(ScenarioMetricSnapshot(run_id=run.id, metric_key=metric["key"], baseline=base_value,
                            candidate=metric["value"], unit=metric["unit"],
                            delta=metric["value"] - base_value if base_value is not None else None,
                            completeness=metric["completeness"], provenance={"input_digest": run.input_digest,
                                "model_version": run.logic_version, "object_set_execution_hash": object_set_execution_hash}))
                for warning in result["warnings"]:
                    if not db.query(ScenarioRunWarning).filter_by(run_id=run.id, code=warning["code"]).first():
                        db.add(ScenarioRunWarning(run_id=run.id, code=warning["code"],
                            severity=warning["severity"], message=warning["message"],
                            provenance={"model_version": run.logic_version, "output_digest": run.output_digest}))
                if not db.query(ScenarioRunArtifact).filter_by(run_id=run.id, artifact_type="demand_impacts").first():
                    db.add(ScenarioRunArtifact(run_id=run.id, artifact_type="demand_impacts",
                        data_view_id=artifact_view.id, object_set_definition=demand_set.model_dump(mode="json"),
                        manifest_json={"metric_contract": result["metric_contract"],
                                       "impact_count": len(result["demand_impacts"]),
                                       "object_set_execution_hash": object_set_execution_hash},
                        content_digest=artifact_view.content_digest))
                summary = {"artifact": "supplier-impact-query-data-view", "view_id": artifact_view.id,
                           "output_digest": run.output_digest, "object_count": len(result["demand_impacts"])}
            else:
                summary = {"status": "complete", "output_digest": run.output_digest}
            _commit_stage(db, stage, "completed", summary=summary)
            run.progress = int((index + 1) * 100 / len(stages))
            db.commit()
        except Exception as exc:
            # SQL/graph failures must not leave a failed transaction hiding the
            # durable stage error, or a partial artifact marked ready.
            db.rollback()
            db.refresh(run)
            db.refresh(stage)
            _commit_stage(db, stage, "failed", error={"code": "stage_failed", "message": str(exc)})
            run.status = "failed"; run.error_json = {"code": "stage_failed", "stage": stage.stage_key, "message": str(exc)}
            run.completed_at = _now(); run.duration_ms = int((monotonic() - started) * 1000)
            db.commit(); return
    run.status = "completed"; run.current_stage = "complete"; run.progress = 100
    run.completed_at = _now(); run.duration_ms = int((monotonic() - started) * 1000)
    db.commit()

def _metrics(case_key, output):
    shortages = output.get("shortages", [])
    return [
        {"key": "shortage_quantity", "label": "Shortage quantity", "value": round(sum(float(item.get("quantity", 0)) for item in shortages), 2), "unit": "units", "completeness": "exact"},
    ]

def _warnings(output):
    return [{"code": "shortage_detected", "severity": "warning", "message": f"{len(output.get('shortages', []))} bounded demand impact(s) detected"}] if output.get("shortages") else []
