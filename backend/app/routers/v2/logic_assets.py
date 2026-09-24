from datetime import datetime, timezone
from time import perf_counter
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.v2.logic_asset import LogicAsset, LogicAssetRun, LogicDerivedFact
from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute
from app.services.v2.logic_contracts import bind_object_fields, normalize_contract, ContractValidationError
from app.services.v2.graph.falkordb_service import FalkorDBService

router = APIRouter()

class RunRequest(BaseModel):
    inputs: dict = Field(default_factory=dict)
    object_id: str | None = None
    subject_id: str | None = None

class PlanStep(BaseModel):
    asset_id: str
    inputs: dict = Field(default_factory=dict)
    input_bindings: dict[str, str] = Field(default_factory=dict)

class PlanRequest(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    subject_id: str | None = None

def _save_derived_facts(db, ontology_id: str, asset: LogicAsset, run: LogicAssetRun, output: dict | None, subject_id: str | None = None):
    if not isinstance(output, dict):
        return []
    facts = []
    for key, value in output.items():
        fact = LogicDerivedFact(ontology_id=ontology_id, run_id=run.id, asset_id=asset.id,
                                subject_id=subject_id, fact_key=key, value=value, source_path=f"output.{key}")
        db.add(fact); facts.append(fact)
    return facts

def _authorized(oid, db, user):
    item=db.get(OntologyProject, oid)
    if not item or (user.role != 'admin' and item.created_by != user.id):
        raise HTTPException(404, '本体不存在或无访问权限')
    return item

def _seed(db, oid):
    rows=[]
    for definition in ASSET_DEFINITIONS:
        row=db.query(LogicAsset).filter_by(ontology_id=oid, asset_key=definition['asset_key']).first()
        if not row:
            row=LogicAsset(ontology_id=oid, **definition)
            db.add(row); db.flush()
        else:
            # Upgrade assets created by the earlier prototype contract in place.
            # This keeps capability discovery deterministic after migration.
            for field in ('interface_key', 'interface_version', 'executor_type',
                          'input_schema', 'output_schema'):
                setattr(row, field, definition[field])
            row.binding_spec = definition.get('binding_spec') or {
                'kind': 'object_fields',
                'fields': {field.split('.')[-1]: field for field in
                           definition.get('bindings', {}).get('ontology_fields', [])},
            }
        rows.append(row)
    db.commit()
    return rows

def _as_dict(row):
    data = {'id':row.id,'asset_key':row.asset_key,'name':row.name,'kind':row.kind,'description':row.description,'implementation':row.implementation,'interface_key':getattr(row, 'interface_key', '') or f"logic.{row.asset_key}",'interface_version':getattr(row, 'interface_version', '1.0.0') or '1.0.0','executor_type':getattr(row, 'executor_type', 'local') or 'local','input_schema':row.input_schema,'output_schema':row.output_schema,'bindings':row.bindings,'binding_spec':getattr(row, 'binding_spec', {}) or {},'config':row.config,'version':row.version,'status':row.status,'deterministic':row.deterministic,'side_effect':row.side_effect}
    return normalize_contract(data)

@router.get('/{oid}/logic-assets')
def list_assets(oid: str, seed: bool = False, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    rows=db.query(LogicAsset).filter_by(ontology_id=oid).order_by(LogicAsset.kind, LogicAsset.asset_key).all()
    if seed: rows=_seed(db, oid)
    return {'assets':[_as_dict(r) for r in rows], 'count':len(rows)}

@router.get('/{oid}/logic-assets/capabilities')
def discover_capabilities(oid: str, interface_key: str | None = None, kind: str | None = None,
                          db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Return published implementations that can satisfy a runtime request.

    This is deliberately deterministic: inference may request an interface,
    while the resolver filters published, side-effect-free assets before any
    execution is attempted.
    """
    _authorized(oid, db, user)
    query = db.query(LogicAsset).filter_by(ontology_id=oid, status='published', side_effect=False)
    if interface_key:
        query = query.filter(LogicAsset.interface_key == interface_key)
    if kind:
        query = query.filter(LogicAsset.kind == kind)
    rows = query.order_by(LogicAsset.interface_key, LogicAsset.version.desc()).all()
    return {'capabilities': [_as_dict(row) for row in rows], 'count': len(rows)}

@router.post('/{oid}/logic-assets/seed')
def seed_assets(oid: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    return {'assets':[_as_dict(r) for r in _seed(db, oid)]}

@router.get('/{oid}/logic-assets/runs')
def list_runs(oid: str, limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    rows=db.query(LogicAssetRun).filter_by(ontology_id=oid).order_by(LogicAssetRun.created_at.desc()).limit(min(limit,100)).all()
    return {'runs':[{'id':r.id,'asset_id':r.asset_id,'asset_version':r.asset_version,'inputs':r.inputs,'output':r.output,'status':r.status,'error':r.error,'trace':r.trace,'duration_ms':r.duration_ms,'created_at':r.created_at.isoformat()} for r in rows]}

@router.post('/{oid}/logic-assets/{asset_id}/run')
def run_asset(oid: str, asset_id: str, request: RunRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    asset=db.query(LogicAsset).filter_by(id=asset_id, ontology_id=oid).first()
    if not asset: raise HTTPException(404, '逻辑资产不存在')
    if asset.status != 'published' or asset.side_effect:
        raise HTTPException(422, '只能执行已发布且无副作用的逻辑资产')
    started=perf_counter(); status='completed'; output=None; error=None; trace=[]
    inputs=dict(request.inputs)
    if request.object_id and not inputs:
        # Explicit, conservative ontology binding: only copy declared fields
        # that are present on the selected graph object; never invent values.
        try:
            graph=FalkorDBService()
            if not graph.available: raise ValueError('FalkorDB 不可用，无法绑定业务对象')
            rows=graph._graph(oid).query('MATCH (n:Instance {_instance_id:$id}) RETURN n', params={'id':request.object_id}).result_set
            if not rows: raise ValueError(f'业务对象不存在: {request.object_id}')
            props=dict(getattr(rows[0][0], 'properties', rows[0][0]))
            inputs, binding_trace = bind_object_fields(_as_dict(asset), props)
            trace.extend([{'stage':'bind_object','object_id':request.object_id}, *binding_trace])
        except Exception as exc:
            status='failed'; error={'type':type(exc).__name__,'message':str(exc)}; trace=[{'stage':'bind_object','status':'failed','error':str(exc)}]
    try:
        if status == 'failed': raise ValueError(error['message'])
        result=execute(_as_dict(asset), inputs)
        output=result['output']; trace=trace + result['trace']
    except ContractValidationError as exc:
        status='failed'; error={'type':type(exc).__name__,'stage':exc.stage,'errors':exc.errors,'message':str(exc)}; trace=trace + [{'stage':exc.stage,'status':'failed','errors':exc.errors}]
    except Exception as exc:
        status='failed'; error={'type':type(exc).__name__,'message':str(exc)}; trace=trace + [{'stage':'validate_or_execute','status':'failed','error':str(exc)}]
    run=LogicAssetRun(asset_id=asset.id, ontology_id=oid, asset_version=asset.version, inputs=inputs, output=output, status=status, error=error, trace=trace, duration_ms=round((perf_counter()-started)*1000))
    db.add(run); db.flush(); _save_derived_facts(db, oid, asset, run, output, request.subject_id or request.object_id); db.commit(); db.refresh(run)
    payload={'run_id':run.id,'asset':_as_dict(asset),'status':status,'output':output,'error':error,'trace':trace,'duration_ms':run.duration_ms}
    if status=='failed': raise HTTPException(422, detail=payload)
    return payload

@router.post('/{oid}/logic-assets/execute-plan')
def execute_plan(oid: str, request: PlanRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Execute a stored sequential plan and pass prior outputs by JSON path.

    This is the first runtime composition layer. It is intentionally explicit;
    it does not generate a workflow or infer arbitrary code.
    """
    _authorized(oid, db, user)
    outputs: list[dict] = []
    step_payloads = []
    for index, step in enumerate(request.steps):
        asset = db.query(LogicAsset).filter_by(id=step.asset_id, ontology_id=oid, status='published', side_effect=False).first()
        if not asset:
            raise HTTPException(404, f'第 {index + 1} 步逻辑资产不存在或未发布')
        inputs = dict(step.inputs)
        for target, reference in step.input_bindings.items():
            if not reference.startswith('$step.'):
                raise HTTPException(422, f'第 {index + 1} 步绑定必须使用 $step.N.output.path')
            parts = reference.split('.')
            if len(parts) < 4 or not parts[1].isascii() or not parts[1].isdigit() or not 0 <= int(parts[1]) < index or parts[2] != 'output':
                raise HTTPException(422, f'第 {index + 1} 步不能引用自身或无效步骤')
            try:
                prior = outputs[int(parts[1])]
                value = prior
                for part in parts[3:]:
                    value = value[part] if isinstance(value, dict) else value[int(part)]
                inputs[target] = value
            except (IndexError, KeyError, ValueError, TypeError) as exc:
                raise HTTPException(422, f'第 {index + 1} 步无法解析绑定 {reference}: {exc}') from exc
        started = perf_counter(); status = 'completed'; output = None; error = None
        try:
            result = execute(_as_dict(asset), inputs); output = result['output']
            trace = result['trace']
        except ContractValidationError as exc:
            status = 'failed'; error = {'type': type(exc).__name__, 'stage': exc.stage, 'errors': exc.errors, 'message': str(exc)}
            trace = [{'stage': exc.stage, 'status': 'failed', 'errors': exc.errors}]
        except Exception as exc:
            status = 'failed'; error = {'type': type(exc).__name__, 'message': str(exc)}
            trace = [{'stage': 'validate_or_execute', 'status': 'failed', 'error': str(exc)}]
        run = LogicAssetRun(asset_id=asset.id, ontology_id=oid, asset_version=asset.version, inputs=inputs,
                            output=output, status=status, error=error, trace=trace,
                            duration_ms=round((perf_counter() - started) * 1000))
        db.add(run); db.flush(); facts = _save_derived_facts(db, oid, asset, run, output, request.subject_id)
        step_payloads.append({'step': index, 'run_id': run.id, 'asset_key': asset.asset_key, 'status': status,
                              'output': output, 'error': error, 'derived_fact_ids': [fact.id for fact in facts]})
        if status == 'failed':
            db.commit()
            return {'status': 'failed', 'failed_step': index, 'steps': step_payloads}
        outputs.append(output)
    db.commit()
    return {'status': 'completed', 'steps': step_payloads, 'outputs': outputs}
