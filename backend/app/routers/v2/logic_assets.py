from datetime import datetime, timezone
from time import perf_counter
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.v2.logic_asset import LogicAsset, LogicAssetRun, LogicDerivedFact
from app.services.v2.logic_assets import ASSET_DEFINITIONS, EXECUTORS, execute
from app.services.v2.function_contracts import resolve_version, version_tuple, execution_class
from app.schemas.v2.object_query import ExecutionContext
from app.services.v2.logic_contracts import bind_object_fields, normalize_contract, ContractValidationError
from app.services.v2.graph.falkordb_service import FalkorDBService

router = APIRouter()

class RunRequest(BaseModel):
    inputs: dict = Field(default_factory=dict)
    object_id: str | None = None
    subject_id: str | None = None
    context: ExecutionContext | None = None


class FunctionRelease(BaseModel):
    version: str
    input_schema: dict
    output_schema: dict
    config: dict = Field(default_factory=dict)


def _execution_arguments(oid, context, db, user):
    if context is None:
        return {}
    if context.ontology_id != oid:
        raise HTTPException(422, detail={'code': 'ontology_context_mismatch', 'message': 'Function context differs from route ontology'})
    from app.services.v2.scenarios import resolve_scenario_context
    from app.services.v2.object_query.metadata import load_sql_metadata
    from app.services.v2.object_query.data_views import require_ready
    from app.services.v2.object_query.core import FalkorReadAdapter
    context = resolve_scenario_context(db, context, oid, user)
    if context.consistency != 'snapshot' or not context.data_view_id:
        raise HTTPException(422, detail={'code': 'snapshot_required', 'message': 'Typed Function inputs require a pinned execution view'})
    view = require_ready(db, context.data_view_id, oid, principal_id=user.id, is_admin=user.role == 'admin')
    graph = FalkorDBService()
    if not graph.available:
        raise HTTPException(503, detail={'code': 'graph_unavailable', 'message': 'Function context is unavailable'})
    return {'metadata': load_sql_metadata(db, oid), 'snapshot': FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(oid), 'ontology_id': oid}

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
        row=db.query(LogicAsset).filter_by(ontology_id=oid, asset_key=definition['asset_key'], version=definition.get('version', '1.0.0')).first()
        if not row:
            row=LogicAsset(ontology_id=oid, **definition)
            db.add(row); db.flush()
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


@router.get('/{oid}/logic-assets/resolve')
def resolve_function(oid: str, asset_key: str, requirement: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    try:
        asset = resolve_version(db.query(LogicAsset).filter_by(ontology_id=oid, asset_key=asset_key).all(), requirement)
        return {'asset': _as_dict(asset), 'pin': f'{asset.asset_key}@{asset.version}'}
    except ContractValidationError as exc:
        raise HTTPException(422, detail={'code': 'function_version_unavailable', 'message': str(exc), 'errors': exc.errors}) from exc


@router.post('/{oid}/logic-assets/{asset_id}/releases', status_code=201)
def release_function(oid: str, asset_id: str, body: FunctionRelease, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorized(oid, db, user)
    db.query(OntologyProject).filter_by(id=oid).with_for_update().one()
    asset = db.query(LogicAsset).filter_by(id=asset_id, ontology_id=oid).first()
    if not asset:
        raise HTTPException(404, 'Function not found')
    from jsonschema import Draft202012Validator, SchemaError
    try:
        new_version, old_version = version_tuple(body.version), version_tuple(asset.version)
        if new_version <= old_version:
            raise HTTPException(409, 'Release version must increase')
        Draft202012Validator.check_schema(body.input_schema)
        Draft202012Validator.check_schema(body.output_schema)
        execution_class({'config': body.config, 'side_effect': asset.side_effect})
    except (ContractValidationError, SchemaError) as exc:
        raise HTTPException(422, 'Invalid Function release contract') from exc
    if asset.implementation not in EXECUTORS:
        raise HTTPException(422, 'Function implementation is unavailable')
    # Conservative compatibility: schema/execution changes require a major
    # release until a richer structural compatibility adapter is available.
    if new_version[0] == old_version[0] and (body.input_schema != asset.input_schema or body.output_schema != asset.output_schema or body.config != asset.config):
        raise HTTPException(409, 'Contract changes require a new major release')
    if db.query(LogicAsset).filter_by(ontology_id=oid, asset_key=asset.asset_key, version=body.version).first():
        raise HTTPException(409, 'Function version already exists')
    values = {field: getattr(asset, field) for field in ('asset_key', 'name', 'kind', 'description', 'implementation', 'interface_key', 'interface_version', 'executor_type', 'bindings', 'binding_spec', 'deterministic', 'side_effect')}
    row = LogicAsset(ontology_id=oid, **values, version=body.version, input_schema=body.input_schema, output_schema=body.output_schema, config=body.config, status='published')
    db.add(row); db.commit(); db.refresh(row)
    return _as_dict(row)

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
            rows=graph._graph(oid).query('MATCH (n:Instance {_instance_id:$id, _ontology_id:$ontology}) RETURN n', params={'id':request.object_id, 'ontology':oid}).result_set
            if not rows: raise ValueError(f'业务对象不存在: {request.object_id}')
            props=dict(getattr(rows[0][0], 'properties', rows[0][0]))
            from app.services.v2.object_query.metadata import load_sql_metadata
            metadata = load_sql_metadata(db, oid)
            object_type = metadata.resolve_type('object', props.get('_type', ''), 'object_id')
            fields = (normalize_contract(_as_dict(asset)).get('binding_spec') or {}).get('fields') or {}
            if any(str(source).split('.')[0] not in object_type.properties for source in fields.values()):
                raise ValueError('Function binding references an unpublished property')
            public_props = {name: value for name, value in props.items() if name in object_type.properties}
            inputs, binding_trace = bind_object_fields(_as_dict(asset), public_props)
            trace.extend([{'stage':'bind_object','object_id':request.object_id}, *binding_trace])
        except Exception as exc:
            status='failed'; error={'type':type(exc).__name__,'message':'Object binding failed'}; trace=[{'stage':'bind_object','status':'failed'}]
    try:
        if status == 'failed': raise ValueError(error['message'])
        result=execute(_as_dict(asset), inputs, **_execution_arguments(oid, request.context, db, user))
        output=result['output']; trace=trace + result['trace']
    except ContractValidationError as exc:
        status='failed'; error={'type':type(exc).__name__,'stage':exc.stage,'errors':exc.errors,'message':str(exc)}; trace=trace + [{'stage':exc.stage,'status':'failed','errors':exc.errors}]
    except Exception as exc:
        status='failed'; error={'type':type(exc).__name__,'message':'Function execution failed'}; trace=trace + [{'stage':'validate_or_execute','status':'failed'}]
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
            status = 'failed'; error = {'type': type(exc).__name__, 'message': 'Function execution failed'}
            trace = [{'stage': 'validate_or_execute', 'status': 'failed'}]
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
