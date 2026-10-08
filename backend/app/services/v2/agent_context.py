"""Grounded context selection and display; relation facts are not diagnoses."""
import re
from copy import deepcopy

from app.models.v2.reasoning import ReasoningRun
from app.services.v2.graph.falkordb_service import FalkorDBService


def phase_name(value):
    if value == 'Repositioning':
        return '重新定位阶段（Repositioning）'
    match = re.fullmatch(r'Layer (\d+) (Up|Down)', str(value))
    if match:
        direction = '向上' if match[2] == 'Up' else '向下'
        return f'第 {match[1]} 层{direction}加工阶段（{value}）'
    return str(value)


def rule_explanation(rule):
    # Match the complete rule, including variable joins; do not explain a different rule as this one.
    if re.fullmatch(r'IF\s+IN_PHASE\((\?\w+),\s*(\?\w+)\)\s+AND\s+HAS_TOOL_CONDITION\(\1,\s*(\?\w+)\)\s+THEN\s+PHASE_TOOL_STATE\(\2,\s*\3\)', rule.strip()):
        return '如果同一条观测记录既说明了加工处于哪个阶段，也记录了刀具状态，就把该状态关联到这个加工阶段。这说明该阶段有这样的记录，不代表整个阶段始终如此，也不直接判定是否需要检修。'
    return '此规则暂无经过核对的业务解释，请展开技术详情核对条件与结论。'


def describe(proof, nodes):
    item = deepcopy(proof)
    raw = item.get('conclusion', '')
    match = re.fullmatch(r'([^()]+)\((.*)\)', raw)
    args = [s.strip() for s in match[2].split(',')] if match else []
    def label(ident):
        p = nodes.get(ident, {})
        value = p.get('display_name') or p.get('name') or p.get('value') or ident
        return {'unworn': '未磨损（unworn）', 'worn': '已磨损（worn）'}.get(str(value), str(value))
    if match and match[1] == 'PHASE_TOOL_STATE' and len(args) == 2:
        item['human_explanation'] = f'{phase_name(label(args[0]))}有一条记录，其刀具状态标为“{label(args[1])}”。'
        item['limitation'] = '这条规则建立阶段与状态的关联，未判断异常程度或是否需要检查。'
    else:
        item['human_explanation'] = (f'{match[1]}：' + ' → '.join(label(a) for a in args)) if match else raw
        item['limitation'] = '按原规则解释；没有维护判定标准时不能自动转成检查建议。'
    item['source_objects'] = [v for v in (item.get('bindings') or {}).values() if isinstance(v, str) and v in nodes]
    return item


