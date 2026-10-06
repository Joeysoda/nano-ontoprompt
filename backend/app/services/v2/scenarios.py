"""Scenario catalog and immutable revision builder.

The service deliberately reuses QueryDataView and the existing FalkorDB adapter;
there is no second query or object storage model here.
"""
from __future__ import annotations
from datetime import timedelta
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from app.models.ontology import OntologyProject
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioAudit, ScenarioChangeSet, ScenarioGrant, ScenarioResource, ScenarioRevision
from app.schemas.v2.scenario import ChangeSetRequest, Edit
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.object_query.data_views import aware, now, require_ready
from app.services.v2.object_query.normalize import stable_hash
from app.schemas.v2.object_query import ExecutionContext


class ScenarioError(Exception):
    def __init__(self, code, message, status=422, path="scenario"):
        self.code, self.message, self.status, self.path = code, message, status, path
        super().__init__(message)


def error(exc: ScenarioError):
    return {"code": exc.code, "path": exc.path, "message": exc.message, "details": {}}


def _target_id(target):
    return str(target.object_id)


def _target_key(target):
    return (target.concrete_type, _target_id(target))


def _validate_edit(edit: Edit, ontology_id: str):
    if edit.op in {"set_property", "unset_property", "delete_object"} and not edit.target:
        raise ScenarioError("invalid_edit", "Target is required for this edit", path=f"edits[{edit.sequence}].target")
    if edit.op in {"set_property", "unset_property"} and not edit.property:
        raise ScenarioError("invalid_edit", "Property is required", path=f"edits[{edit.sequence}].property")
    if edit.op in {"add_link", "remove_link"} and not edit.link:
        raise ScenarioError("invalid_edit", "Link is required", path=f"edits[{edit.sequence}].link")
    for candidate in (edit.target, edit.link.source if edit.link else None, edit.link.target if edit.link else None):
        if candidate and candidate.ontology_id != ontology_id:
            raise ScenarioError("ontology_context_mismatch", "Edit target ontology differs from scenario", path=f"edits[{edit.sequence}]")


