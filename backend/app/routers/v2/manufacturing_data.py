"""Read-only manufacturing fixture inspection using existing authorization."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.services.v2.manufacturing_data import context

router = APIRouter()


@router.get('/{oid}/manufacturing-data')
def inspect_data(oid: str, object_id: str | None = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ontology = db.get(OntologyProject, oid)
    if not ontology or (user.role != 'admin' and ontology.created_by != user.id):
        raise HTTPException(404, '本体不存在或无访问权限')
    run = db.get(ConstructionRun, oid + ':import')
    if not run or run.config.get('adapter') != 'frepple_fixture_v1':
        return {'available': False}
    report = run.config['report']
    selection = None
    if object_id:
        try:
            selection = context(report, object_id)
        except KeyError:
            raise HTTPException(404, '对象不属于当前数据集')
    evidence = []
    if object_id:
        evidence = [{'source_file': e.source_file, 'source_row_id': e.source_row_id,
            'content_hash': e.content_hash, 'source_version': e.source_version, 'text': e.evidence_text}
            for e in db.query(EvidenceRef).filter_by(ontology_id=oid, assertion_id=object_id).all()]
    return {'available': True, 'import_status': run.status, 'error': run.error,
        **{k: v for k, v in report.items() if k != 'excluded_records'},
        'excluded_counts': len(report['excluded_records']), 'selection': selection, 'evidence': evidence,
        'scope': '固定导入批次的完整来源快照，不是当前可编辑图的实时状态；不用于直接计算 What-if。'}
