"""Import the checked-in Joey BTS files, without LLM or synthetic relationships.

Run inside backend: PYTHONPATH=/app python /app/scripts/import_joey_bts_reasoning.py /state/bts_demo
The full CSV is registered as a dataset. The graph contains all 49 streams and
their latest observation, plus original Brick points/equipment (bounded view).
Safe retries use source-content-derived IDs; existing ontologies are not reset.
"""
from __future__ import annotations
import csv
import hashlib
import json
import sys
from pathlib import Path
from rdflib import Graph, Namespace, Literal, RDF
import app.main  # register existing models, not a new parallel storage schema
from app.database import SessionLocal
from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.relation import Relation
from app.models.logic import LogicRule
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.services.storage_service import get_storage_service
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.revision_service import create_revision

RULES = [
    'IF STREAM_OF(?stream, ?point) AND IS_POINT_OF(?point, ?equipment) THEN OBSERVES(?stream, ?equipment)',
    'IF OF_STREAM(?observation, ?stream) AND OBSERVES(?stream, ?equipment) THEN MEASURES_EQUIPMENT(?observation, ?equipment)',
]

def stable(prefix, value):
    return prefix + hashlib.sha256(str(value).encode()).hexdigest()[:24]

def install(root):
    csv_bytes = (root / 'observations.csv').read_bytes()
    ttl_bytes = (root / 'Site_B.ttl').read_bytes()
    digest = hashlib.sha256(csv_bytes + ttl_bytes).hexdigest()
    oid = stable('joey-bts-', digest)
    dataset_id, version_id, run_id = (stable(p, digest) for p in ('bts-ds-', 'bts-ver-', 'bts-import-'))
    with (root / 'observations.csv').open(encoding='utf-8-sig', newline='') as handle:
        all_rows = list(csv.DictReader(handle))
    latest = {}
    for line, row in enumerate(all_rows, start=2):
        key = row['stream_id']
        if key not in latest or row['event_time'] > latest[key][1]['event_time']:
            latest[key] = (line, row)
    rdf = Graph().parse(data=ttl_bytes.decode(), format='turtle')
    senaps = Namespace('http://senaps.io/schema/1.0/senaps#')
    brick = Namespace('https://brickschema.org/schema/Brick#')
    nodes, edges, evidence = {}, [], []
    def cite(assertion, kind, filename, locator, text):
        evidence.append(dict(id=stable('bts-ev-', oid + assertion + locator), construction_run_id=run_id,
            ontology_id=oid, assertion_id=assertion, assertion_kind=kind, source_dataset_id=dataset_id,
            source_version=digest, source_file='data/bts_demo/' + filename, source_row_id=str(locator),
            extractor='rule', evidence_text=text, content_hash=hashlib.sha256(text.encode()).hexdigest()))
    def node(nid, entity_type, props, filename, locator, text):
        nodes[nid] = {'id': nid, 'entity_type': entity_type, 'properties': props}
        cite(nid, 'node', filename, locator, text)
    def edge(source, predicate, target, filename, locator, text):
        assertion = f'{predicate}({source}, {target})'
        edges.append({'source': source, 'target': target, 'type': predicate, 'properties': {'assertion_id': assertion}})
        cite(assertion, 'edge', filename, locator, text)
    for stream, (line, row) in sorted(latest.items()):
        points = list(rdf.subjects(senaps.stream_id, Literal(stream)))
        if len(points) != 1:
            raise ValueError(f'Stream must map uniquely to a real Brick point: {stream}')
        point = points[0]
        equipment = list(rdf.objects(point, brick.isPointOf))
        if not equipment:
            raise ValueError(f'No source equipment relation for {stream}')
        sid, pid, obs = 'stream_' + stream, stable('point_', point), stable('obs_', stream + row['event_time'])
        point_type = [str(t).split('#')[-1] for t in rdf.objects(point, RDF.type)]
        triple = f'<{point}> <{senaps.stream_id}> "{stream}" .'
        node(sid, 'Stream', {'name': row['point_name'], 'stream_id': stream, 'brick_class': row['brick_class']}, 'Site_B.ttl', stream, triple)
        node(pid, 'BrickPoint', {'name': point_type[0] if point_type else 'Point', 'source_uri': str(point)}, 'Site_B.ttl', stream, triple)
        edge(sid, 'STREAM_OF', pid, 'Site_B.ttl', stream, triple)
        csv_text = json.dumps(row, ensure_ascii=False)
        node(obs, 'Observation', {**row, 'name': row['point_name'] + ' latest', 'value': float(row['value']), 'source_row': line}, 'observations.csv', str(line), csv_text)
        edge(obs, 'OF_STREAM', sid, 'observations.csv', str(line), csv_text)
        for eq in equipment:
            eid = stable('equipment_', eq)
            types = [str(t).split('#')[-1] for t in rdf.objects(eq, RDF.type)]
            text = f'<{point}> <{brick.isPointOf}> <{eq}> .'
            node(eid, 'Equipment', {'name': types[0] if types else 'Equipment', 'source_uri': str(eq), 'brick_types': types}, 'Site_B.ttl', stream, text)
            edge(pid, 'IS_POINT_OF', eid, 'Site_B.ttl', stream, text)
    service = FalkorDBService()
    if not service.available:
        raise RuntimeError('FalkorDB unavailable; no data was imported')
    with SessionLocal() as db:
        user = db.query(User).filter(User.role == 'admin').first() or db.query(User).first()
        if user is None:
            raise RuntimeError('Start backend once to initialize local user')
        storage = get_storage_service()
        uri = storage.put_bytes('raw-datasets', f'{dataset_id}/observations.csv', csv_bytes, 'text/csv')
        storage.put_bytes('raw-datasets', f'{dataset_id}/Site_B.ttl', ttl_bytes, 'text/turtle')
        if db.get(Dataset, dataset_id) is None:
            db.add(Dataset(id=dataset_id, name='Joey BTS Site B observations', kind='structured', data_class='temporal', schema_json={'columns': [{'name': k, 'type': 'string'} for k in all_rows[0]]}, latest_version_id=version_id))
            db.flush()
            db.add(DatasetVersion(id=version_id, dataset_id=dataset_id, rowcount=len(all_rows), storage_uri=uri, checksum=hashlib.sha256(csv_bytes).hexdigest(), source_path='data/bts_demo/observations.csv'))
        if db.get(OntologyProject, oid) is None:
            db.add(OntologyProject(id=oid, name='Joey BTS · 楼宇观测推理验证', domain='其他', data_class='temporal', created_by=user.id,
                description=f'Joey checked-in BTS: {len(all_rows)} CSV rows, {len(latest)} streams. Graph uses the latest row per stream and original Brick links; no Azure, no synthetic production lines.', status='published'))
        db.flush()
        if db.get(ConstructionRun, run_id) is None:
            db.add(ConstructionRun(id=run_id, ontology_id=oid, dataset_id=dataset_id, mode='temporal', status='completed', config={'source': 'Joey data/bts_demo', 'selection': 'latest observation per stream', 'source_sha256': digest}, metrics={'source_rows': len(all_rows), 'streams': len(latest), 'graph_nodes': len(nodes), 'graph_edges': len(edges)}))
        db.flush()
        for kind in sorted({n['entity_type'] for n in nodes.values()}):
            tid = oid + ':type:' + kind
            if db.get(Entity, tid) is None:
                db.add(Entity(id=tid, ontology_id=oid, name_cn=kind, name_en=kind, type='EntityType', properties={'property_definitions': [{'id': 'name', 'name': 'name', 'type': 'string'}]}))
        db.flush()
        for n in nodes.values():
            if db.get(EntityInstance, n['id']) is None:
                db.add(EntityInstance(id=n['id'], ontology_id=oid, entity_id=oid + ':type:' + n['entity_type'], row_identity=n['id'], row_data=n['properties']))
        for source, rel, target in [('Stream', 'STREAM_OF', 'BrickPoint'), ('BrickPoint', 'IS_POINT_OF', 'Equipment'), ('Observation', 'OF_STREAM', 'Stream')]:
            rid = oid + ':relation:' + rel
            if db.get(Relation, rid) is None:
                db.add(Relation(id=rid, ontology_id=oid, source_entity=oid + ':type:' + source, target_entity=oid + ':type:' + target, type=rel))
        for index, formula in enumerate(RULES, 1):
            rid = oid + ':rule:' + str(index)
            if db.get(LogicRule, rid) is None:
                db.add(LogicRule(id=rid, ontology_id=oid, name_cn=['数据流观测设备', '观测记录对应设备'][index-1], formula=formula, enabled=True, status='published', description='演示解释规则；不是数据集自带的物理或故障模型'))
        for ev in {e['id']: e for e in evidence}.values():
            if db.get(EvidenceRef, ev['id']) is None:
                db.add(EvidenceRef(**ev))
        db.flush()
        if not db.get(OntologyProject, oid).current_revision_id:
            create_revision(db, oid, source_run_id=run_id, summary={'source': 'Joey BTS', 'selection': 'latest per stream'})
        db.commit()
    service.upsert_instances(oid, list(nodes.values()))
    service.upsert_relations(oid, edges)
    report = {'ontology_id': oid, 'dataset_id': dataset_id, 'source_rows': len(all_rows), 'streams': len(latest), 'instance_nodes': len(nodes), 'asserted_edges': len(edges), 'evidence_refs': len({e['id'] for e in evidence}), 'rules': RULES, 'source_sha256': digest}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report

if __name__ == '__main__':
    install(Path(sys.argv[1]))
