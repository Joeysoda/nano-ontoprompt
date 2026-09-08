"""Semantica preview, source-backed proof and explicit instance-graph projection."""
from __future__ import annotations
import hashlib
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user
from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.logic import LogicRule
from app.models.v2.construction import EvidenceRef
from app.models.v2.reasoning import ReasoningRun
from app.services.v2.graph.falkordb_service import FalkorDBService

router = APIRouter(dependencies=[Depends(get_current_user)])
ATOM = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(([^()]*)\)$")
TERM = re.compile(r"^\??[A-Za-z0-9_:./#-]+$")

class ReasoningRequest(BaseModel):
    facts: list[str] = Field(default_factory=list, max_length=2000)
    rules: list[str] = Field(default_factory=list, max_length=50)
    apply_to_graph: bool = False

def get_db():
    with SessionLocal() as db:
        yield db

def project(db, ontology_id):
    item = db.get(OntologyProject, ontology_id)
    if item is None:
        raise HTTPException(404, "本体不存在")
    return item

def atom(text: str, variables: bool = False) -> str:
    match = ATOM.fullmatch(text.strip())
    if not match:
        raise ValueError(f"无效事实表达式：{text}")
    args = [s.strip() for s in match[2].split(",")]
    if not 1 <= len(args) <= 2 or any(not TERM.fullmatch(s) or (s.startswith("?") and not variables) for s in args):
        raise ValueError(f"仅支持一元/二元谓词及安全标识符：{text}")
    return f"{match[1]}({', '.join(args)})"

def normalize(facts, rules):
    facts = list(dict.fromkeys(atom(f) for f in facts))
    normalized, arities, all_atoms = [], {}, list(facts)
    for rule in rules:
        match = re.fullmatch(r"IF\s+(.+?)\s+THEN\s+(.+)", rule.strip(), re.I)
        if not match:
            raise ValueError(f"规则必须采用 IF … AND … THEN …：{rule}")
        conditions = [atom(s, True) for s in re.split(r"\s+AND\s+", match[1], flags=re.I)]
        conclusion = atom(match[2], True)
        if not set(re.findall(r"\?[\w]+", conclusion)) <= set(re.findall(r"\?[\w]+", " ".join(conditions))):
            raise ValueError("结论含有未在前提中绑定的变量")
        if len(conditions) > 4:
            raise ValueError("单条规则最多四个前提")
        normalized.append(f"IF {' AND '.join(conditions)} THEN {conclusion}")
        all_atoms.extend(conditions + [conclusion])
    for value in all_atoms:
        match = ATOM.fullmatch(value)
        arity = len(match[2].split(','))
        if match[1] in arities and arities[match[1]] != arity:
            raise ValueError(f"谓词参数数量不一致：{match[1]}")
        arities[match[1]] = arity
    if not facts or not normalized:
        raise ValueError("请先加载事实并填写至少一条规则")
    return facts, normalized

@router.get("/{ontology_id}/reasoning/inputs")
def reasoning_inputs(ontology_id: str, db: Session = Depends(get_db)):
    project(db, ontology_id)
    service = FalkorDBService()
    if not service.available:
        raise HTTPException(503, "FalkorDB 不可用，未使用备用内存图")
    data = service.get_graph_data(ontology_id, limit=500)
    # Event-sequenced graphs naturally sort Observation nodes first. For
    # reasoning, that would hide the Machine/Episode/phase endpoints needed by
    # a rule. Add a bounded, relation-first sample so every returned edge has
    # both endpoints and the workbench can test cross-entity rules.
    try:
        graph = service._graph(ontology_id)
        rel_result = graph.query(
            "MATCH (a:Instance)-[r]->(b:Instance) "
            "RETURN a._instance_id, a._type, b._instance_id, b._type, type(r), r LIMIT 500"
        )
        relation_edges = []
        relation_nodes = {}
        for source, source_type, target, target_type, rel_type, rel in rel_result.result_set:
            relation_nodes[str(source)] = {"id": str(source), "entity_type": str(source_type or "Entity"), "properties": {}}
            relation_nodes[str(target)] = {"id": str(target), "entity_type": str(target_type or "Entity"), "properties": {}}
            relation_edges.append({"source": str(source), "target": str(target), "type": str(rel_type), "properties": dict(getattr(rel, "properties", {}) or {})})
        if relation_edges:
            data["nodes"] = list(relation_nodes.values())[:500]
            selected_ids = {n["id"] for n in data["nodes"]}
            data["edges"] = [e for e in relation_edges if e["source"] in selected_ids and e["target"] in selected_ids]
    except Exception:
        # The normal bounded graph response remains a valid fallback if a
        # graph version lacks relation scans.
        pass
    refs = {}
    for row in db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id).all():
        refs.setdefault(row.assertion_id, []).append({
            "evidence_ref_id": row.id, "source_file": row.source_file,
            "source_row": row.source_row_id, "source_version": row.source_version,
            "source_dataset_id": row.source_dataset_id, "source": "source_record",
            "evidence_text": row.evidence_text, "content_hash": row.content_hash,
        })
    evidence = {}
    def add(fact, assertion_id):
        evidence[fact] = [{**r, "fact": fact} for r in refs.get(assertion_id, [])] or [{"fact": fact, "source": "graph_without_source_reference"}]
    for node in data.get("nodes", []):
        try:
            add(atom(f"{node['entity_type']}({node['id']})"), node["id"])
        except ValueError:
            continue
    for edge in data.get("edges", []):
        if (edge.get("properties") or {}).get("derived"):
            continue
        fact = atom(f"{edge['type']}({edge['source']}, {edge['target']})")
        add(fact, (edge.get("properties") or {}).get("assertion_id", fact))
    rules = [r.formula for r in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id, LogicRule.enabled.is_(True)).order_by(LogicRule.id).all()
             if (r.formula or '').startswith('IF ') and re.fullmatch(r'IF\s+[^<>]+\([^<>]+\)(?:\s+AND\s+[^<>]+\([^<>]+\))*\s+THEN\s+[^<>]+\([^<>]+\)', r.formula.strip(), re.I)]
    return {"facts": list(evidence), "evidence": evidence, "rules": rules, "available": True,
            "node_count": len(data['nodes']), "edge_count": len(data['edges']), "sample_limit": 500,
            "total_instances": data.get('total_instances'), "truncated": data.get('total_instances', 0) > len(data['nodes'])}

