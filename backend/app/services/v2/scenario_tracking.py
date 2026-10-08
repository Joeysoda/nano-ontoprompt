"""Three-way overlay rebase. Existing revision resources remain immutable."""
from copy import deepcopy
from uuid import uuid4
from app.models.v2.scenario import ScenarioResource, ScenarioRevision, ScenarioChangeSet, ScenarioAudit
from app.models.v2.query_view import QueryDataView
from app.schemas.v2.scenario import Edit, Target, LinkTarget, RevisionRequest
from app.services.v2.scenarios import ScenarioError
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.object_query.data_views import require_ready, now, build_live_view
from app.services.v2.object_query.metadata import load_sql_metadata


def rebase_edits(ontology_id, base, candidate, live, preserved=()):
    """Keep the user's delta; reject overlapping live changes instead of replaying Functions."""
    missing = object()
    edits, conflicts = [], []
    def target(key):
        return Target(ontology_id=ontology_id, concrete_type=key[0], object_id=key[1])
    def add(**kwargs):
        edits.append(Edit(sequence=len(edits), **kwargs))
    def public(values):
        return {key: value for key, value in values.items() if not key.startswith('_')}
    preserved_properties = {
        ((item.get('target') or {}).get('concrete_type'), (item.get('target') or {}).get('object_id'), item.get('property')): item
        for item in preserved if item.get('op') in {'set_property', 'unset_property'}
    }
    for key in sorted(set(base.objects) | set(candidate.objects)):
        before = public(base.objects[key]) if key in base.objects else missing
        after = public(candidate.objects[key]) if key in candidate.objects else missing
        current = public(live.objects[key]) if key in live.objects else missing
        if before == after and not any(item[:2] == key for item in preserved_properties):
            continue
        if before is missing:
            if current is not missing:
                conflicts.append({'object_type': key[0], 'object_id': key[1], 'reason': 'created_object_collision'})
            else:
                add(op='create_object', target=target(key), object_type=key[0], properties=deepcopy(after))
        elif after is missing:
            if current is missing:
                continue
            incoming = {edge for edge in live.edges if key in (edge[0], edge[2])}
            original = {edge for edge in base.edges if key in (edge[0], edge[2])}
            if current != before or incoming != original:
                conflicts.append({'object_type': key[0], 'object_id': key[1], 'reason': 'deleted_object_changed'})
            else:
                add(op='delete_object', target=target(key))
        elif current is missing:
            conflicts.append({'object_type': key[0], 'object_id': key[1], 'reason': 'target_deleted'})
        else:
            for prop in sorted(set(before) | set(after) | {item[2] for item in preserved_properties if item[:2] == key}):
                old, new, actual = before.get(prop, missing), after.get(prop, missing), current.get(prop, missing)
                explicit = preserved_properties.get((key[0], key[1], prop))
                if old == new and not explicit:
                    continue
                if actual == new:
                    if explicit:
                        add(op='unset_property' if new is missing else 'set_property', target=target(key), property=prop,
                            value=None if new is missing else deepcopy(new), expected_old_value=None if actual is missing else actual)
                    continue
                if actual != old:
                    conflicts.append({'object_type': key[0], 'object_id': key[1], 'property': prop, 'reason': 'property_changed'})
                else:
                    add(op='unset_property' if new is missing else 'set_property', target=target(key), property=prop,
                        value=None if new is missing else deepcopy(new), expected_old_value=None if actual is missing else actual)
    base_edges, candidate_edges, live_edges = set(base.edges), set(candidate.edges), set(live.edges)
    created = {key for key in candidate.objects if key not in base.objects}
    deleted = {key for key in base.objects if key not in candidate.objects}
    available = (set(live.objects) | created) - deleted
    for op, edges in [('add_link', candidate_edges - base_edges), ('remove_link', base_edges - candidate_edges)]:
        for source, relation, dest in sorted(edges):
            edge = (source, relation, dest)
            if op == 'add_link' and edge in live_edges or op == 'remove_link' and edge not in live_edges:
                continue
            if source in deleted or dest in deleted:
                continue
            if source not in available or dest not in available:
                conflicts.append({'relation_type': relation, 'reason': 'link_endpoint_deleted'})
            else:
                add(op=op, link=LinkTarget(ontology_id=ontology_id, relation_type=relation, source=target(source), target=target(dest)))
    return edits, conflicts


