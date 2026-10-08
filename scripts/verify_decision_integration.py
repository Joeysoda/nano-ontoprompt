"""Exercise all analyzer operations on explicitly labelled test decisions.

Requires the existing Joey validation server and ontology. Does not invent
business evidence: links to an actual saved reasoning conclusion when present.
"""
import json
import os
import urllib.request

BASE = os.environ.get('DECISION_TEST_URL', 'http://127.0.0.1:18080/api/v2/ontologies')
OID = os.environ.get('DECISION_TEST_ONTOLOGY', 'e69354c8-6db7-4da1-b551-33b41f4c0315')


def request(path, body=None):
    req = urllib.request.Request(BASE + '/' + OID + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def main():
    runs = request('/reasoning/runs')
    basis = []
    if runs:
        run = request('/reasoning/runs/' + runs[0]['run_id'])
        if run['inferred_facts']:
            basis = [{'run_id': run['run_id'], 'conclusion': run['inferred_facts'][0]['conclusion']}]
    current = request('/decisions')['decisions']
    ids = []
    for name in ['测试：检查加工记录', '测试：安排人工复核', '测试：调整检查计划']:
        existing = next((d for d in current if d['scenario'] == name and d['test_record']), None)
        item = existing or request('/decisions', {'scenario': name, 'test_record': True,
                                  'basis': basis, 'reasoning': '验证用决策，并非数据集真实历史决策', 'confidence': 0.8})
        ids.append(item['id'])
    edges = request('/decisions')['links']
    for source, target, kind in [(ids[0],ids[1],'CAUSED'),(ids[1],ids[2],'INFLUENCED'),(ids[0],ids[2],'PRECEDENT_FOR')]:
        if not any(e['source']==source and e['target']==target and e['relationship']==kind for e in edges):
            request('/decisions/links', {'source':source, 'target':target, 'relationship':kind,
                                       'weight':0.8, 'explanation':'测试程序明确指定的关系，不是自动发现的因果'})
    checks = []
    for operation in ['chain','influenced','precedents','roots','loops','score','network','time','distance']:
        body = {'operation':operation,'decision_id':ids[2] if operation=='roots' else ids[0],
                'target_id':ids[2], 'at_time':'2099-01-01T00:00:00Z'}
        try:
            result = request('/decisions/analyze', body)['result']
            ok = True
            if operation in ('chain','influenced','time'):
                ok = {r['decision_id'] for r in result} == set(ids[1:])
            elif operation == 'roots':
                ok = {r['decision_id'] for r in result} == {ids[0]}
            elif operation == 'precedents':
                ok = {r['decision_id'] for r in result} == {ids[2]}
            elif operation == 'distance':
                ok = result['causal_path'] == [ids[0],ids[2]]
            elif operation == 'loops':
                ok = result == []
            elif operation == 'score':
                ok = 0 < result <= 1
            elif operation == 'network':
                ok = result['node_count'] >= 3 and result['edge_count'] >= 2
            checks.append({'operation':operation,'passed':ok,'result':result})
        except Exception as exc:
            detail = exc.read().decode() if hasattr(exc, 'read') else str(exc)
            checks.append({'operation':operation,'passed':False,'error':detail})
    print(json.dumps({'ontology_id':OID,'decision_ids':ids,'checks':checks}, ensure_ascii=False, indent=2))
    if not all(c['passed'] for c in checks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
