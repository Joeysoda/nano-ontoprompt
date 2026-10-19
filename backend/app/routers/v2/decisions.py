"""Ontology-scoped decisions and complete causal-analyzer operations."""
from datetime import datetime, timezone
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.v2.decision import (DecisionRecord, DecisionLink, DecisionPackage,
    DecisionPackageAction, DecisionPackageEvidence, DecisionPackageEvent, DecisionOutbox)
from app.models.v2.scenario import ScenarioResource, ScenarioRun, ScenarioRunArtifact
from app.services.v2 import decision_packages as package_service
from app.services.v2.decision_packages import DecisionPackageError, package_digest, stable_digest, transition
from app.models.v2.reasoning import ReasoningRun
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.graph.decision_analysis import DecisionGraphAdapter, analyze

router = APIRouter(dependencies=[Depends(get_current_user)])


def scope(db, oid, user):
    obj = db.get(OntologyProject, oid)
    if not obj or (user.role != 'admin' and obj.created_by != user.id):
        raise HTTPException(404, '本体不存在或无访问权限')
    return obj


class Basis(BaseModel):
    run_id: str
    conclusion: str


class DecisionInput(BaseModel):
    category: str = ''
    scenario: str = Field(min_length=1, max_length=4000)
    reasoning: str = ''
    outcome: str = ''
    confidence: float = Field(default=0.0, ge=0, le=1)
    decision_maker: str = ''
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: str | None = None
    valid_until: str | None = None
    reasoning_embedding: list[float] | None = None
    node2vec_embedding: list[float] | None = None
    metadata: dict = Field(default_factory=dict)
    policy_ids: list[str] = Field(default_factory=list, max_length=50)
    exceptions: list[dict] = Field(default_factory=list, max_length=50)
    approval_chain: list[dict] = Field(default_factory=list, max_length=50)
    cross_system_context: dict = Field(default_factory=dict)
    entity_ids: list[str] = Field(default_factory=list, max_length=100)
    basis: list[Basis] = Field(default_factory=list, max_length=100)
    test_record: bool = False


class LinkInput(BaseModel):
    source: str
    target: str
    relationship: Literal['CAUSED', 'INFLUENCED', 'PRECEDENT_FOR']
    explanation: str = Field(min_length=1, max_length=4000)
    weight: float = Field(default=1, ge=0, le=1)


class AnalysisInput(BaseModel):
    operation: Literal['chain', 'time', 'influenced', 'precedents', 'roots', 'loops', 'score', 'network', 'distance']
    decision_id: str | None = None
    target_id: str | None = None
    direction: Literal['upstream', 'downstream'] = 'downstream'
    max_depth: int = Field(default=5, ge=1, le=20)
    at_time: datetime | None = None


class ContextInput(BaseModel):
    policy_ids: list[str] | None = None
    exceptions: list[dict] | None = None
    approval_chain: list[dict] | None = None
    cross_system_context: dict | None = None


class PackageActionInput(BaseModel):
    action_type_id: str = Field(min_length=1, max_length=100)
    invocation: dict
    expected_edits: list[dict] = Field(default_factory=list, max_length=500)
    external_effect_class: Literal["none", "reversible", "irreversible"] = "none"


class PackageEvidenceInput(BaseModel):
    evidence_type: Literal["artifact", "impact_path", "metric", "constraint", "object"]
    reference: dict
    # Run artifacts use the canonical "sha256:<hex>" form; packages store the bare hex.
    content_digest: str = Field(pattern=r"^(sha256:)?[0-9a-f]{64}$")

    @field_validator("content_digest")
    @classmethod
    def _bare_digest(cls, value: str) -> str:
        return value.removeprefix("sha256:")


class PackageCreateInput(BaseModel):
    scenario_id: str
    scenario_revision: int = Field(ge=0)
    run_id: str
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=10000)
    actions: list[PackageActionInput] = Field(min_length=1, max_length=30)
    evidence: list[PackageEvidenceInput] = Field(min_length=1, max_length=100)


class PackageFromRunInput(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    rationale: str = Field(min_length=1, max_length=10000)


class PackageCommandInput(BaseModel):
    expected_etag: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=200)


class PackageTransitionInput(BaseModel):
    # Apply and revert are not plain transitions: they run through :apply / :revert,
    # which perform the Scenario merge, so a package cannot be marked applied by hand.
    event: Literal["submit", "approve", "reject", "revise", "supersede"]
    expected_etag: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=4000)