def rebase(service, scenario_id, expected_etag):
    db = service.db
    scenario = service.get(scenario_id, write=True)
    if scenario.mode != 'tracking' or scenario.protected_demo or scenario.status != 'active':
        raise ScenarioError('rebase_unavailable', 'Only active Tracking Scenarios can rebase', 409)
    if scenario.etag != expected_etag:
        raise ScenarioError('etag_conflict', 'Scenario changed before rebase', 409)
    if not service.graph.available:
        raise ScenarioError('graph_unavailable', 'FalkorDB is unavailable', 503)
    old_base_id, old_revision = scenario.base_view_id, scenario.head_revision
    def read(view_id):
        view = require_ready(db, view_id, service.ontology_id, principal_id=service.user.id, is_admin=service.user.role == 'admin')
        return FalkorReadAdapter(service.graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(service.ontology_id)
    base = read(old_base_id)
    head = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=old_revision).first() if old_revision else None
    candidate = read(head.result_view_id) if head else base
    live = FalkorReadAdapter(service.graph._graph(service.ontology_id)).read(service.ontology_id)
    if base.objects == live.objects and set(base.edges) == set(live.edges):
        scenario.last_rebased_at = now()
        scenario.rebase_error = None
        db.commit()
        return {'status': 'unchanged', 'revision': old_revision, 'base_view_id': old_base_id, 'conflicts': []}
    live_view = build_live_view(db, service.graph, load_sql_metadata(db, service.ontology_id), service.ontology_id,
                               service.user.id, f'tracking:{scenario.id}:{uuid4()}', scenario.ttl_seconds or 30 * 86400)
    # Capture commits its independent immutable view. Lock and recheck the
    # Scenario after capture so another writer cannot be overwritten.
    scenario = db.query(ScenarioResource).filter_by(id=scenario_id).populate_existing().with_for_update().one()
    if scenario.etag != expected_etag or scenario.base_view_id != old_base_id:
        raise ScenarioError('etag_conflict', 'Scenario changed during live capture', 409)
    previous = db.get(ScenarioChangeSet, head.changeset_id) if head and head.changeset_id else None
    preserved = previous.ordered_edits if previous and previous.validation_report.get('replace_all') else []
    edits, conflicts = rebase_edits(service.ontology_id, base, candidate, read(live_view.id), preserved)
    if conflicts:
        scenario.rebase_error = {'code': 'rebase_conflict', 'conflicts': conflicts}
        db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=service.user.id, operation='rebase_conflict', summary=scenario.rebase_error))
        db.commit()
        return {'status': 'conflict', 'conflicts': conflicts, 'revision': old_revision}
    if not db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=0).first():
        initial = db.get(QueryDataView, old_base_id)
        db.add(ScenarioRevision(scenario_id=scenario.id, revision=0, result_view_id=initial.id,
                               content_digest=initial.content_digest or initial.source_manifest_digest, created_by=service.user.id))
    scenario.base_view_id = live_view.id
    scenario.last_rebased_at = now()
    scenario.rebase_error = None
    if not edits:
        edits = [Edit(sequence=0, op='invoke_action', action_key='scenario_reset')]
    db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=service.user.id, operation='rebase', summary={'old_base_view_id': old_base_id, 'base_view_id': live_view.id}))
    revision = service.revision(scenario, RevisionRequest(base_revision=old_revision, expected_etag=expected_etag,
                               client_request_id=f'rebase:{uuid4()}', edits=edits), replace_all=True)
    return {'status': 'rebased', 'revision': revision.revision, 'base_view_id': live_view.id, 'conflicts': []}
