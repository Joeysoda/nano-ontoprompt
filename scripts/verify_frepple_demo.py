"""Read-only verification of all imported records, evidence and graph edges."""
import json
import sys
from pathlib import Path
import app.main
from app.database import SessionLocal
from app.models.entity_instance import EntityInstance
from app.models.v2.construction import EvidenceRef, ConstructionRun
from app.services.v2.manufacturing_data import adapt
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.dataset_service import DatasetService
from app.services.v2.agent_decision import AgentToolbox

report = adapt(Path(sys.argv[1]).read_bytes())
oid = report['ontology_id']
graph = FalkorDBService()._graph(oid)
actual_nodes = {r[0]: dict(r[1].properties) for r in graph.query('MATCH (n:Instance) RETURN n._instance_id, n').result_set}
actual_edges = {(r[0],r[1],r[2]) for r in graph.query('MATCH (a:Instance)-[r]->(b:Instance) RETURN a._instance_id,type(r),b._instance_id').result_set}
assert actual_edges == {(e['source'],e['type'],e['target']) for e in report['edges']}
assert set(actual_nodes) == {n['id'] for n in report['nodes']}
with SessionLocal() as db:
    instances = db.query(EntityInstance).filter_by(ontology_id=oid).all()
    assert len(instances) == 363
    by_id = {i.id:i for i in instances}
    for n in report['nodes']:
        assert by_id[n['id']].row_data == n['properties']
        for key, value in n['properties'].items():
            if value is not None and not isinstance(value, (list,dict)):
                assert actual_nodes[n['id']][key] == value, (n['id'],key)
    evidence = db.query(EvidenceRef).filter_by(ontology_id=oid).all()
    expected = {n['id'] for n in report['nodes']} | {e['properties']['assertion_id'] for e in report['edges']}
    assert len(evidence) == len(expected) == 1266
    assert {e.assertion_id for e in evidence} == expected
    assert all(e.source_version == report['sha256'] and e.evidence_text for e in evidence)
    preview = DatasetService(db).preview(oid + ':dataset', 1, 1000)
    assert preview == json.loads(Path(sys.argv[1]).read_bytes())
    tool = AgentToolbox(oid, db)
    demands = [n for n in report['nodes'] if n['kind'] == 'demand' and n['properties']['status'] == 'open']
    for n in demands:
        ctx = tool.manufacturing_context(n['id'])
        assert ctx['snapshot_only']
        assert any(x['id'] == n['id'] for x in ctx['nodes'])
    assert db.get(ConstructionRun, oid + ':import').status == 'completed'
print(json.dumps({'passed':True,'nodes_verified':len(actual_nodes),'edges_verified':len(actual_edges),
    'evidence_verified':len(evidence),'raw_records_previewed':len(preview),'agent_contexts':len(demands)}))