def package_json(db, item):
    actions = db.query(DecisionPackageAction).filter_by(package_id=item.id).order_by(DecisionPackageAction.sequence).all()
    evidence = db.query(DecisionPackageEvidence).filter_by(package_id=item.id).all()
    events = db.query(DecisionPackageEvent).filter_by(package_id=item.id).order_by(DecisionPackageEvent.sequence).all()
    outbox = db.query(DecisionOutbox).filter_by(package_id=item.id).all()
    return {"id": item.id, "scenario_id": item.scenario_id, "scenario_revision": item.scenario_revision,
            "run_id": item.run_id, "title": item.title, "rationale": item.rationale, "status": item.status,
            "content_digest": item.content_digest, "preview_digest": item.preview_digest, "etag": item.etag,
            "created_by": item.created_by, "reviewed_by": item.reviewed_by, "applied_decision_id": item.applied_decision_id,
            "outbox": [{"id": row.id, "action_id": row.action_id, "delivered": row.delivered, "attempts": row.attempts} for row in outbox],
            "actions": [{"id": row.id, "sequence": row.sequence, "action_type_id": row.action_type_id,
                         "invocation": row.invocation, "expected_edits": row.expected_edits,
                         "external_effect_class": row.external_effect_class, "receipt": row.receipt} for row in actions],
            "evidence": [{"id": row.id, "evidence_type": row.evidence_type, "reference": row.reference,
                          "content_digest": row.content_digest} for row in evidence],
            "events": [{"sequence": row.sequence, "event_type": row.event_type, "actor_id": row.actor_id,
                        "reason": row.reason, "request_id": row.request_id} for row in events]}


