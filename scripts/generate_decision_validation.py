"""Generate 100 explicitly labelled industrial decision records for validation.

The records model a maintenance-response chain around FactoryNet observations.
They are synthetic test decisions, never presented as historical source data.
The script is idempotent by scenario prefix and uses the existing API.
"""
import json
import os
import urllib.request

BASE = os.environ.get('DECISION_TEST_URL', 'http://127.0.0.1:18080/api/v2/ontologies')
OID = os.environ.get('DECISION_TEST_ONTOLOGY', 'e69354c8-6db7-4da1-b551-33b41f4c0315')
PREFIX = '合成验证决策'


def call(path, body=None):
    request = urllib.request.Request(BASE + '/' + OID + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    current = call('/decisions')
    by_name = {d['scenario']: d for d in current['decisions'] if d.get('test_record')}
    runs = call('/reasoning/runs')
    basis = []
    if runs:
        run = call('/reasoning/runs/' + runs[0]['run_id'])
        if run.get('inferred_facts'):
            basis = [{'run_id': run['run_id'], 'conclusion': run['inferred_facts'][0]['conclusion']}]
    ids = []
    for i in range(100):
        name = f'{PREFIX} {i + 1:03d}'
        existing = by_name.get(name)
        if existing:
            ids.append(existing['id'])
            continue
        result = call('/decisions', {
            'category': 'industrial_maintenance',
            'scenario': name,
            'reasoning': f'FactoryNet 场景的合成验证：第 {i + 1} 个维护决策，不是原始数据历史记录',
            'outcome': '完成检查后更新生产安排',
            'confidence': round(0.65 + (i % 30) / 100, 2),
            'decision_maker': f'planner-{(i % 5) + 1}',
            'valid_from': f'2026-09-{(i % 28) + 1:02d}T08:00:00Z',
            'valid_until': f'2026-09-{(i % 28) + 1:02d}T20:00:00Z',
            'policy_ids': [f'MAINT-POLICY-{(i % 4) + 1}'],
            'approval_chain': [{'approver': f'supervisor-{(i % 3) + 1}', 'status': 'approved'}],
            'cross_system_context': {'source': 'synthetic_factorynet_validation', 'sequence': i + 1},
            'metadata': {'synthetic_validation': True, 'source_scenario': 'FactoryNet CNC'},
            'basis': basis,
            'test_record': True,
        })
        ids.append(result['id'])
    current_links = {(x['source'], x['target'], x['relationship']) for x in current['links']}
    for i in range(99):
        edge = (ids[i], ids[i + 1], 'INFLUENCED')
        if edge not in current_links:
            call('/decisions/links', {'source': ids[i], 'target': ids[i + 1], 'relationship': 'INFLUENCED', 'weight': 0.75, 'explanation': '合成验证中预先指定的决策传播关系'})
    for i in range(0, 96, 8):
        edge = (ids[i], ids[i + 4], 'CAUSED')
        if edge not in current_links:
            call('/decisions/links', {'source': ids[i], 'target': ids[i + 4], 'relationship': 'CAUSED', 'weight': 0.85, 'explanation': '合成验证中预先指定的直接影响关系'})
    print(json.dumps({'ontology_id': OID, 'synthetic_decisions': len(ids), 'chain_links': 99, 'shortcut_links': 12, 'test_record': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
