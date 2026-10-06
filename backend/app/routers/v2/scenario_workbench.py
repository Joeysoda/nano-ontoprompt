"""User-configurable Scenario workspace over existing Ontology data."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.v2.action import OntologyActionType
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioAudit, ScenarioChangeSet, ScenarioRevision
from app.schemas.v2.scenario import Edit, RevisionRequest, CreateScenarioRequest
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.object_query.data_views import build_live_view
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.scenario_actions import compile_actions, validate_definition
from app.services.v2.scenarios import ScenarioError, ScenarioService, error, _summary

router = APIRouter()


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FromLive(StrictBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    ttl_seconds: int = Field(default=30 * 86400, ge=3600, le=365 * 86400)
    mode: Literal['tracking', 'pinned'] = 'tracking'


class ScenarioSettings(StrictBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    expected_etag: int = Field(ge=1)
    ttl_seconds: int = Field(ge=3600, le=365 * 86400)


class ActionDefinition(StrictBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    parameters: list[dict] = Field(default_factory=list)
    rules: list[dict] = Field(min_length=1, max_length=30)


class ActionInvocation(StrictBody):
    action_type_id: str
    parameters: dict = Field(default_factory=dict)


class ActionList(StrictBody):
    expected_etag: int = Field(ge=1)
    client_request_id: str = Field(min_length=1, max_length=200)
    actions: list[ActionInvocation] = Field(max_length=30)


class MergeCommand(StrictBody):
    expected_revision: int = Field(ge=1)


def _service(ontology_id, db, user):
    service = ScenarioService(db, ontology_id, user)
    service.authorize_ontology()
    return service


def _http(exc):
    raise HTTPException(exc.status, error(exc)) from exc


def _action_dict(row):
    return {"id": row.id, "name": row.name, "description": row.description or "",
            "parameters": row.parameters or [], "rules": row.effects or [], "version": row.version,
            "status": row.status, "enabled": row.enabled}


def _snapshot(service, view_id):
    view = service.db.get(QueryDataView, view_id)
    if not view or view.ontology_id != service.ontology_id or view.status != "ready":
        raise ScenarioError("view_unavailable", "Scenario data view is unavailable", 409)
    if not service.graph.available:
        raise ScenarioError("graph_unavailable", "FalkorDB is unavailable", 503)
    return FalkorReadAdapter(service.graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(service.ontology_id)


@router.post("/{ontology_id}/scenarios:from-live", status_code=201)
def create_from_live(ontology_id: str, body: FromLive, db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        if not service.graph.available:
            raise ScenarioError("graph_unavailable", "FalkorDB is unavailable", 503)
        metadata = load_sql_metadata(db, ontology_id)
        capture_id = stable_hash({"ontology": ontology_id, "time": datetime.now(timezone.utc).isoformat(),
                                  "nonce": uuid4().hex})
        view = build_live_view(db, service.graph, metadata, ontology_id, user.id, capture_id, body.ttl_seconds)
        scenario = service.create(CreateScenarioRequest(name=body.name.strip(), description=body.description,
                                                        base_view_id=view.id, ttl_seconds=body.ttl_seconds, mode=body.mode))
        return _summary(scenario)
    except ScenarioError as exc:
        _http(exc)


class RebaseCommand(StrictBody):
    expected_etag: int = Field(ge=1)


@router.post('/{ontology_id}/scenarios/{scenario_id}:rebase')
def rebase_scenario(ontology_id: str, scenario_id: str, body: RebaseCommand, db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.v2.scenario_tracking import rebase
    try:
        return rebase(_service(ontology_id, db, user), scenario_id, body.expected_etag)
    except ScenarioError as exc:
        db.rollback()
        _http(exc)


@router.put("/{ontology_id}/scenarios/{scenario_id}/settings")
def update_scenario_settings(ontology_id: str, scenario_id: str, body: ScenarioSettings,
                             db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id, write=True)
        if scenario.status != "active" or scenario.protected_demo:
            raise ScenarioError("scenario_read_only", "Scenario settings are read only", 409)
        if scenario.etag != body.expected_etag:
            raise ScenarioError("etag_conflict", "Scenario changed; reload settings", 409)
        scenario.name = body.name.strip()
        scenario.description = body.description
        scenario.ttl_seconds = body.ttl_seconds
        retention = datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)
        view_ids = [scenario.base_view_id] + [item.result_view_id for item in
            db.query(ScenarioRevision).filter_by(scenario_id=scenario.id).all()]
        for view_id in view_ids:
            view = db.get(QueryDataView, view_id)
            if view and view.ontology_id == ontology_id:
                view.retention_until = retention
        scenario.etag += 1
        scenario.updated_at = datetime.now(timezone.utc)
        db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=user.id, operation="settings",
                             summary={"name": scenario.name, "ttl_seconds": scenario.ttl_seconds}))
        db.commit()
        return _summary(scenario)
    except ScenarioError as exc:
        _http(exc)


@router.get("/{ontology_id}/scenario-action-types")
def list_action_types(ontology_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    try:
        _service(ontology_id, db, user)
        rows = db.query(OntologyActionType).filter_by(ontology_id=ontology_id,
                  action_category="scenario").order_by(OntologyActionType.created_at.desc()).all()
        return {"action_types": [_action_dict(row) for row in rows]}
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenario-action-types", status_code=201)
def create_action_type(ontology_id: str, body: ActionDefinition, db=Depends(get_db), user=Depends(get_current_user)):
    try:
        _service(ontology_id, db, user)
        validate_definition(body.parameters, body.rules)
        row = OntologyActionType(ontology_id=ontology_id, name=body.name.strip(),
                                 description=body.description, action_category="scenario",
                                 parameters=body.parameters, effects=body.rules, created_by=user.id,
                                 status="draft", version=1)
        db.add(row); db.commit(); db.refresh(row)
        return _action_dict(row)
    except ScenarioError as exc:
        _http(exc)


@router.put("/{ontology_id}/scenario-action-types/{action_type_id}")
def update_action_type(ontology_id: str, action_type_id: str, body: ActionDefinition,
                       db=Depends(get_db), user=Depends(get_current_user)):
    try:
        _service(ontology_id, db, user)
        row = db.query(OntologyActionType).filter_by(id=action_type_id, ontology_id=ontology_id,
                   action_category="scenario").first()
        if not row:
            raise ScenarioError("action_type_not_found", "Action type not found", 404)
        if user.role != "admin" and row.created_by != user.id:
            raise ScenarioError("forbidden", "Only the Action type author may edit it", 403)
        validate_definition(body.parameters, body.rules)
        row.name, row.description = body.name.strip(), body.description
        row.parameters, row.effects = body.parameters, body.rules
        row.version += 1
        row.status = "draft"
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _action_dict(row)
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenario-action-types/{action_type_id}:publish")
def publish_action_type(ontology_id: str, action_type_id: str,
                        db=Depends(get_db), user=Depends(get_current_user)):
    try:
        _service(ontology_id, db, user)
        row = db.query(OntologyActionType).filter_by(id=action_type_id, ontology_id=ontology_id,
                   action_category="scenario").first()
        if not row:
            raise ScenarioError("action_type_not_found", "Action type not found", 404)
        if user.role != "admin" and row.created_by != user.id:
            raise ScenarioError("forbidden", "Only the Action type author may publish it", 403)
        validate_definition(row.parameters, row.effects)
        row.status = "published"
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _action_dict(row)
    except ScenarioError as exc:
        _http(exc)


def _compiled(service, scenario, body):
    from app.services.v2.function_binding import function_resolver
    if scenario.status != "active":
        raise ScenarioError("scenario_archived", "Archived scenarios cannot be edited", 409)
    if scenario.etag != body.expected_etag:
        raise ScenarioError("etag_conflict", "Scenario changed; reload before submitting", 409)
    base = _snapshot(service, scenario.base_view_id)
    actions = [item.model_dump() for item in body.actions]
    ids = {item["action_type_id"] for item in actions}
    types = {row.id: row for row in service.db.query(OntologyActionType).filter(
        OntologyActionType.ontology_id == service.ontology_id,
        OntologyActionType.action_category == "scenario",
        OntologyActionType.id.in_(ids)).all()} if ids else {}
    edits = compile_actions(service.ontology_id, scenario.id, actions, types, base, actor=service.user, function_resolver=function_resolver(service.db, service.ontology_id))
    if not edits:
        edits = [Edit(sequence=0, op="invoke_action", action_key="scenario_reset",
                      parameters={}, source_action="scenario_reset")]
    return edits


@router.post("/{ontology_id}/scenarios/{scenario_id}/actions:preview")
def preview_actions(ontology_id: str, scenario_id: str, body: ActionList,
                    db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id, write=True)
        edits = _compiled(service, scenario, body)
        return {"valid": True, "edits": [edit.model_dump(mode="json") for edit in edits],
                "edit_count": sum(edit.op != "invoke_action" for edit in edits),
                "action_count": len(body.actions)}
    except ScenarioError as exc:
        _http(exc)


@router.put("/{ontology_id}/scenarios/{scenario_id}/actions")
def submit_actions(ontology_id: str, scenario_id: str, body: ActionList,
                   db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id, write=True)
        input_hash = stable_hash([item.model_dump(mode="json") for item in body.actions])
        prior = db.query(ScenarioChangeSet).filter_by(scenario_id=scenario.id,
                    client_request_id=body.client_request_id).first()
        if prior:
            if prior.validation_report.get("action_input_hash") != input_hash:
                raise ScenarioError("idempotency_conflict", "Request ID was used for different Actions", 409)
            prior_revision = db.get(ScenarioRevision, prior.validation_report["revision_id"])
            return {"scenario_id": scenario.id, "revision": prior_revision.revision,
                    "result_view_id": prior_revision.result_view_id,
                    "changeset_id": prior_revision.changeset_id,
                    "action_count": len(body.actions)}
        edits = _compiled(service, scenario, body)
        request = RevisionRequest(base_revision=scenario.head_revision,
                                  expected_etag=scenario.etag, client_request_id=body.client_request_id,
                                  edits=edits)
        revision = service.revision(scenario, request, replace_all=True)
        changeset = db.get(ScenarioChangeSet, revision.changeset_id)
        changeset.validation_report = {**changeset.validation_report, "action_input_hash": input_hash}
        db.commit()
        return {"scenario_id": scenario.id, "revision": revision.revision,
                "result_view_id": revision.result_view_id, "changeset_id": revision.changeset_id,
                "action_count": len(body.actions)}
    except ScenarioError as exc:
        _http(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, {"code": "action_submission_failed", "message": str(exc)}) from exc


@router.get("/{ontology_id}/scenarios/{scenario_id}/workspace")
def get_workspace(ontology_id: str, scenario_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id)
        base = _snapshot(service, scenario.base_view_id)
        revision = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id,
                    revision=scenario.head_revision).first() if scenario.head_revision else None
        candidate = _snapshot(service, revision.result_view_id) if revision else base
        changeset = db.get(ScenarioChangeSet, revision.changeset_id) if revision else None
        def objects(snapshot):
            return [{"object_type": kind, "object_id": oid, "properties": props}
                    for (kind, oid), props in sorted(snapshot.objects.items())]
        base_keys, candidate_keys = set(base.objects), set(candidate.objects)
        modified = [key for key in base_keys & candidate_keys
                    if base.objects[key] != candidate.objects[key]]
        actions = []
        for edit in changeset.ordered_edits if changeset else []:
            if edit.get("op") == "invoke_action" and edit.get("action_key") != "scenario_reset":
                actions.append({"action_type_id": edit["action_key"],
                                "parameters": edit.get("parameters", {}).get("values", {})})
        return {"scenario": _summary(scenario), "revision": revision.revision if revision else 0,
                "view_id": revision.result_view_id if revision else scenario.base_view_id,
                "actions": actions, "base_objects": objects(base), "objects": objects(candidate),
                "edges": [{"source_type": src[0], "source_id": src[1], "relation_type": rel,
                           "target_type": dst[0], "target_id": dst[1]} for src, rel, dst in candidate.edges],
                "changes": {"created": [{"object_type": key[0], "object_id": key[1]} for key in sorted(candidate_keys - base_keys)],
                            "deleted": [{"object_type": key[0], "object_id": key[1]} for key in sorted(base_keys - candidate_keys)],
                            "modified": [{"object_type": key[0], "object_id": key[1],
                                          "before": base.objects[key], "after": candidate.objects[key]} for key in sorted(modified)]},
                "edits": changeset.ordered_edits if changeset else []}
    except ScenarioError as exc:
        _http(exc)


@router.get("/{ontology_id}/scenarios/{scenario_id}/compare")
def compare_scenarios(ontology_id: str, scenario_id: str, metric_type: str = "",
                      metric_property: str = "", db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        active = service.get(scenario_id)
        base = _snapshot(service, active.base_view_id)
        options = sorted({(kind, prop) for (kind, _), values in base.objects.items()
                          for prop, value in values.items() if not prop.startswith("_")
                          and isinstance(value, (int, float)) and not isinstance(value, bool)})
        if metric_type and (metric_type, metric_property) not in options:
            raise ScenarioError("invalid_metric", "Select a numeric property from this Scenario base")
        def metric(snapshot):
            if not metric_type:
                return len(snapshot.objects)
            return sum(float(props.get(metric_property) or 0) for (kind, _), props in
                       snapshot.objects.items() if kind == metric_type and
                       isinstance(props.get(metric_property), (int, float)) and
                       not isinstance(props.get(metric_property), bool))
        from app.models.v2.scenario import ScenarioResource
        peers = db.query(ScenarioResource).filter_by(ontology_id=ontology_id,
                  base_view_id=active.base_view_id, status="active").order_by(ScenarioResource.name).all()
        columns = []
        for peer in peers:
            try:
                service.get(peer.id)
            except ScenarioError:
                continue
            revision = db.query(ScenarioRevision).filter_by(scenario_id=peer.id,
                        revision=peer.head_revision).first() if peer.head_revision else None
            snapshot = _snapshot(service, revision.result_view_id) if revision else base
            base_keys, keys = set(base.objects), set(snapshot.objects)
            columns.append({"id": peer.id, "name": peer.name, "revision": peer.head_revision,
                            "metric": metric(snapshot),
                            "created": len(keys - base_keys), "deleted": len(base_keys - keys),
                            "modified": sum(base.objects[key] != snapshot.objects[key]
                                            for key in keys & base_keys),
                            "links_added": len(set(snapshot.edges) - set(base.edges)),
                            "links_removed": len(set(base.edges) - set(snapshot.edges))})
        return {"base_view_id": active.base_view_id,
                "metric_options": [{"object_type": kind, "property": prop} for kind, prop in options],
                "metric": {"object_type": metric_type, "property": metric_property},
                "baseline": {"name": "Baseline", "objects": len(base.objects),
                             "links": len(base.edges), "metric": metric(base)}, "columns": columns}
    except ScenarioError as exc:
        _http(exc)


def _merge_state(service, scenario):
    db = service.db
    revision = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id,
                revision=scenario.head_revision).first() if scenario.head_revision else None
    if not revision:
        return {"can_merge": False, "conflicts": [{"object_type": "", "object_id": "",
                "property": "", "reason": "Submit Actions before merging"}], "applied_edits": 0,
                "already_merged": False}, []
    prior = db.query(ScenarioAudit).filter_by(scenario_id=scenario.id, revision=revision.revision,
             operation="merge").first()
    if prior:
        return {"can_merge": False, "conflicts": [], "applied_edits": 0,
                "already_merged": True}, []
    changeset = db.get(ScenarioChangeSet, revision.changeset_id)
    if not changeset or not changeset.validation_report.get("replace_all"):
        return {"can_merge": False, "conflicts": [{"object_type": "", "object_id": "",
                "property": "", "reason": "This revision predates the reviewed Action contract"}],
                "applied_edits": 0, "already_merged": False}, []
    base = _snapshot(service, scenario.base_view_id)
    live = FalkorReadAdapter(service.graph._graph(scenario.ontology_id)).read(scenario.ontology_id)
    edits = [item for item in changeset.ordered_edits if item.get("op") != "invoke_action"]
    conflicts = []
    projected = {key: dict(props) for key, props in live.objects.items()}
    projected_links = set(live.edges)
    base_links = set(base.edges)
    for edit in edits:
        op = edit.get("op")
        ref = edit.get("target") or {}
        key = (ref.get("concrete_type", ""), ref.get("object_id", ""))
        reason = None
        prop = edit.get("property", "")
        if op in {"set_property", "unset_property"}:
            if key not in projected:
                reason = "Object is absent from main Ontology"
            elif not db.query(EntityInstance).filter_by(ontology_id=scenario.ontology_id, id=key[1]).first():
                reason = "Object has no canonical SQL row to merge"
            elif projected[key].get(prop) != edit.get("expected_old_value"):
                reason = "Main Ontology value changed since this Scenario was created"
            elif op == "set_property":
                projected[key][prop] = edit.get("value")
            else:
                projected[key].pop(prop, None)
        elif op == "create_object":
            if key in projected:
                reason = "Object already exists in main Ontology"
            else:
                known = db.query(Entity.id).filter(Entity.ontology_id == scenario.ontology_id,
                        (Entity.name_en == key[0]) | (Entity.name_cn == key[0])).first()
                if not known:
                    reason = "Object type is not defined in main Ontology"
                else:
                    projected[key] = dict(edit.get("properties") or {})
        elif op == "delete_object":
            if key not in projected:
                reason = "Object is absent from main Ontology"
            elif not db.query(EntityInstance).filter_by(ontology_id=scenario.ontology_id, id=key[1]).first():
                reason = "Object has no canonical SQL row to merge"
            elif key not in base.objects or {k: v for k, v in projected[key].items() if not k.startswith("_")} != {
                    k: v for k, v in base.objects[key].items() if not k.startswith("_")}:
                reason = "Object changed since this Scenario was created"
            else:
                projected.pop(key)
                projected_links = {link for link in projected_links if key not in (link[0], link[2])}
        elif op in {"add_link", "remove_link"}:
            link = edit.get("link") or {}
            source, target = link.get("source") or {}, link.get("target") or {}
            edge = ((source.get("concrete_type"), source.get("object_id")), link.get("relation_type"),
                    (target.get("concrete_type"), target.get("object_id")))
            if edge[0] not in projected or edge[2] not in projected:
                reason = "Link endpoint is absent from main Ontology"
            elif op == "add_link" and edge in projected_links:
                reason = "Link already exists in main Ontology"
            elif op == "remove_link" and edge not in projected_links:
                reason = "Link is absent from main Ontology"
            elif op == "remove_link" and edge not in base_links:
                reason = "Link was not present in the Scenario base"
            elif op == "add_link":
                projected_links.add(edge)
            else:
                projected_links.remove(edge)
        else:
            reason = f"Unsupported merge edit {op}"
        if reason:
            conflicts.append({"object_type": key[0], "object_id": key[1], "property": prop,
                              "reason": reason})
    return {"can_merge": bool(edits) and not conflicts, "conflicts": conflicts,
            "applied_edits": len(edits), "already_merged": False}, edits


@router.post("/{ontology_id}/scenarios/{scenario_id}:merge-preview")
def preview_merge(ontology_id: str, scenario_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id)
        if scenario.protected_demo:
            raise ScenarioError("protected_scenario", "Prepared demo Scenarios cannot be merged", 403)
        result, _ = _merge_state(service, scenario)
        return result
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}:merge")
def merge_scenario(ontology_id: str, scenario_id: str, body: MergeCommand,
                   db=Depends(get_db), user=Depends(get_current_user)):
    try:
        service = _service(ontology_id, db, user)
        scenario = service.get(scenario_id, write=True)
        if scenario.protected_demo:
            raise ScenarioError("protected_scenario", "Prepared demo Scenarios cannot be merged", 403)
        if scenario.head_revision != body.expected_revision:
            raise ScenarioError("revision_conflict", "Scenario changed; review the merge again", 409)
        result, edits = _merge_state(service, scenario)
        if not result["can_merge"]:
            raise ScenarioError("merge_conflict", "Merge preview contains conflicts or no supported edits", 409)
        from app.services.v2.live_edits import apply_live_edits
        typed_edits = [Edit.model_validate(item) for item in edits]
        _, _, _, restore_graph = apply_live_edits(db, ontology_id, typed_edits, service.graph)
        try:
            db.add(ScenarioAudit(scenario_id=scenario.id, revision=scenario.head_revision,
                                 actor_id=user.id, operation="merge",
                                 summary={"applied_edits": len(edits), "base_view_id": scenario.base_view_id}))
            db.commit()
        except Exception:
            db.rollback()
            restore_graph()
            raise
        return {"scenario_id": scenario.id, "revision": scenario.head_revision,
                "status": "merged", "applied_edits": len(edits)}
    except ScenarioError as exc:
        _http(exc)
