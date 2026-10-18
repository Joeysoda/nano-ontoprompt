"""Versioned study validation, publishing, case management and compilation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.action import OntologyActionType
from app.models.v2.logic_asset import LogicAsset
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioGrant, ScenarioResource, ScenarioRevision
from app.models.v2.scenario_study import CaseDefinition, RunPlan, StudyDefinition, StudyVersion
from app.schemas.v2.scenario_study import StudySpec


EXECUTOR_VERSION = "scenario-dag/v1"
IMPLEMENTATION_KINDS = {"function", "action_preview", "action_apply_scenario", "time_series", "solver"}
RUNNABLE_NODE_KINDS = {"object_query", "function", "action_preview", "aggregate_metric", "compare", "time_series"}


class StudyError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, path: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.path = path


def error_detail(exc: StudyError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message, "path": exc.path, "retryable": False, "details": {}}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _node_part(endpoint: str, path: str) -> tuple[str, str]:
    if "." not in endpoint:
        raise StudyError("invalid_endpoint", "Edge endpoints must use node.port", path=path)
    node, port = endpoint.split(".", 1)
    if not node or not port:
        raise StudyError("invalid_endpoint", "Edge endpoints must use node.port", path=path)
    return node, port


def validate_spec(spec: StudySpec, *, require_nodes: bool = True) -> list[str]:
    """Validate graph semantics and return a stable topological node order."""
    if require_nodes and not spec.nodes:
        raise StudyError("empty_graph", "A published study must contain at least one node", path="nodes")
    if len(spec.nodes) > spec.budgets.max_nodes:
        raise StudyError("budget_exceeded", "Node count exceeds budgets.max_nodes", path="nodes")

    for index, parameter in enumerate(spec.parameters):
        if parameter.minimum is not None and parameter.maximum is not None and parameter.minimum > parameter.maximum:
            raise StudyError("invalid_parameter_range", "Parameter minimum cannot exceed maximum", path=f"parameters.{index}")
        if parameter.default is not None:
            _validate_value(parameter, parameter.default, f"parameters.{index}.default")

    nodes = {node.key: node for node in spec.nodes}
    for index, node in enumerate(spec.nodes):
        if node.kind in IMPLEMENTATION_KINDS and node.implementation is None:
            raise StudyError("implementation_required", f"{node.kind} requires a pinned implementation", path=f"nodes.{index}.implementation")
        if node.kind == "object_query":
            if not node.config.get("type") or not node.config.get("select"):
                raise StudyError("query_projection_required", "Object Query nodes must name a type and explicit property selection", path=f"nodes.{index}.config")
            if any(not isinstance(field, str) or not field or field.startswith("_") for field in node.config["select"]):
                raise StudyError("invalid_query_projection", "Object Query selection must contain public property names", path=f"nodes.{index}.config.select")

    outgoing: dict[str, list[str]] = {key: [] for key in nodes}
    indegree: dict[str, int] = {key: 0 for key in nodes}
    edge_keys: set[tuple[str, str]] = set()
    for index, edge in enumerate(spec.edges):
        source, source_port = _node_part(edge.source, f"edges.{index}.source")
        target, target_port = _node_part(edge.target, f"edges.{index}.target")
        if source not in nodes or target not in nodes:
            raise StudyError("unknown_node", "Edge references an unknown node", path=f"edges.{index}")
        if source_port not in nodes[source].outputs:
            raise StudyError("unknown_output", f"Node {source} has no output {source_port}", path=f"edges.{index}.source")
        if target_port not in nodes[target].inputs:
            raise StudyError("unknown_input", f"Node {target} has no input {target_port}", path=f"edges.{index}.target")
        key = (edge.source, edge.target)
        if key in edge_keys:
            raise StudyError("duplicate_edge", "Duplicate edge", path=f"edges.{index}")
        edge_keys.add(key)
        outgoing[source].append(target)
        indegree[target] += 1

    declared_parameters = {parameter.key for parameter in spec.parameters}
    for node_index, node in enumerate(spec.nodes):
        for input_name, binding in node.inputs.items():
            if isinstance(binding, dict) and set(binding) == {"parameter"}:
                if not isinstance(binding["parameter"], str) or binding["parameter"] not in declared_parameters:
                    raise StudyError("unknown_parameter", "Node input references an unknown Study parameter", path=f"nodes.{node_index}.inputs.{input_name}")
            elif isinstance(binding, dict) and set(binding) == {"from"}:
                source_endpoint = binding["from"]
                if not isinstance(source_endpoint, str):
                    raise StudyError("invalid_endpoint", "from binding must reference node.port", path=f"nodes.{node_index}.inputs.{input_name}")
                source, source_port = _node_part(source_endpoint, f"nodes.{node_index}.inputs.{input_name}")
                if source not in nodes or source_port not in nodes[source].outputs:
                    raise StudyError("unknown_input_source", "Node input references an unknown output", path=f"nodes.{node_index}.inputs.{input_name}")
                if (source_endpoint, f"{node.key}.{input_name}") not in edge_keys:
                    raise StudyError("edge_required", "Every from binding must have a matching Study edge", path=f"nodes.{node_index}.inputs.{input_name}")

    position = {node.key: index for index, node in enumerate(spec.nodes)}
    ready = sorted((key for key, degree in indegree.items() if degree == 0), key=position.get)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current], key=position.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=position.get)
    if len(ordered) != len(nodes):
        raise StudyError("cycle_detected", "Study graph must be acyclic", path="edges")

    for index, metric in enumerate(spec.metrics):
        source, port = _node_part(metric.source, f"metrics.{index}.source")
        if source not in nodes or port not in nodes[source].outputs:
            raise StudyError("unknown_metric_source", "Metric source must reference a declared node output", path=f"metrics.{index}.source")
    for index, constraint in enumerate(spec.constraints):
        source, port = _node_part(constraint.expression_ref, f"constraints.{index}.expression_ref")
        if source not in nodes or port not in nodes[source].outputs:
            raise StudyError("unknown_constraint_source", "Constraint source must reference a declared node output", path=f"constraints.{index}.expression_ref")
        operator = constraint.config.get("operator")
        if operator not in {"lt", "lte", "gt", "gte", "eq", "neq", "between"}:
            raise StudyError("unsupported_constraint", "Constraint operator must be lt, lte, gt, gte, eq, neq or between", path=f"constraints.{index}.config.operator")
        if operator == "between":
            bounds = (constraint.config.get("minimum"), constraint.config.get("maximum"))
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds) or bounds[0] > bounds[1]:
                raise StudyError("invalid_constraint", "between constraints require numeric minimum and maximum", path=f"constraints.{index}.config")
        elif "value" not in constraint.config:
            raise StudyError("invalid_constraint", "Constraint requires config.value", path=f"constraints.{index}.config.value")
        elif operator in {"lt", "lte", "gt", "gte"}:
            value = constraint.config["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StudyError("invalid_constraint", "Numeric comparison constraints require a numeric config.value", path=f"constraints.{index}.config.value")
    return ordered


def _validate_value(definition, value: Any, path: str) -> None:
    expected = definition.type
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "decimal": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "datetime": isinstance(value, str),
        "object_ref": isinstance(value, str),
        "object_set": isinstance(value, list) and all(isinstance(item, str) for item in value),
        "json": True,
    }[expected]
    if not valid:
        raise StudyError("parameter_type_mismatch", f"Parameter {definition.key} must be {expected}", path=path)
    if expected == "json":
        try:
            encoded = canonical_json(value).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise StudyError("parameter_type_mismatch", f"Parameter {definition.key} must be finite JSON", path=path) from exc
        if len(encoded) > 65_536:
            raise StudyError("parameter_too_large", f"Parameter {definition.key} exceeds the 64 KiB JSON limit", path=path)
    if expected == "datetime":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StudyError("parameter_type_mismatch", f"Parameter {definition.key} must be an ISO datetime", path=path) from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if definition.minimum is not None and value < definition.minimum:
            raise StudyError("parameter_out_of_range", f"Parameter {definition.key} is below its minimum", path=path)
        if definition.maximum is not None and value > definition.maximum:
            raise StudyError("parameter_out_of_range", f"Parameter {definition.key} is above its maximum", path=path)
    if definition.enum is not None and value not in definition.enum:
        raise StudyError("parameter_not_allowed", f"Parameter {definition.key} is not an allowed value", path=path)


class StudyService:
    def __init__(self, db: Session, ontology_id: str, user: Any):
        self.db = db
        self.ontology_id = ontology_id
        self.user = user

    def authorize_ontology(self) -> OntologyProject:
        project = self.db.get(OntologyProject, self.ontology_id)
        if not project:
            raise StudyError("ontology_not_found", "Ontology not found", 404, "ontology_id")
        if self.user.role != "admin" and project.created_by != self.user.id:
            raise StudyError("permission_denied", "Study access denied", 403, "ontology_id")
        return project

    def get_study(self, study_id: str, *, write: bool = False) -> StudyDefinition:
        query = self.db.query(StudyDefinition).filter_by(id=study_id, ontology_id=self.ontology_id)
        if write:
            query = query.with_for_update()
        row = query.first()
        if not row:
            raise StudyError("study_not_found", "Study not found", 404, "study_id")
        if self.user.role != "admin" and row.owner_id != self.user.id:
            raise StudyError("permission_denied", "Study access denied", 403, "study_id")
        return row

    def get_version(self, study: StudyDefinition, version: int) -> StudyVersion:
        row = self.db.query(StudyVersion).filter_by(study_id=study.id, version=version).first()
        if not row:
            raise StudyError("version_not_found", "Study version not found", 404, "version")
        return row

    def get_scenario(self, scenario_id: str) -> ScenarioResource:
        row = self.db.query(ScenarioResource).filter_by(
            id=scenario_id, ontology_id=self.ontology_id, status="active"
        ).first()
        if not row:
            raise StudyError("scenario_not_found", "Active scenario not found", 404, "scenario_id")
        if self.user.role != "admin" and row.owner_id != self.user.id:
            grant = self.db.query(ScenarioGrant).filter_by(
                scenario_id=row.id, principal_id=self.user.id
            ).first()
            if not grant:
                # Avoid revealing another user's scenario identifier.
                raise StudyError("scenario_not_found", "Active scenario not found", 404, "scenario_id")
        return row

    def create(self, name: str, description: str, spec: StudySpec) -> StudyDefinition:
        self.authorize_ontology()
        validate_spec(spec, require_nodes=False)
        row = StudyDefinition(
            ontology_id=self.ontology_id,
            owner_id=self.user.id,
            name=name.strip(),
            description=description,
            draft_spec=spec.model_dump(mode="json"),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, study: StudyDefinition, *, expected_etag: int, name: str | None, description: str | None, spec: StudySpec | None) -> StudyDefinition:
        if study.etag != expected_etag:
            raise StudyError("etag_mismatch", "Study changed; refresh and retry", 409, "expected_etag")
        if study.status == "archived":
            raise StudyError("study_archived", "Archived studies cannot be edited", 409, "study_id")
        if spec is not None:
            validate_spec(spec, require_nodes=False)
            study.draft_spec = spec.model_dump(mode="json")
        if name is not None:
            study.name = name.strip()
        if description is not None:
            study.description = description
        study.etag += 1
        study.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(study)
        return study

    def validate_draft(self, study: StudyDefinition) -> dict[str, Any]:
        spec = StudySpec.model_validate(study.draft_spec)
        order = validate_spec(spec)
        return {"valid": True, "spec_hash": stable_hash(spec.model_dump(mode="json")), "topological_order": order}

    def publish(self, study: StudyDefinition, *, expected_etag: int, change_note: str, compatibility: str) -> StudyVersion:
        if study.etag != expected_etag:
            raise StudyError("etag_mismatch", "Study changed; refresh and retry", 409, "expected_etag")
        spec = StudySpec.model_validate(study.draft_spec)
        validate_spec(spec)
        spec_json = spec.model_dump(mode="json")
        digest = stable_hash(spec_json)
        existing = self.db.query(StudyVersion).filter_by(study_id=study.id, spec_hash=digest).first()
        if existing:
            return existing
        project = self.authorize_ontology()
        revision = self.db.get(OntologyRevision, project.current_revision_id) if project.current_revision_id else None
        # The semantic-resource revision digest and the Object Query catalog
        # digest identify different representations. Views use the latter;
        # comparing them rejects a freshly published study on unchanged data.
        # Keep revision identity separately for permission/provenance pinning.
        if revision:
            from app.services.v2.object_query.core import metadata_digest as query_metadata_digest
            from app.services.v2.object_query.metadata import load_sql_metadata
            metadata_digest = query_metadata_digest(load_sql_metadata(self.db, self.ontology_id))
        else:
            metadata_digest = "unversioned"
        row = StudyVersion(
            study_id=study.id,
            version=study.latest_version + 1,
            spec_version=spec.spec_version,
            spec_json=spec_json,
            spec_hash=digest,
            ontology_metadata_revision_id=project.current_revision_id,
            ontology_metadata_digest=metadata_digest,
            compatibility=compatibility,
            change_note=change_note,
            created_by=self.user.id,
        )
        self.db.add(row)
        study.latest_version = row.version
        study.status = "published"
        study.etag += 1
        study.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row

    def create_case(self, study: StudyDefinition, version: StudyVersion, request: Any) -> CaseDefinition:
        scenario = self.get_scenario(request.scenario_id)
        if request.case_kind == "baseline" and self.db.query(CaseDefinition).filter_by(
            study_version_id=version.id, case_kind="baseline", status="active"
        ).first():
            raise StudyError("baseline_exists", "This Study version already has a baseline case", 409, "case_kind")
        if self.db.query(CaseDefinition).filter_by(scenario_id=scenario.id).first():
            raise StudyError("scenario_already_bound", "Each scenario can belong to one Study case", 409, "scenario_id")
        source = None
        if request.source_case_id:
            source = self.db.get(CaseDefinition, request.source_case_id)
            if not source or source.study_version_id != version.id:
                raise StudyError("source_case_not_found", "Source case must belong to the same study version", 422, "source_case_id")
        inherited_parameters = dict(source.parameter_overrides) if source else {}
        inherited_parameters.update(request.parameter_overrides)
        actions = [item.model_dump(mode="json") for item in request.ordered_actions]
        if source and not actions:
            actions = list(source.ordered_actions)
        payload = {
            "study_version_id": version.id,
            "scenario_id": scenario.id,
            "source_case_id": source.id if source else None,
            "parameter_overrides": inherited_parameters,
            "ordered_actions": actions,
        }
        row = CaseDefinition(
            study_version_id=version.id,
            scenario_id=scenario.id,
            source_case_id=source.id if source else None,
            name=request.name.strip(),
            case_kind=request.case_kind,
            parameter_overrides=inherited_parameters,
            ordered_actions=actions,
            case_hash=stable_hash(payload),
            created_by=self.user.id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_case(self, case: CaseDefinition, *, expected_etag: int, name: str | None,
                    parameter_overrides: dict[str, Any] | None, ordered_actions: list[Any] | None) -> CaseDefinition:
        if case.etag != expected_etag:
            raise StudyError("etag_mismatch", "Case changed; refresh and retry", 409, "expected_etag")
        if case.status != "active":
            raise StudyError("case_archived", "Archived cases cannot be edited", 409, "case_id")
        if name is not None:
            case.name = name.strip()
        if parameter_overrides is not None:
            case.parameter_overrides = parameter_overrides
        if ordered_actions is not None:
            case.ordered_actions = [item.model_dump(mode="json") for item in ordered_actions]
        case.case_hash = stable_hash({
            "study_version_id": case.study_version_id,
            "scenario_id": case.scenario_id,
            "source_case_id": case.source_case_id,
            "parameter_overrides": case.parameter_overrides,
            "ordered_actions": case.ordered_actions,
        })
        case.etag += 1
        case.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(case)
        return case

    def get_case(self, study: StudyDefinition, case_id: str) -> tuple[CaseDefinition, StudyVersion]:
        row = self.db.get(CaseDefinition, case_id)
        if not row:
            raise StudyError("case_not_found", "Case not found", 404, "case_id")
        version = self.db.get(StudyVersion, row.study_version_id)
        if not version or version.study_id != study.id:
            raise StudyError("case_not_found", "Case not found", 404, "case_id")
        return row, version

    def _resolved_parameters(self, spec: StudySpec, overrides: dict[str, Any]) -> dict[str, Any]:
        definitions = {item.key: item for item in spec.parameters}
        unknown = sorted(set(overrides) - set(definitions))
        if unknown:
            raise StudyError("unknown_parameter", f"Unknown parameter: {unknown[0]}", path=f"parameter_overrides.{unknown[0]}")
        values: dict[str, Any] = {}
        for definition in spec.parameters:
            value = overrides.get(definition.key, definition.default)
            if value is None:
                if definition.required:
                    raise StudyError("required_parameter_missing", f"Parameter {definition.key} is required", path=f"parameter_overrides.{definition.key}")
                continue
            _validate_value(definition, value, f"parameter_overrides.{definition.key}")
            values[definition.key] = value
        return values

    def _validate_implementations(self, spec: StudySpec) -> None:
        for index, node in enumerate(spec.nodes):
            impl = node.implementation
            if node.kind not in RUNNABLE_NODE_KINDS:
                raise StudyError("capability_unavailable", f"{node.kind} nodes are not enabled yet", 409, f"nodes.{index}")
            if node.kind in {"function", "time_series"}:
                found = self.db.query(LogicAsset).filter_by(
                    ontology_id=self.ontology_id, asset_key=impl.key, version=impl.version, status="published"
                ).first()
                if not found:
                    raise StudyError("implementation_unavailable", "Pinned logic asset is unavailable", 409, f"nodes.{index}.implementation")
            if node.kind in {"action_preview", "action_apply_scenario"}:
                found = self.db.query(OntologyActionType).filter_by(
                    ontology_id=self.ontology_id, id=impl.key, enabled=True, status="published"
                ).first()
                if not found or str(found.version) != impl.version:
                    raise StudyError("implementation_unavailable", "Pinned action version is unavailable", 409, f"nodes.{index}.implementation")

    def compile(self, study: StudyDefinition, case: CaseDefinition, version: StudyVersion) -> RunPlan:
        spec = StudySpec.model_validate(version.spec_json)
        order = validate_spec(spec)
        self._validate_implementations(spec)
        parameters = self._resolved_parameters(spec, case.parameter_overrides)
        if case.ordered_actions:
            raise StudyError("case_actions_not_executable", "Case Actions must be represented by a supported Study node before this case can run", 409, "ordered_actions")
        scenario = self.get_scenario(case.scenario_id)
        revision = None
        if scenario.head_revision:
            revision = self.db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=scenario.head_revision, status="ready").first()
            if not revision:
                raise StudyError("scenario_revision_unavailable", "Scenario head revision is not ready", 409, "scenario_id")
        data_view_id = revision.result_view_id if revision else scenario.base_view_id
        view = self.db.query(QueryDataView).filter_by(id=data_view_id, ontology_id=self.ontology_id, status="ready").first()
        if not view:
            raise StudyError("data_view_unavailable", "Resolved data view is not ready", 409, "scenario_id")
        if version.ontology_metadata_digest != "unversioned" and view.metadata_digest != version.ontology_metadata_digest:
            raise StudyError("metadata_mismatch", "Study version metadata differs from the scenario data view", 409, "study_version")

        from app.services.v2.authorization_service import AuthorizationContext, resolve_projection
        requested_fields = sorted({
            field
            for node in spec.nodes if node.kind == "object_query"
            for field in (node.config.get("select") or [])
        })
        permission = resolve_projection(self.db, AuthorizationContext(
            ontology_id=self.ontology_id,
            principal_id=self.user.id,
            principal_role=self.user.role,
            metadata_revision_id=version.ontology_metadata_revision_id,
            requested_fields=tuple(requested_fields),
        ), self.user.role)
        if not permission.allowed:
            raise StudyError("permission_denied", "The current user cannot read this study context", 403, "scenario_id")
        permission_digest = permission.permission_digest
        node_map = {node.key: node.model_dump(mode="json") for node in spec.nodes}
        payload = {
            "executor_version": EXECUTOR_VERSION,
            "ontology_id": self.ontology_id,
            "study_id": study.id,
            "study_version_id": version.id,
            "study_version": version.version,
            "spec_hash": version.spec_hash,
            "case_id": case.id,
            "case_hash": case.case_hash,
            "scenario_id": scenario.id,
            "scenario_revision": scenario.head_revision,
            "data_view_id": data_view_id,
            "ontology_metadata_revision_id": version.ontology_metadata_revision_id,
            "ontology_metadata_digest": view.metadata_digest,
            "permission_projection_digest": permission_digest,
            "parameters": parameters,
            "ordered_actions": case.ordered_actions,
            "topological_order": order,
            "nodes": [node_map[key] for key in order],
            "edges": [edge.model_dump(mode="json") for edge in spec.edges],
            "metrics": [metric.model_dump(mode="json") for metric in spec.metrics],
            "constraints": [constraint.model_dump(mode="json") for constraint in spec.constraints],
            "budgets": spec.budgets.model_dump(mode="json"),
        }
        plan_hash = stable_hash(payload)
        existing = self.db.query(RunPlan).filter_by(plan_hash=plan_hash).first()
        if existing:
            return existing
        row = RunPlan(
            case_id=case.id,
            study_version_id=version.id,
            scenario_id=scenario.id,
            scenario_revision=scenario.head_revision,
            data_view_id=data_view_id,
            spec_hash=version.spec_hash,
            case_hash=case.case_hash,
            ontology_metadata_revision_id=version.ontology_metadata_revision_id,
            ontology_metadata_digest=view.metadata_digest,
            permission_projection_digest=permission_digest,
            executor_version=EXECUTOR_VERSION,
            plan_json=payload,
            plan_hash=plan_hash,
            created_by=self.user.id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row


def study_summary(row: StudyDefinition) -> dict[str, Any]:
    return {
        "id": row.id, "ontology_id": row.ontology_id, "owner_id": row.owner_id,
        "name": row.name, "description": row.description, "status": row.status,
        "draft_spec": row.draft_spec, "latest_version": row.latest_version, "etag": row.etag,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
    }


def version_summary(row: StudyVersion) -> dict[str, Any]:
    return {
        "id": row.id, "study_id": row.study_id, "version": row.version,
        "spec_version": row.spec_version, "spec": row.spec_json, "spec_hash": row.spec_hash,
        "ontology_metadata_revision_id": row.ontology_metadata_revision_id,
        "ontology_metadata_digest": row.ontology_metadata_digest, "compatibility": row.compatibility,
        "change_note": row.change_note, "created_by": row.created_by, "created_at": row.created_at.isoformat(),
    }


def case_summary(row: CaseDefinition) -> dict[str, Any]:
    return {
        "id": row.id, "study_version_id": row.study_version_id, "scenario_id": row.scenario_id,
        "source_case_id": row.source_case_id, "name": row.name, "case_kind": row.case_kind,
        "parameter_overrides": row.parameter_overrides, "ordered_actions": row.ordered_actions,
        "case_hash": row.case_hash, "status": row.status, "etag": row.etag,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
    }


def plan_summary(row: RunPlan) -> dict[str, Any]:
    return {
        "id": row.id, "case_id": row.case_id, "study_version_id": row.study_version_id,
        "scenario_id": row.scenario_id, "scenario_revision": row.scenario_revision,
        "data_view_id": row.data_view_id, "plan_hash": row.plan_hash, "status": row.status,
        "executor_version": row.executor_version, "plan": row.plan_json,
        "created_at": row.created_at.isoformat(),
    }
