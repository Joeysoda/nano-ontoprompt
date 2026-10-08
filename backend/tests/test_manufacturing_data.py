import copy
import json
import unittest
from pathlib import Path
from app.services.v2.manufacturing_data import adapt, context, SHA256


class ManufacturingFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'data/frepple_demo/manufacturing_demo.json'
        if not path.exists():
            path = Path('/state/manufacturing_demo.json')
        cls.raw = path.read_bytes()
        cls.rows = json.loads(cls.raw)
        cls.report = adapt(cls.raw)

    def test_counts_and_full_accounting(self):
        r = self.report
        self.assertEqual(r['source_records'], 384)
        self.assertEqual(len(r['nodes']), 363)
        self.assertEqual(len(r['edges']), 903)
        self.assertEqual(len(r['excluded_records']), 21)
        self.assertEqual(r['sha256'], SHA256)

    def test_demand_status(self):
        self.assertEqual(self.report['demand_statuses'], {'closed': 204, 'open': 16})

    def test_lossless_business_fields(self):
        for n in self.report['nodes']:
            for k, v in self.rows[n['source_index']]['fields'].items():
                self.assertEqual(n['properties'][k], v)

    def test_every_foreign_key_is_accounted_for(self):
        expected = sum(sum(k.endswith('_id') and v is not None for k,v in row['fields'].items())
                       for row in self.rows if row['model'].startswith('input.'))
        self.assertEqual(expected, len(self.report['edges']))

    def test_no_unknown_endpoints_or_duplicate_edges(self):
        ids = {n['id'] for n in self.report['nodes']}
        edges = self.report['edges']
        self.assertEqual(len(edges), len({(e['source'],e['type'],e['target']) for e in edges}))
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in edges))

    def test_deterministic(self):
        self.assertEqual(self.report, adapt(self.raw))

    def test_bad_checksum(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            adapt(self.raw + b' ')

    def test_empty_dataset_has_no_synthetic_records(self):
        report = adapt(b'[]', verify=False)
        self.assertEqual(report['source_records'], 0)
        self.assertEqual(report['nodes'], [])
        self.assertEqual(report['edges'], [])

    def test_missing_or_invalid_row_fields_are_validation_errors(self):
        for row in ({'model': 'input.demand'}, {'fields': {}},
                    {'model': 'input.demand', 'fields': []}, None):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'Invalid fixture row 0'):
                adapt(json.dumps([row]).encode(), verify=False)

    def test_unqualified_model_is_not_a_supported_fixture(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported model'):
            adapt(b'[{"model":"demand","fields":{}}]', verify=False)

    def test_dangling_reference_rejected(self):
        rows = copy.deepcopy(self.rows)
        next(r for r in rows if r['model'] == 'input.demand')['fields']['item_id'] = 'nonexistent'
        with self.assertRaisesRegex(ValueError, 'Dangling'):
            adapt(json.dumps(rows).encode(), verify=False)

    def test_duplicate_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            adapt(json.dumps(self.rows + [self.rows[0]]).encode(), verify=False)

    def test_unsupported_model_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            adapt(json.dumps([{'model': 'input.mystery', 'fields': {}}]).encode(), verify=False)

    def test_unknown_context_rejected(self):
        with self.assertRaises(KeyError):
            context(self.report, 'unrelated-ontology-id')

    def test_context_no_other_demands(self):
        for node in self.report['nodes']:
            if node['kind'] != 'demand':
                continue
            selected = context(self.report, node['id'])
            demands = [n for n in selected['nodes'] if n['kind'] == 'demand']
            self.assertEqual([n['id'] for n in demands], [node['id']])
            direct = {e['target'] for e in self.report['edges'] if e['source'] == node['id']}
            self.assertTrue(direct <= {n['id'] for n in selected['nodes']})

    def test_missing_fields_not_fabricated(self):
        self.assertTrue(all('currency' not in n['properties'] for n in self.report['nodes']))
        self.assertTrue(all(r['status'] == 'blocked' for r in self.report['readiness']))

    def test_routing_includes_children(self):
        root = next(n for n in self.report['nodes'] if n['properties']['name'] == 'Varnish chair')
        names = {n['properties']['name'] for n in context(self.report, root['id'])['nodes']}
        self.assertTrue({'Apply varnish for chair', 'Drying varnish for chair'} <= names)


def test_api_scope_and_agent_context(db, admin_user, editor_user):
    import pytest
    from fastapi import HTTPException
    from app.models.ontology import OntologyProject
    from app.models.v2.construction import ConstructionRun
    from app.routers.v2.manufacturing_data import inspect_data
    from app.services.v2.agent_decision import AgentToolbox
    ManufacturingFixtureTests.setUpClass()
    report = ManufacturingFixtureTests.report
    oid = report['ontology_id']
    db.add(OntologyProject(id=oid, name='Fixture test', domain='manufacturing', created_by=admin_user.id))
    db.flush()
    run = ConstructionRun(id=oid + ':import', ontology_id=oid, mode='regular', status='completed',
        config={'adapter': 'frepple_fixture_v1', 'report': report})
    db.add(run)
    db.commit()
    assert inspect_data(oid, db=db, user=admin_user)['available']
    with pytest.raises(HTTPException) as err:
        inspect_data(oid, db=db, user=editor_user)
    assert err.value.status_code == 404
    toolbox = AgentToolbox(oid, db)
    result = toolbox.manufacturing_context(report['nodes'][0]['id'])
    assert result['snapshot_only'] and result['sha256'] == SHA256
    with pytest.raises(ValueError):
        toolbox.manufacturing_context('foreign')
    run.status = 'failed'
    db.commit()
    with pytest.raises(ValueError, match='未完成'):
        toolbox.manufacturing_context(report['nodes'][0]['id'])


if __name__ == '__main__':
    unittest.main()
