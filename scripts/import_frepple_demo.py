"""Import the pinned fixture into existing Nano models; retry projection safely.

PYTHONPATH=backend python scripts/import_frepple_demo.py data/frepple_demo/manufacturing_demo.json
"""
import json
import sys
import hashlib
from pathlib import Path
import app.main
from app.database import SessionLocal
from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.relation import Relation
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.services.v2.manufacturing_data import adapt, stable, LABELS, FIELDS
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.revision_service import create_revision
from app.services.storage_service import get_storage_service


def install(path):
    raw = Path(path).read_bytes()
    report = adapt(raw)
    oid = report['ontology_id']
    run_id, ds_id, ver_id = (oid + suffix for suffix in (':import', ':dataset', ':version'))
    graph = FalkorDBService()
    if not graph.available:
        raise RuntimeError('FalkorDB unavailable; import not started')
    rows = json.loads(raw)
    with SessionLocal() as db:
        user = db.query(User).filter(User.role == 'admin').first()
        if not user:
            raise RuntimeError('Initialize a local admin before running the importer')
        storage = get_storage_service()
        raw_uri = storage.put_bytes('raw-datasets', f'{ds_id}/manufacturing_demo.json', raw, 'application/json')
        if hashlib.sha256(storage.get_object(raw_uri)).hexdigest() != report['sha256']:
            raise RuntimeError('Stored source checksum mismatch')
        if not db.get(Dataset, ds_id):
            db.add(Dataset(id=ds_id, name='frePPLe 制造业官方样例', kind='structured', data_class='regular',
                latest_version_id=ver_id, schema_json={'format': 'django_fixture', 'source_url': report['source_url']}))
            db.flush()
            db.add(DatasetVersion(id=ver_id, dataset_id=ds_id, rowcount=len(rows),
                storage_uri=raw_uri,
                checksum=report['sha256'], source_path='data/frepple_demo/manufacturing_demo.json'))
        else:
            db.get(DatasetVersion, ver_id).storage_uri = raw_uri
        if not db.get(OntologyProject, oid):
            db.add(OntologyProject(id=oid, name='frePPLe · 制造业务数据工作台', domain='工业生产',
                data_class='regular', created_by=user.id, status='published',
                description='官方桌椅生产示例。仅数据与语义，不含 frePPLe 排程引擎；与 FactoryNet 隔离。'))
        db.flush()
        run = db.get(ConstructionRun, run_id)
        if not run:
            run = ConstructionRun(id=run_id, ontology_id=oid, dataset_id=ds_id, mode='regular', status='running',
                config={'adapter': 'frepple_fixture_v1', 'report': report}, metrics={})
            db.add(run)
        else:
            run.config = {'adapter': 'frepple_fixture_v1', 'report': report}
            run.status = 'running'
        schema_changed = False
        for kind in report['counts']:
            tid = oid + ':type:' + kind
            sample_fields = sorted({k for n in report['nodes'] if n['entity_type'] == kind for k in n['properties']})
            def definition(k):
                values = [n['properties'][k] for n in report['nodes'] if n['entity_type'] == kind and n['properties'].get(k) is not None]
                dtype = 'boolean' if values and all(isinstance(v, bool) for v in values) else 'number' if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values) else 'string'
                return {'id': k, 'name': k, 'type': dtype, **FIELDS.get(k, {})}
            existing = db.get(Entity, tid)
            definitions = {'property_definitions': [definition(k) for k in sample_fields]}
            if not existing:
                db.add(Entity(id=tid, ontology_id=oid, name_cn=LABELS[kind], name_en=kind, type='EntityType',
                    properties=definitions))
                schema_changed = True
            elif existing.properties != definitions:
                # This importer owns only its content-addressed demo ontology.
                existing.properties = definitions
                schema_changed = True
        db.flush()
        for n in report['nodes']:
            if not db.get(EntityInstance, n['id']):
                db.add(EntityInstance(id=n['id'], ontology_id=oid, entity_id=oid + ':type:' + n['entity_type'],
                    row_identity=n['id'], row_data=n['properties']))
        by_id = {n['id']: n for n in report['nodes']}
        for edge in report['edges']:
            a, b = by_id[edge['source']]['entity_type'], by_id[edge['target']]['entity_type']
            rid = oid + ':rel:' + stable(a + edge['type'] + b)
            if not db.get(Relation, rid):
                db.add(Relation(id=rid, ontology_id=oid, source_entity=oid + ':type:' + a,
                    target_entity=oid + ':type:' + b, type=edge['type']))
                db.flush()
        assertions = [(n['id'], 'node', n['source_index']) for n in report['nodes']]
        assertions += [(e['properties']['assertion_id'], 'edge', e['properties']['source_index']) for e in report['edges']]
        for assertion, kind, pos in assertions:
            eid = oid + ':ev:' + stable(assertion)
            if not db.get(EvidenceRef, eid):
                text = json.dumps(rows[pos], ensure_ascii=False, sort_keys=True)
                db.add(EvidenceRef(id=eid, construction_run_id=run_id, ontology_id=oid, assertion_id=assertion,
                    assertion_kind=kind, source_dataset_id=ds_id, source_version=report['sha256'],
                    source_file=report['source_url'], source_row_id=f'JSON index {pos}', extractor='rule',
                    evidence_text=text, content_hash=hashlib.sha256(text.encode()).hexdigest()))
        db.flush()
        if schema_changed or not db.get(OntologyProject, oid).current_revision_id:
            create_revision(db, oid, source_run_id=run_id, summary={'source': report['source_url']})
        db.commit()
        try:
            graph.upsert_instances(oid, [{'id': n['id'], 'entity_type': n['entity_type'],
                'properties': {k: v for k, v in n['properties'].items() if v is not None and not isinstance(v, (dict, list))}} for n in report['nodes']])
            graph.upsert_relations(oid, report['edges'])
            result = graph._graph(oid).query('MATCH (n:Instance) RETURN count(n)').result_set[0][0]
            edge_count = graph._graph(oid).query('MATCH (:Instance)-[r]->(:Instance) RETURN count(r)').result_set[0][0]
            if result != len(report['nodes']) or edge_count != len(report['edges']):
                raise RuntimeError(f'Projection count mismatch: {result}/{edge_count}')
            run.status = 'completed'
            run.error = None
            run.metrics = {'nodes': result, 'edges': edge_count, 'source_records': len(rows)}
        except Exception as exc:
            run.status, run.error = 'failed', str(exc)
            db.commit()
            raise
        db.commit()
    print(json.dumps({'ontology_id': oid, 'source_records': len(rows), 'nodes': result, 'edges': edge_count,
        'demand_statuses': report['demand_statuses'], 'status': 'completed'}, ensure_ascii=False))


if __name__ == '__main__':
    install(sys.argv[1])
