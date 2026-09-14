"""Ten grounded natural-language context tests against the live FactoryNet API."""
import json
import requests

BASE = 'http://127.0.0.1:18080/api/v2/ontologies/e69354c8-6db7-4da1-b551-33b41f4c0315'
CASES = [
    ('设备关键词', '哪些 CNC 设备因为刀具状态需要检查？'),
    ('具体设备', '请分析 CNC_Mill_3_Axis 的观测记录。'),
    ('加工阶段英文', 'Repositioning 阶段的刀具状态是什么？'),
    ('加工阶段中文', '第 2 层向下加工阶段有哪些记录？'),
    ('状态英文', '哪些记录标注为 unworn？'),
    ('状态中文', '哪些观测显示刀具未磨损？'),
    ('组合问题', 'CNC 在 Layer 3 Up 时的刀具状态如何？'),
    ('大小写混合', 'cNc mill 的 Layer 2 Down 记录需要检查吗？'),
    ('无关设备', '请分析 Robot_999 的维护风险。'),
    ('不存在对象', '请检查 NOT_A_REAL_DEVICE_8877 的状态。'),
]

def main():
    results = []
    for name, task in CASES:
        try:
            r = requests.post(BASE + '/agent/context-drafts', json={'task': task}, timeout=60)
            data = r.json()
            context = data.get('context') or {}
            results.append({
                'case': name, 'status': r.status_code,
                'objects': len(context.get('objects') or []),
                'rules': len(context.get('rules') or []),
                'conclusions': len(context.get('conclusions') or []),
                'run': bool(context.get('reasoning_run')),
                'notice': context.get('notice'),
                'error': data.get('detail') if r.status_code >= 400 else None,
            })
        except Exception as exc:
            results.append({'case': name, 'status': 'exception', 'error': str(exc)})
    assert len(results) == 10
    assert all(item['status'] == 200 for item in results), results
    by_name = {item['case']: item for item in results}
    assert by_name['加工阶段中文']['conclusions'] >= 1
    assert by_name['状态中文']['conclusions'] >= 1
    assert by_name['组合问题']['conclusions'] < by_name['设备关键词']['conclusions']
    assert by_name['无关设备']['conclusions'] == 0
    assert by_name['不存在对象']['conclusions'] == 0
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