def _summary(row):
    return {"id": row.id, "ontology_id": row.ontology_id, "owner_id": row.owner_id, "name": row.name,
            "description": row.description, "status": row.status, "base_view_id": row.base_view_id,
            "head_revision": row.head_revision, "etag": row.etag, "ttl_seconds": row.ttl_seconds,
            "mode": row.mode, "last_rebased_at": row.last_rebased_at.isoformat() if row.last_rebased_at else None,
            "rebase_error": row.rebase_error,
            "protected_demo": row.protected_demo, "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat()}


class ScenarioService:
    def __init__(self, db, ontology_id, user, graph=None):
        self.db, self.ontology_id, self.user = db, ontology_id, user
        self.graph = graph or FalkorDBService()

    def authorize_ontology(self):
        row = self.db.get(OntologyProject, self.ontology_id)
        if not row or (self.user.role != "admin" and row.created_by != self.user.id):
            raise ScenarioError("not_found", "Ontology not found", 404)

    def get(self, scenario_id, write=False):
        row = self.db.get(ScenarioResource, scenario_id)
        if not row or row.ontology_id != self.ontology_id:
            raise ScenarioError("not_found", "Scenario not found", 404)
        if row.ttl_seconds and aware(row.created_at) + timedelta(seconds=row.ttl_seconds) <= now():
            raise ScenarioError('scenario_expired', 'Scenario retention period has expired', 410)
        grant = self.db.get(ScenarioGrant, (row.id, self.user.id))
        if self.user.role != "admin" and row.owner_id != self.user.id and not grant:
            raise ScenarioError("forbidden", "Scenario access denied", 403)
        if write and self.user.role != "admin" and row.owner_id != self.user.id and (not grant or grant.role != "editor"):
            raise ScenarioError("forbidden", "Scenario editor access required", 403)
        return row

    def create(self, request):
        view = require_ready(self.db, request.base_view_id, self.ontology_id, principal_id=self.user.id, is_admin=self.user.role == "admin")
        row = ScenarioResource(ontology_id=self.ontology_id, owner_id=self.user.id, name=request.name,
                               description=request.description, base_view_id=view.id, ttl_seconds=request.ttl_seconds,
                               protected_demo=request.protected_demo, mode=request.mode,
                               last_rebased_at=now() if request.mode == 'tracking' else None)
        self.db.add(row); self.db.flush()
        self.db.add(ScenarioAudit(scenario_id=row.id, actor_id=self.user.id, operation="create", summary={"base_view_id": view.id}))
        self.db.commit(); self.db.refresh(row)
        return row

    def validate(self, scenario, request: ChangeSetRequest):
        if request.base_revision != scenario.head_revision:
            raise ScenarioError("revision_conflict", "ChangeSet base revision is not the current head", 409, "base_revision")
        if len({edit.sequence for edit in request.edits}) != len(request.edits):
            raise ScenarioError("invalid_edit", "Edit sequence values must be unique", path="edits.sequence")
        for edit in sorted(request.edits, key=lambda item: item.sequence):
            _validate_edit(edit, self.ontology_id)
        canonical = request.model_dump(mode="json")
        return canonical, stable_hash(canonical)

    def revision(self, scenario, request, *, replace_all=False):
        self.get(scenario.id, write=True)
        if scenario.status != "active":
            raise ScenarioError("scenario_archived", "Archived scenarios cannot be edited", 409)
        canonical, payload_hash = self.validate(scenario, request)
        payload_hash = stable_hash({"request": canonical, "replace_all": replace_all})
        existing = self.db.query(ScenarioChangeSet).filter_by(scenario_id=scenario.id, client_request_id=request.client_request_id).first()
        if existing:
            if existing.payload_hash != payload_hash:
                raise ScenarioError("idempotency_conflict", "Request id was already used with another payload", 409, "client_request_id")
            return self.db.get(ScenarioRevision, existing.validation_report.get("revision_id"))
        if request.expected_etag != scenario.etag:
            raise ScenarioError("etag_conflict", "Scenario changed since it was read", 409, "expected_etag")
        parent_view = self.db.get(QueryDataView, scenario.base_view_id if replace_all or scenario.head_revision == 0 else self.db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=scenario.head_revision).one().result_view_id)
        require_ready(self.db, parent_view.id, self.ontology_id, principal_id=self.user.id, is_admin=self.user.role == "admin")
        changeset = ScenarioChangeSet(scenario_id=scenario.id, base_revision=request.base_revision, ordered_edits=canonical["edits"],
                                      payload_hash=payload_hash, client_request_id=request.client_request_id, actor_id=self.user.id, validation_report={"status": "validated"})
        self.db.add(changeset); self.db.flush()
        revision_number = scenario.head_revision + 1
        source = FalkorReadAdapter(self.graph._graph(parent_view.graph_key), graph_ontology_id=parent_view.graph_key).read(self.ontology_id)
        objects = {key: dict(value) for key, value in source.objects.items()}
        edges = list(source.edges)
        for raw in sorted(request.edits, key=lambda item: item.sequence):
            edit = Edit.model_validate(raw)
            if edit.op == "set_property":
                key = _target_key(edit.target)
                if key not in objects: raise ScenarioError("object_not_found", "Target object does not exist", path=f"edits[{edit.sequence}]")
                if "expected_old_value" in edit.model_fields_set and objects[key].get(edit.property) != edit.expected_old_value:
                    raise ScenarioError("edit_conflict", "Target property changed since the Action was prepared", 409, f"edits[{edit.sequence}]")
                objects[key][edit.property] = edit.value
            elif edit.op == "unset_property":
                key = _target_key(edit.target)
                if key not in objects: raise ScenarioError("object_not_found", "Target object does not exist", path=f"edits[{edit.sequence}]")
                if "expected_old_value" in edit.model_fields_set and objects[key].get(edit.property) != edit.expected_old_value:
                    raise ScenarioError("edit_conflict", "Target property changed since the Action was prepared", 409, f"edits[{edit.sequence}]")
                objects[key].pop(edit.property, None)
            elif edit.op == "delete_object":
                key = _target_key(edit.target)
                if key not in objects: raise ScenarioError("object_not_found", "Target object does not exist", path=f"edits[{edit.sequence}]")
                objects.pop(key)
                edges = [edge for edge in edges if edge[0] != key and edge[2] != key]
            elif edit.op == "create_object":
                object_id = _target_id(edit.target) if edit.target else f"{scenario.id}:{request.client_request_id}:{edit.sequence}"
                objects[(edit.object_type or "Entity", object_id)] = dict(edit.properties)
            elif edit.op in {"add_link", "remove_link"}:
                link = edit.link
                edge = ((link.source.concrete_type, link.source.object_id), link.relation_type, (link.target.concrete_type, link.target.object_id))
                if edit.op == "add_link" and edge not in edges: edges.append(edge)
                if edit.op == "remove_link": edges = [item for item in edges if item != edge]
        graph_key = f"scenario_{scenario.id}_{revision_number}_{payload_hash[:24]}"
        view = QueryDataView(ontology_id=self.ontology_id, source_manifest_digest=parent_view.content_digest or parent_view.source_manifest_digest,
                             base_view_id=parent_view.id, changeset_digest=payload_hash, changeset_version="scenario-changeset-v1",
                             metadata_digest=parent_view.metadata_digest, graph_key=graph_key, status="building", created_by=self.user.id,
                             retention_until=(now() + timedelta(seconds=scenario.ttl_seconds)) if scenario.ttl_seconds else None)
        self.db.add(view); self.db.flush()
        try:
            self.graph.upsert_instances(graph_key, [{"id": oid, "entity_type": typ, "properties": props} for (typ, oid), props in objects.items()])
            self.graph.upsert_relations(graph_key, [{"source": src[1], "target": dst[1], "type": rel} for src, rel, dst in edges])
            view.object_count, view.edge_count = len(objects), len(edges); view.content_digest = stable_hash({"objects": sorted(objects.items()), "edges": sorted(edges)}); view.status = "ready"
            revision = ScenarioRevision(scenario_id=scenario.id, revision=revision_number, parent_revision=scenario.head_revision,
                                        changeset_id=changeset.id, result_view_id=view.id, content_digest=view.content_digest,
                                        created_by=self.user.id, status="ready")
            self.db.add(revision); self.db.flush()
            changeset.validation_report = {"status": "published", "revision_id": revision.id,
                                           "replace_all": replace_all}
            scenario.head_revision, scenario.etag, scenario.updated_at = revision_number, scenario.etag + 1, now()
            self.db.add(ScenarioAudit(scenario_id=scenario.id, revision=revision_number, actor_id=self.user.id, operation="publish_revision", request_id=request.client_request_id, summary={"payload_hash": payload_hash}))
            self.db.commit(); self.db.refresh(revision)
            return revision
        except Exception:
            self.db.rollback()
            raise

    def list_revisions(self, scenario):
        return self.db.query(ScenarioRevision).filter_by(scenario_id=scenario.id).order_by(ScenarioRevision.revision.desc()).all()