def context_for(toolbox, task, run_id=None, object_ids=None):
    ontology = toolbox.ontology_context()
    service = FalkorDBService()
    if not service.available:
        raise RuntimeError('FalkorDB 不可用，无法验证对象与结论的关系')
    rows = service._graph(toolbox.ontology_id).query('MATCH (n:Instance) RETURN n LIMIT 10001').result_set
    if len(rows) > 10000:
        raise ValueError('当前对象范围超过 10000，请缩小分析范围；未使用截断样本作判断')
    nodes = {r[0].properties['_instance_id']: dict(r[0].properties) for r in rows}
    tokens = re.findall(r'[A-Za-z0-9_-]{3,}', task)
    task_lower = task.lower()
    # The FactoryNet labels are English, while users may ask in Chinese.
    phase_terms = []
    for pattern, value in ((r'第\s*1\s*层\s*(向上|向下)', None), (r'第\s*2\s*层\s*(向上|向下)', None),
                           (r'第\s*3\s*层\s*(向上|向下)', None)):
        match = re.search(pattern, task)
        if match:
            layer = re.search(r'第\s*(\d+)', match.group()).group(1)
            phase_terms.append(f'Layer {layer} {"Up" if match.group(1) == "向上" else "Down"}')
    phase_terms.extend(re.findall(r'Layer\s+[123]\s+(?:Up|Down)|Repositioning', task, flags=re.I))
    status_terms = []
    if '未磨损' in task or 'unworn' in task_lower:
        status_terms.append('unworn')
    if '已磨损' in task or re.search(r'\bworn\b', task_lower):
        status_terms.append('worn')
    selected = {}
    for ident, props in nodes.items():
        searchable = ('name', 'display_name', 'machine_type', '_type', '_instance_id',
                      'ctx_process_phase', 'ctx_tool_condition')
        hits = [(token, key, str(value)) for token in tokens for key, value in props.items()
                if key in searchable and token.lower() in str(value).lower()]
        hits.extend([(term, 'ctx_process_phase', str(props.get('ctx_process_phase')))
                     for term in phase_terms if props.get('ctx_process_phase') == term])
        hits.extend([(term, 'ctx_tool_condition', str(props.get('ctx_tool_condition')))
                     for term in status_terms if props.get('ctx_tool_condition') == term])
        if object_ids is not None:
            if ident in object_ids:
                selected[ident] = '用户手动选择了该对象。'
        elif hits:
            token, field, value = hits[0]
            selected[ident] = f'问题中的“{token}”匹配属性 {field} 的值“{value}”；这是范围匹配，不代表已发现异常。'
    # Apply multiple domain constraints as an intersection for observation records.
    if object_ids is None and (phase_terms or status_terms):
        for ident, props in nodes.items():
            if props.get('_type') != 'Observation':
                continue
            if phase_terms and props.get('ctx_process_phase') not in phase_terms:
                selected.pop(ident, None)
                continue
            if status_terms and props.get('ctx_tool_condition') not in status_terms:
                selected.pop(ident, None)
    if object_ids is not None and set(object_ids) - nodes.keys():
        raise ValueError('所选对象不属于当前本体实例')
    query = toolbox.db.query(ReasoningRun).filter_by(ontology_id=toolbox.ontology_id)
    candidates = [query.filter_by(id=run_id).first()] if run_id else query.order_by(ReasoningRun.created_at.desc()).all()
    if run_id and not candidates[0]:
        raise ValueError('推理运行不存在于当前本体')
    chosen, proofs = None, []
    for run in candidates:
        if (run.payload or {}).get('ontology_revision_id') != ontology['revision_id']:
            if run_id:
                raise ValueError('推理运行的本体版本与当前版本不同，请重新推理')
            continue
        matched = []
        for proof in (run.payload or {}).get('conclusions', []):
            refs = {v for v in (proof.get('bindings') or {}).values() if isinstance(v, str)}
            related = sorted(refs & selected.keys())
            if related:
                item = describe(proof, nodes)
                item['related_object_ids'] = related
                item['relevance'] = '推理证明中的变量绑定引用了：' + '、'.join(related)
                matched.append(item)
        if matched or run_id:
            chosen, proofs = run, matched
            break
    # Show the actual supporting objects, not the first arbitrary twenty rows.
    supporting = {ident for p in proofs for ident in p['related_object_ids']}
    visible = supporting if object_ids is None else set(object_ids)
    objects = []
    for ident in sorted(visible):
        p = nodes[ident]
        objects.append({'id': ident, 'type': p.get('_type'), 'label': p.get('display_name') or p.get('name') or f"{p.get('machine_type', p.get('_type', '对象'))} · 记录 {p.get('source_row_index', ident)}",
                        'summary': '；'.join(filter(None, [f"这条记录来自{phase_name(p['ctx_process_phase'])}" if p.get('ctx_process_phase') else '',
                            f"刀具状态标注为{ {'unworn': '未磨损', 'worn': '已磨损', 'unknown': '未知'}.get(p['ctx_tool_condition'], p['ctx_tool_condition'])}" if p.get('ctx_tool_condition') else ''])),
                        'relevance': selected.get(ident, '用户手动选择了该对象。'), 'properties': p})
    rules = {}
    for p in proofs:
        key = (p.get('rule_id'), p.get('rule'))
        rules[key] = {'id': p.get('rule_id'), 'technical_rule': p.get('rule', ''), 'run_id': chosen.id,
                      'explanation': rule_explanation(p.get('rule', '')), 'status': '已命中'}
    return {'ontology': ontology, 'objects': objects, 'conclusions': proofs, 'rules': list(rules.values()),
            'reasoning_run': {'id': chosen.id, 'status': chosen.status, 'created_at': chosen.created_at.isoformat()} if chosen else None,
            'selection_method': 'verified_keyword_and_proof_match', 'requires_review': True,
            'notice': '当前使用关键词与证明绑定匹配。没有匹配到相关证明时不补入其他结论。' if proofs else '未找到同时匹配问题范围和当前版本的推理证明，请调整范围或补充推理依据。'}