def infer(facts, rules, evidence):
    from semantica.reasoning import Reasoner
    # Capture ONE grounded proof before upstream merges alternative premises.
    class TracedReasoner(Reasoner):
        def __init__(self):
            super().__init__(max_iterations=50)
            self.proofs = {}
        def _match_rule(self, rule):
            matches = super()._match_rule(rule)
            if len(matches) > 10000 or len(self.facts) > 10000:
                raise ValueError("推理超出工作台规模限制，未保存结果")
            for conclusion, premises, bindings in matches:
                if conclusion not in self.facts and conclusion not in self.proofs:
                    self.proofs[conclusion] = (rule, list(premises), dict(bindings))
            return matches
    engine = TracedReasoner()
    results = engine.infer_with_results(facts, rules)
    if any(c not in engine.facts for r in engine.rules for c, _, _ in engine._match_rule(r)):
        raise ValueError("推理达到迭代上限，尚未收敛，未保存结果")
    proofs = {}
    for result in results:
        rule, premises, bindings = engine.proofs[result.conclusion]
        proofs[result.conclusion] = {
            "conclusion": result.conclusion, "rule_id": rule.rule_id,
            "rule": f"IF {' AND '.join(rule.conditions)} THEN {rule.conclusion}",
            "premises": premises, "bindings": bindings, "proof_scope": "one_grounded_derivation", "evidence": [],
        }
    def leaves(fact, seen):
        if fact in seen:
            raise ValueError("推理证据循环")
        if fact in proofs:
            return [e for p in proofs[fact]['premises'] for e in leaves(p, seen | {fact})]
        return evidence.get(fact) or [{"fact": fact, "source": "manual_input"}]
    for fact, proof in proofs.items():
        proof['evidence'] = list({(e.get('evidence_ref_id'), e['fact']): e for e in leaves(fact, set())}.values())
    return list(proofs.values())

def _project_edges(ontology_id, run_id, conclusions):
    service = FalkorDBService()
    if not service.available:
        return {"status": "unavailable", "written": 0, "reason": "FalkorDB 不可用；预览记录已保留，可重试"}
    rows = []
    for item in conclusions:
        match = ATOM.fullmatch(item['conclusion'])
        args = [s.strip() for s in match[2].split(',')]
        if len(args) == 2:
            rows.append({"source": args[0], "target": args[1], "predicate": match[1],
                         "fact_id": hashlib.sha256(f"{run_id}:{item['conclusion']}".encode()).hexdigest(),
                         "conclusion": item['conclusion']})
    graph = service._graph(ontology_id)
    ids = sorted({s for row in rows for s in (row['source'], row['target'])})
    existing = graph.query("MATCH (n:Instance) WHERE n._instance_id IN $ids RETURN n._instance_id", params={"ids": ids}) if ids else None
    missing = sorted(set(ids) - {str(r[0]) for r in existing.result_set}) if existing else ids
    if missing:
        return {"status": "rejected", "written": 0, "reason": "关系端点不存在；没有创建实体或写入任何关系", "missing_nodes": missing}
    # Dedicated edges preserve original assertions. Stable IDs allow retries.
    if rows:
        graph.query("UNWIND $rows AS row MATCH (a:Instance {_instance_id: row.source}), (b:Instance {_instance_id: row.target}) "
                    "MERGE (a)-[r:INFERRED {fact_id: row.fact_id}]->(b) "
                    "SET r.derived = true, r.predicate = row.predicate, r.conclusion = row.conclusion, r.reasoning_run_id = $run_id",
                    params={"rows": rows, "run_id": run_id})
    return {"status": "completed", "written": len(rows), "stored_facts": len(conclusions), "unary_facts": len(conclusions) - len(rows)}

