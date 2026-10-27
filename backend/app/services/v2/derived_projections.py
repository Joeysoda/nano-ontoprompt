"""Version-pinned query -> row mapping -> Function projections.

The runtime contains no domain names.  Dataset adaptation is explicit, every
dependency is pinned, and each run records enough digests to explain freshness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.user import User
from app.models.v2.derived_projection import DerivedProjection, DerivedProjectionRun
from app.models.v2.logic_asset import LogicAsset, LogicAssetRun, LogicDerivedFact
from app.models.v2.ontology_query import OntologyQuery
from app.models.v2.query_view import QueryDataView
from app.schemas.v2.derived_projection import ProjectionSpec
from app.services.v2.logic_assets import execute
from app.services.v2.logic_contracts import normalize_contract
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.ontology_queries import pick, run as run_query
from app.services.v2.row_mapping import map_rows


class ProjectionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, path: str = "spec"):
        self.code, self.message, self.status, self.path = code, message, status, path
        super().__init__(message)


def _asset_dict(asset: LogicAsset) -> dict:
    return {name: getattr(asset, name) for name in (
        "asset_key", "name", "kind", "description", "implementation", "interface_key", "interface_version",
        "executor_type", "input_schema", "output_schema", "bindings", "binding_spec", "config", "version",
        "status", "deterministic", "side_effect",
    )}


def resolve(db, ontology_id: str, spec: ProjectionSpec):
    query = db.query(OntologyQuery).filter_by(ontology_id=ontology_id, api_name=spec.query_api_name).first()
    if query is None:
        raise ProjectionError("query_not_found", "Pinned Ontology Query does not exist", 404, "spec.query_api_name")
    try:
        query_version = pick(db, query, spec.query_version)
    except Exception as exc:
        raise ProjectionError("query_version_not_found", "Pinned Ontology Query version does not exist", 404, "spec.query_version") from exc
    asset = db.query(LogicAsset).filter_by(ontology_id=ontology_id, asset_key=spec.asset_key,
                                           version=spec.asset_version, status="published").first()
    if asset is None or asset.side_effect:
        raise ProjectionError("asset_unavailable", "Pinned side-effect-free Function release is unavailable", 404, "spec.asset_version")
    path = query_version.output_schema.get("path")
    columns = {item.get("name") for item in query_version.output_schema.get("columns", [])}
    for index, field in enumerate(spec.mapping.fields):
        if len(field.source) != 1 or field.source[0] not in columns:
            raise ProjectionError("mapping_source_unavailable", "Projection mappings must select one declared query column",
                                  path=f"spec.mapping.fields.{index}.source")
    input_schema = normalize_contract(_asset_dict(asset))["input_schema"]
    accepted = set(input_schema.get("properties", {}))
    required = set(input_schema.get("required", []))
    if unknown := set(spec.inputs) - accepted:
        raise ProjectionError("unknown_function_input", f"Function does not declare inputs {sorted(unknown)}", path="spec.inputs")
    if missing := required - set(spec.inputs):
        raise ProjectionError("missing_function_input", f"Projection does not bind required inputs {sorted(missing)}", path="spec.inputs")
    return query_version, asset, path


def assemble_inputs(spec: ProjectionSpec, mapped: list[dict]) -> dict:
    values = {}
    for target, binding in spec.inputs.items():
        if binding.kind == "literal":
            values[target] = binding.value
        elif binding.kind == "mapped_rows":
            values[target] = mapped
        else:
            values[target] = [row.get(binding.field) for row in mapped]
    return values


def run_projection(db, projection: DerivedProjection, owner: User, access) -> DerivedProjectionRun:
    spec = ProjectionSpec.model_validate(projection.spec)
    version, asset, path = resolve(db, projection.ontology_id, spec)
    dependency_digest = stable_hash({"query": version.definition_digest, "asset": {
        "key": asset.asset_key, "version": asset.version, "input": asset.input_schema, "output": asset.output_schema,
    }})
    try:
        query_result = run_query(db, projection.ontology_id, version, spec.query_params, spec.time_range,
                                 owner, access, spec.limit)
        rows = (query_result.get("outputs") or {}).get(path)
        if not isinstance(rows, list):
            raise ProjectionError("query_output_invalid", "Ontology Query did not return the declared row collection")
        adapted = map_rows(spec.mapping.model_dump(mode="json"), rows, max_rows=spec.max_rows)
        inputs = assemble_inputs(spec, adapted["rows"])
        result = execute(_asset_dict(asset), inputs)
        asset_run = LogicAssetRun(asset_id=asset.id, ontology_id=projection.ontology_id, asset_version=asset.version,
                                  inputs=inputs, output=result["output"], status="completed", error=None,
                                  trace=[{"stage": "projection", "projection_id": projection.id,
                                          "query_version": version.version, "data_view_id": query_result.get("data_view_id")},
                                         *result["trace"]], duration_ms=0)
        db.add(asset_run); db.flush()
        for key, value in result["output"].items():
            db.add(LogicDerivedFact(ontology_id=projection.ontology_id, run_id=asset_run.id, asset_id=asset.id,
                                    subject_id=projection.id, fact_key=key, value=value, source_path=f"output.{key}"))
        run = DerivedProjectionRun(projection_id=projection.id, logic_asset_run_id=asset_run.id,
                                   query_version=version.version, data_view_id=query_result.get("data_view_id"),
                                   spec_digest=projection.spec_digest, input_digest=stable_hash(inputs),
                                   dependency_digest=dependency_digest, status="completed", output=result["output"])
        db.add(run); db.flush()
        projection.last_successful_run_id = run.id
        projection.schedule_error = None
    except Exception as exc:
        run = DerivedProjectionRun(projection_id=projection.id, query_version=version.version,
                                   spec_digest=projection.spec_digest, dependency_digest=dependency_digest,
                                   status="failed", error={"code": getattr(exc, "code", "projection_failed"),
                                                            "message": getattr(exc, "message", "Projection execution failed")})
        projection.schedule_error = run.error
    projection.last_run_at = datetime.now(timezone.utc)
    db.add(run); db.commit(); db.refresh(run)
    return run


def due(db, at: datetime | None = None):
    at = at or datetime.now(timezone.utc)
    rows = db.query(DerivedProjection).filter(DerivedProjection.status == "active",
                                               DerivedProjection.interval_minutes.isnot(None)).all()
    return [row for row in rows if row.last_run_at is None or (row.last_run_at.replace(tzinfo=timezone.utc)
            if row.last_run_at.tzinfo is None else row.last_run_at) + timedelta(minutes=row.interval_minutes) <= at]


def freshness(db, projection: DerivedProjection) -> dict:
    if not projection.last_successful_run_id:
        return {"status": "never_run", "reason": "No successful run exists"}
    run = db.get(DerivedProjectionRun, projection.last_successful_run_id)
    if run is None or run.spec_digest != projection.spec_digest:
        return {"status": "stale", "reason": "Projection definition changed"}
    current = (db.query(QueryDataView).filter_by(ontology_id=projection.ontology_id, created_by=projection.owner_id,
                                                 status="ready").filter(QueryDataView.source_manifest_digest.like("workshop:%"))
               .order_by(QueryDataView.created_at.desc()).first())
    used = db.get(QueryDataView, run.data_view_id) if run.data_view_id else None
    aware = lambda value: value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value  # noqa: E731
    if current and used and aware(current.created_at) > aware(used.created_at) and current.content_digest != used.content_digest:
        return {"status": "stale", "reason": "A newer authorized data view has different content",
                "latest_data_view_id": current.id, "run_data_view_id": used.id}
    return {"status": "fresh", "run_id": run.id, "data_view_id": run.data_view_id,
            "input_digest": run.input_digest, "dependency_digest": run.dependency_digest}