@router.get('/{oid}/decision-packages')
def list_packages(oid: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    return {"packages": [package_json(db, row) for row in db.query(DecisionPackage).filter_by(ontology_id=oid).order_by(DecisionPackage.created_at.desc()).all()]}


@router.post('/{oid}/decision-packages')
def create_package(oid: str, body: PackageCreateInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    scenario = db.query(ScenarioResource).filter_by(id=body.scenario_id, ontology_id=oid).first()
    run = db.query(ScenarioRun).filter_by(id=body.run_id, scenario_id=body.scenario_id).first()
    if not scenario or not run:
        raise HTTPException(422, "Scenario or Run does not belong to this ontology")
    if run.status != "completed" or run.revision != body.scenario_revision or scenario.head_revision != body.scenario_revision:
        raise HTTPException(409, "Run is incomplete or its Scenario revision is stale")
    artifacts = {row.id: row for row in db.query(ScenarioRunArtifact).filter_by(run_id=run.id).all()}
    for evidence in body.evidence:
        if evidence.evidence_type in {"artifact", "impact_path"}:
            artifact_id = str(evidence.reference.get("artifact_id", ""))
            if artifact_id not in artifacts or artifacts[artifact_id].content_digest.removeprefix("sha256:") != evidence.content_digest:
                raise HTTPException(422, "Evidence artifact or digest is invalid")
    return _insert_package(db, oid, user, scenario_id=body.scenario_id, scenario_revision=body.scenario_revision, run_id=body.run_id,
                           title=body.title, rationale=body.rationale, actions=[row.model_dump(mode="json") for row in body.actions],
                           evidence=[row.model_dump(mode="json") for row in body.evidence])


def _insert_package(db, oid, user, *, scenario_id, scenario_revision, run_id, title, rationale, actions, evidence):
    digest = package_digest(ontology_id=oid, scenario_id=scenario_id, scenario_revision=scenario_revision,
        run_id=run_id, title=title, rationale=rationale, actions=actions, evidence=evidence)
    item = DecisionPackage(ontology_id=oid, scenario_id=scenario_id, scenario_revision=scenario_revision,
        run_id=run_id, title=title, rationale=rationale, content_digest=digest,
        preview_digest=stable_digest([row["expected_edits"] for row in actions]), created_by=user.id)
    db.add(item); db.flush()
    for sequence, row in enumerate(actions):
        db.add(DecisionPackageAction(package_id=item.id, sequence=sequence, **row))
    for row in evidence:
        db.add(DecisionPackageEvidence(package_id=item.id, **row))
    db.add(DecisionPackageEvent(package_id=item.id, sequence=0, event_type="created", actor_id=user.id,
        reason="", request_id=f"create:{item.id}", after_digest=digest))
    db.commit()
    return package_json(db, item)


def _package_error(exc: DecisionPackageError):
    raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post('/{oid}/scenario-study-runs/{run_id}/decision-packages')
def create_package_from_run(oid: str, run_id: str, body: PackageFromRunInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    """Create a draft whose edits the server compiles from the run's pinned data view."""
    scope(db, oid, user)
    run = db.get(ScenarioRun, run_id)
    scenario = db.query(ScenarioResource).filter_by(id=run.scenario_id, ontology_id=oid).first() if run else None
    if not run or not scenario:
        raise HTTPException(404, {"code": "run_not_found", "message": "Run not found"})
    if run.status != "completed":
        raise HTTPException(409, {"code": "run_not_complete", "message": "Run is not complete"})
    if scenario.head_revision != run.revision:
        raise HTTPException(409, {"code": "stale_scenario_revision", "message": "Scenario changed after this run"})
    try:
        actions, evidence, label = package_service.compile_package_actions(db, run, oid, body.candidate_id, user)
    except DecisionPackageError as exc:
        _package_error(exc)
    return _insert_package(db, oid, user, scenario_id=scenario.id, scenario_revision=run.revision, run_id=run.id,
                           title=body.title or label, rationale=body.rationale, actions=actions, evidence=evidence)


def _locked_package(db, oid, package_id):
    item = db.query(DecisionPackage).filter_by(id=package_id, ontology_id=oid).with_for_update().first()
    if not item:
        raise HTTPException(404, "Decision package not found")
    return item


@router.post('/{oid}/decision-packages/{package_id}:apply')
def apply_package(oid: str, package_id: str, body: PackageCommandInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    item = _locked_package(db, oid, package_id)
    try:
        delivery = package_service.apply_package(db, item, user, expected_etag=body.expected_etag, request_id=body.request_id)
    except DecisionPackageError as exc:
        _package_error(exc)
    return {"package": package_json(db, db.get(DecisionPackage, package_id)), "delivery": delivery}


@router.post('/{oid}/decision-packages/{package_id}:deliver-outbox')
def deliver_package_outbox(oid: str, package_id: str, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    item = _locked_package(db, oid, package_id)
    if item.status != "applied":
        raise HTTPException(409, {"code": "invalid_transition", "message": "Only applied packages have external effects"})
    return {"package": package_json(db, item), "delivery": package_service.deliver_outbox(db, item)}


@router.post('/{oid}/decision-packages/{package_id}:revert')
def revert_package(oid: str, package_id: str, body: PackageCommandInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    item = _locked_package(db, oid, package_id)
    try:
        result = package_service.revert_package(db, item, user, expected_etag=body.expected_etag, request_id=body.request_id)
    except DecisionPackageError as exc:
        _package_error(exc)
    return {"package": package_json(db, db.get(DecisionPackage, package_id)), **result}


@router.post('/{oid}/decision-packages/{package_id}/transition')
def transition_package(oid: str, package_id: str, body: PackageTransitionInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    item = db.query(DecisionPackage).filter_by(id=package_id, ontology_id=oid).with_for_update().first()
    if not item:
        raise HTTPException(404, "Decision package not found")
    duplicate = db.query(DecisionPackageEvent).filter_by(package_id=item.id, request_id=body.request_id).first()
    if duplicate:
        return package_json(db, item)
    try:
        before, target = transition(item, body.event, user.id, expected_etag=body.expected_etag, reason=body.reason)
    except DecisionPackageError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    sequence = db.query(DecisionPackageEvent).filter_by(package_id=item.id).count()
    db.add(DecisionPackageEvent(package_id=item.id, sequence=sequence, event_type=body.event, actor_id=user.id,
        reason=body.reason, request_id=body.request_id, before_digest=before, after_digest=item.content_digest))
    db.commit()
    return package_json(db, item)


def records(db, oid):
    return db.query(DecisionRecord).filter_by(ontology_id=oid).all()


def links(db, oid):
    return db.query(DecisionLink).filter_by(ontology_id=oid).all()


def project_graph(db, oid):
    adapter = DecisionGraphAdapter(oid)
    for record in records(db, oid):
        p = record.payload
        adapter.execute_query('MERGE (d:Decision {decision_id: $id}) SET d.scenario=$scenario, d.category=$category, d.reasoning=$reasoning, d.outcome=$outcome, d.confidence=$confidence, d.timestamp=$timestamp, d.decision_maker=$maker, d.valid_from=$valid_from, d.valid_until=$valid_until, d.policy_ids=$policy_ids, d.approval_chain_json=$approval_chain_json',
                              {'id': record.id, **{k: p.get(k) for k in ('scenario','category','reasoning','outcome','confidence','timestamp','valid_from','valid_until','policy_ids')}, 'maker': p.get('decision_maker', ''), 'approval_chain_json': json.dumps(p.get('approval_chain', []), ensure_ascii=False)})
        for entity_id in p.get('entity_ids', []):
            adapter.execute_query('MATCH (d:Decision {decision_id:$decision}), (e:Instance {_instance_id:$entity}) MERGE (d)-[:ABOUT {entity_id:$entity}]->(e)', {'decision': record.id, 'entity': entity_id})
    for edge in links(db, oid):
        p = edge.payload
        kind = p['relationship']
        if kind not in ('CAUSED', 'INFLUENCED', 'PRECEDENT_FOR'):
            raise ValueError('无效决策关系')
        adapter.execute_query(f'MATCH (a:Decision {{decision_id:$source}}), (b:Decision {{decision_id:$target}}) MERGE (a)-[r:{kind} {{link_id:$id}}]->(b) SET r.weight=$weight, r.recorded_at=$recorded_at, r.type=$kind',
                              {'source': edge.source, 'target': edge.target, 'id': edge.id, 'weight': p['weight'], 'recorded_at': p['recorded_at'], 'kind': kind})


@router.get('/{oid}/decisions')
def list_decisions(oid: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    return {'decisions': [{'id': r.id, **r.payload} for r in records(db, oid)],
            'links': [{'id': r.id, 'source': r.source, 'target': r.target, **r.payload} for r in links(db, oid)]}


@router.post('/{oid}/decisions')
def create_decision(oid: str, body: DecisionInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    ontology = scope(db, oid, user)
    proofs = []
    for basis in body.basis:
        run = db.query(ReasoningRun).filter_by(id=basis.run_id, ontology_id=oid).first()
        proof = next((c for c in (run.payload or {}).get('conclusions', []) if c['conclusion'] == basis.conclusion), None) if run else None
        if not proof:
            raise HTTPException(422, '推理依据不存在于指定运行中')
        proofs.append({'run_id': run.id, 'run_status_at_link': run.status, **proof})
    if body.entity_ids:
        service = FalkorDBService()
        if not service.available:
            raise HTTPException(503, '无法验证业务对象：FalkorDB 不可用')
        found = service._graph(oid).query('MATCH (n:Instance) WHERE n._instance_id IN $ids RETURN n._instance_id', params={'ids': body.entity_ids})
        if set(body.entity_ids) != {r[0] for r in found.result_set}:
            raise HTTPException(422, '关联业务对象不存在')
    payload = body.model_dump(mode='json')
    payload.update(evidence=proofs, ontology_revision_id=ontology.current_revision_id, created_by=user.id)
    item = DecisionRecord(ontology_id=oid, payload=payload)
    db.add(item)
    db.commit()
    return {'id': item.id, **payload}


@router.post('/{oid}/decisions/{decision_id}/context')
def update_context(oid: str, decision_id: str, body: ContextInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    item = db.query(DecisionRecord).filter_by(id=decision_id, ontology_id=oid).first()
    if not item:
        raise HTTPException(404, '决策不存在')
    payload = dict(item.payload)
    for key, value in body.model_dump(exclude_none=True).items():
        payload[key] = value
    payload['context_updated_by'] = user.id
    item.payload = payload
    db.commit()
    return {'id': item.id, **payload}


@router.post('/{oid}/decisions/links')
def create_link(oid: str, body: LinkInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    if body.source == body.target:
        raise HTTPException(422, '不能关联到自身')
    for ident in (body.source, body.target):
        if not db.query(DecisionRecord).filter_by(id=ident, ontology_id=oid).first():
            raise HTTPException(422, '决策端点不存在')
    payload = body.model_dump(exclude={'source','target'})
    payload.update(recorded_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), created_by=user.id)
    item = DecisionLink(ontology_id=oid, source=body.source, target=body.target, payload=payload)
    db.add(item)
    db.commit()
    return {'id': item.id, **payload}


@router.post('/{oid}/decisions/analyze')
def analysis(oid: str, body: AnalysisInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    ids = {r.id for r in records(db, oid)}
    if body.operation not in ('network', 'loops') and body.decision_id not in ids:
        raise HTTPException(422, '请选择有效决策')
    if body.operation == 'distance' and body.target_id not in ids:
        raise HTTPException(422, '请选择目标决策')
    try:
        project_graph(db, oid)
        response = analyze(oid, **body.model_dump())
        selected_ids = {body.decision_id} if body.decision_id else set()
        if isinstance(response.get('result'), list):
            selected_ids.update(x.get('decision_id') for x in response['result'] if isinstance(x, dict) and x.get('decision_id'))
        if isinstance(response.get('result'), dict) and response['result'].get('causal_path'):
            selected_ids.update(response['result']['causal_path'])
        if body.target_id:
            selected_ids.add(body.target_id)
        selected_ids.discard(None)
        path_pairs = set()
        if isinstance(response.get('result'), dict) and response['result'].get('causal_path'):
            path = response['result']['causal_path']
            path_pairs = set(zip(path, path[1:]))
        response['highlight'] = {'nodes': sorted(selected_ids), 'edges': [
            {'id': link.id, 'source': link.source, 'target': link.target, 'relationship': link.payload.get('relationship'),
             'weight': link.payload.get('weight', 1), 'highlight': True}
            for link in links(db, oid)
            if ((link.source, link.target) in path_pairs if path_pairs else link.source in selected_ids and link.target in selected_ids)
        ]}
        return response
    except Exception as exc:
        raise HTTPException(503, f'决策分析失败：{exc}') from exc
