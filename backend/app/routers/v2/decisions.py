"""Ontology-scoped decisions and complete causal-analyzer operations."""
from datetime import datetime, timezone
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.v2.decision import DecisionRecord, DecisionLink
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