def resolve_scenario_context(db, context: ExecutionContext, ontology_id: str, user) -> ExecutionContext:
    """Resolve a durable scenario pointer into the existing QueryDataView context."""
    if not context.scenario_id:
        return context
    scenario = db.get(ScenarioResource, context.scenario_id)
    if not scenario or scenario.ontology_id != ontology_id:
        raise ScenarioError("not_found", "Scenario context not found", 404, "context.scenario_id")
    if scenario.ttl_seconds and aware(scenario.created_at) + timedelta(seconds=scenario.ttl_seconds) <= now():
        raise ScenarioError('scenario_expired', 'Scenario retention period has expired', 410)
    grant = db.get(ScenarioGrant, (scenario.id, user.id))
    if user.role != "admin" and scenario.owner_id != user.id and not grant:
        raise ScenarioError("forbidden", "Scenario context access denied", 403, "context.scenario_id")
    revision = context.scenario_revision
    view_id = scenario.base_view_id
    if revision:
        row = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=revision, status="ready").first()
        if not row:
            raise ScenarioError("not_found", "Scenario revision not found", 404, "context.scenario_revision")
        view_id = row.result_view_id
    else:
        initial = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=0, status='ready').first()
        if initial:
            view_id = initial.result_view_id
    view = require_ready(db, view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
    return context.model_copy(update={"data_view_id": view.id, "consistency": "snapshot", "revision_policy": "pinned",
                                      "revision_id": f"{scenario.id}:{revision or 0}", "metadata_digest": view.metadata_digest})