def response(run):
    payload = run.payload or {}
    return {"run_id": run.id, "status": run.status, **payload, "inferred_facts": payload.get('conclusions', []),
            "derived_fact_count": len(payload.get('conclusions', [])), "graph": payload.get('graph', {"status": "preview", "written": 0}),
            "created_at": run.created_at.isoformat() if run.created_at else None}

@router.post("/{ontology_id}/reasoning/run")
def run_reasoning(ontology_id: str, body: ReasoningRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ontology = project(db, ontology_id)
    try:
        facts, rules = normalize(body.facts, body.rules)
        inputs = reasoning_inputs(ontology_id, db)
        conclusions = infer(facts, rules, inputs['evidence'])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Semantica 推理失败，未保存结果：{exc}")
    run = ReasoningRun(ontology_id=ontology_id, created_by=user.id, status='preview', payload={
        "facts": facts, "rules": rules, "conclusions": conclusions, "ontology_revision_id": ontology.current_revision_id,
        "rules_fired": sorted({r['rule_id'] for r in conclusions}), "engine": "semantica.reasoning.Reasoner", "proof_scope": "one_grounded_derivation",
    })
    db.add(run)
    db.commit()
    if body.apply_to_graph:
        apply_reasoning(ontology_id, run.id, db, user)
    return response(run)

@router.get("/{ontology_id}/reasoning/runs")
def list_runs(ontology_id: str, db: Session = Depends(get_db)):
    project(db, ontology_id)
    runs = db.query(ReasoningRun).filter(ReasoningRun.ontology_id == ontology_id).order_by(ReasoningRun.created_at.desc()).limit(30).all()
    return [{"run_id": r.id, "status": r.status, "created_at": r.created_at.isoformat(), "derived_fact_count": len((r.payload or {}).get('conclusions', []))} for r in runs]

@router.get("/{ontology_id}/reasoning/graph")
def instance_graph(ontology_id: str, db: Session = Depends(get_db)):
    project(db, ontology_id)
    service = FalkorDBService()
    data = service.get_graph_data(ontology_id, limit=500)
    # Keep derived relationships visible even when a temporal graph has more
    # Observation nodes than the browser limit. The default event-order sample
    # can otherwise omit ProcessPhase/ToolCondition endpoints entirely.
    try:
        graph = service._graph(ontology_id)
        result = graph.query("MATCH (a:Instance)-[r]->(b:Instance) RETURN a, b, type(r), r ORDER BY coalesce(r.derived, false) DESC LIMIT 500")
        nodes, edges = {}, []
        for a, b, rel_type, rel in result.result_set:
            for node in (a, b):
                props = dict(getattr(node, "properties", {}) or {})
                node_id = str(props.get("_instance_id") or "")
                if node_id:
                    nodes[node_id] = {"id": node_id, "entity_type": props.get("_type") or "Entity", "properties": {k: v for k, v in props.items() if not str(k).startswith("_")}}
            ap = dict(getattr(a, "properties", {}) or {}); bp = dict(getattr(b, "properties", {}) or {})
            rp = dict(getattr(rel, "properties", {}) or {})
            edges.append({"id": f"{ap.get('_instance_id')}:{rel_type}:{bp.get('_instance_id')}", "source": ap.get("_instance_id"), "target": bp.get("_instance_id"), "type": str(rel_type), "label": str(rel_type), "properties": rp})
        if edges:
            data["nodes"], data["edges"] = list(nodes.values()), edges
    except Exception:
        pass
    for edge in data.get('edges', []):
        props = edge.get('properties') or {}
        edge['id'] = props.get('fact_id') or edge['id']
        edge['label'] = props.get('predicate') or edge['type']
    return data

@router.post("/{ontology_id}/reasoning/runs/{run_id}/apply")
def apply_reasoning(ontology_id: str, run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = db.query(ReasoningRun).filter_by(id=run_id, ontology_id=ontology_id).first()
    if not run:
        raise HTTPException(404, "推理运行不存在")
    if run.status != 'applied':
        graph = _project_edges(ontology_id, run.id, (run.payload or {}).get('conclusions', []))
        run.payload = {**run.payload, "graph": graph}
        if graph['status'] == 'completed':
            run.status = 'applied'
        db.commit()
    return response(run)

@router.get("/{ontology_id}/reasoning/runs/{run_id}")
def get_reasoning(ontology_id: str, run_id: str, db: Session = Depends(get_db)):
    run = db.query(ReasoningRun).filter_by(id=run_id, ontology_id=ontology_id).first()
    if not run:
        raise HTTPException(404, "推理运行不存在")
    return response(run)
