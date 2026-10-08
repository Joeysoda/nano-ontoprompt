"""Regression on the existing FactoryNet graph; proposals only, no confirmation."""
import requests

base = 'http://127.0.0.1:18080/api/v2/ontologies/e69354c8-6db7-4da1-b551-33b41f4c0315'
def post(path, body):
    r = requests.post(base + path, json=body, timeout=60)
    r.raise_for_status()
    return r.json()

d = post('/agent/context-drafts', {'task': '哪些 CNC 设备因为刀具状态异常需要安排人工检查？'})
c = d['context']
assert c['objects'] and c['conclusions']
assert all('你的问题提到了 CNC_Mill' not in o['relevance'] for o in c['objects'])
assert all('“CNC”' in o['relevance'] for o in c['objects'])
texts = [p['human_explanation'] for p in c['conclusions']]
assert len(set(texts)) == len(texts), texts
assert any('未磨损' in t for t in texts), texts
assert all(p['related_object_ids'] for p in c['conclusions'])
body = {'object_ids': [o['id'] for o in c['objects']], 'reasoning_run_id': c['reasoning_run']['id'], 'conclusions': [p['conclusion'] for p in c['conclusions']]}
r = requests.patch(base + '/agent/runs/' + d['run_id'] + '/context', json=body, timeout=60)
r.raise_for_status()
p = post('/agent/runs/' + d['run_id'] + '/generate', {})['proposal']
assert p['outcome'] == '建议放行进入下一批生产', p['outcome']
assert p['decision_rule_evaluation']['matches']
assert len(p['basis']) == len(c['conclusions'])
assert set(p['entity_ids']) == set(body['object_ids'])
empty = post('/agent/context-drafts', {'task': '检查 NOT_A_REAL_DEVICE_8877 的状态'})['context']
assert not empty['conclusions'] and not empty['rules'] and not empty['objects']
print('PASS: actual keyword, distinct resolved labels, grounded proofs, installed decision rule, no unrelated fallback')
for t in texts:
    print(t)
