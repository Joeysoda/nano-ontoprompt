"""Lossless, deterministic adapter for the pinned frePPLe fixture (no planner).

Association rows remain objects: their quantities, priorities and effectivity
must not be flattened into an unqualified edge. Source dates are not shifted.
"""
import hashlib
import json
from collections import Counter

REVISION = '73e5be3d1573db043209111325dd921d68cf4b88'
SHA256 = 'd7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef'
SOURCE = f'https://github.com/frePPLe/frepple/blob/{REVISION}/freppledb/input/fixtures/manufacturing_demo.json'
TYPES = {
    'calendar': 'Calendar', 'calendarbucket': 'CalendarBucket', 'location': 'Location',
    'customer': 'Customer', 'item': 'Item', 'operation': 'Operation', 'buffer': 'Inventory',
    'resource': 'Resource', 'skill': 'Skill', 'resourceskill': 'ResourceSkill',
    'operationmaterial': 'MaterialRequirement', 'operationresource': 'ResourceRequirement',
    'supplier': 'Supplier', 'itemsupplier': 'SupplyOption', 'itemdistribution': 'DistributionOption',
    'demand': 'Demand', 'operationplan': 'PlannedOrder',
}
LABELS = dict(zip(TYPES.values(), ['日历','日历时段','地点','客户','产品与物料','工序定义','库存',
    '设备与人员资源','技能','资源技能配置','物料投入产出','工序资源需求','供应商','采购选项','配送选项','客户需求','计划订单']))
FIELDS = {
    'name': {'label': '名称'}, 'type': {'label': '类型'},
    'item_id': {'label': '产品或物料'}, 'customer_id': {'label': '客户'},
    'location_id': {'label': '所在地点'}, 'origin_id': {'label': '发货地点'},
    'operation_id': {'label': '关联工序'}, 'resource_id': {'label': '所需资源'},
    'supplier_id': {'label': '供应商'}, 'owner_id': {'label': '上级分组或路线'},
    'calendar_id': {'label': '所属日历'}, 'maximum_calendar_id': {'label': '产能日历'},
    'available_id': {'label': '可用日历'}, 'skill_id': {'label': '技能'},
    'duration': {'label': '固定加工时间', 'unit': '秒', 'description': '工序定义的固定时间部分；不是观测到的实际耗时'},
    'duration_per': {'label': '单位加工时间', 'unit': '秒/产品单位', 'description': '按加工数量增加的时间；产品单位未提供时不能假设为件'},
    'leadtime': {'label': '提前期', 'unit': '秒'},
    'due': {'label': '需求交期', 'description': '保留源数据时间；时区未声明'},
    'quantity': {'label': '数量', 'description': '含义取决于对象类型；物料投入为负、产出为正，不取绝对值'},
    'priority': {'label': '优先级', 'description': '保留源值；不能跨不同对象类型直接比较'},
    'onhand': {'label': '现有库存'}, 'maximum': {'label': '资源能力'},
    'status': {'label': '业务状态', 'description': 'closed 历史需求不能当作待交付订单'},
    'startdate': {'label': '开始时间'}, 'enddate': {'label': '结束时间'},
}
REFS = {'calendar_id': 'calendar', 'available_id': 'calendar', 'maximum_calendar_id': 'calendar',
    'location_id': 'location', 'origin_id': 'location', 'destination_id': 'location',
    'customer_id': 'customer', 'item_id': 'item', 'operation_id': 'operation',
    'resource_id': 'resource', 'skill_id': 'skill', 'supplier_id': 'supplier'}


def stable(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def adapt(raw: bytes, verify=True):
    digest = hashlib.sha256(raw).hexdigest()
    if verify and digest != SHA256:
        raise ValueError('Source checksum differs from the reviewed official fixture')
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('Expected a fixture array')
    oid = 'frepple-' + digest[:24]
    nodes, index, ignored, edges = [], {}, [], []
    for pos, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get('model'), str) or not isinstance(row.get('fields'), dict):
            raise ValueError(f'Invalid fixture row {pos}: model must be a string and fields must be an object')
        model, fields = row['model'], row['fields']
        kind = model.removeprefix('input.')
        if model.startswith('common.'):
            ignored.append({'index': pos, 'model': model, 'fields': fields})
            continue
        if not model.startswith('input.') or kind not in TYPES:
            raise ValueError(f'Unsupported model: {model}')
        key = fields.get('name', fields.get('reference', row.get('pk', fields.get('id'))))
        key = str(key) if key is not None else 'row:' + str(pos)
        if (kind, key) in index:
            raise ValueError(f'Duplicate identity: {kind}/{key}')
        nid = oid + ':' + stable(kind + ':' + key)
        index[kind, key] = nid
        nodes.append({'id': nid, 'entity_type': TYPES[kind], 'kind': kind, 'source_index': pos,
            'properties': {**fields, 'name': fields.get('name', fields.get('reference', LABELS[TYPES[kind]] + ' #' + str(pos))),
                'source_model': model, 'source_key': key, 'source_index': pos, 'source_kind': 'official_demo'}})
    for node in nodes:
        for field, value in rows[node['source_index']]['fields'].items():
            if not field.endswith('_id') or value is None:
                continue
            target_kind = node['kind'] if field == 'owner_id' else REFS.get(field)
            if not target_kind:
                raise ValueError(f'Unmapped reference: {node["kind"]}.{field}')
            target = index.get((target_kind, str(value)))
            if not target:
                raise ValueError(f'Dangling reference: {node["kind"]}.{field}={value}')
            rel = 'HAS_' + field.removesuffix('_id').upper()
            edges.append({'source': node['id'], 'target': target, 'type': rel,
                'properties': {'source_index': node['source_index'], 'source_field': field,
                    'assertion_id': f'{rel}({node["id"]}, {target})'}})
    counts = dict(Counter(n['entity_type'] for n in nodes))
    statuses = dict(Counter(n['properties'].get('status', 'unknown') for n in nodes if n['kind'] == 'demand'))
    baseline_date = next((r['fields'].get('value') for r in rows if r['model'] == 'common.parameter' and r['fields'].get('name') == 'currentdate'), None)
    return {'ontology_id': oid, 'source_url': SOURCE, 'sha256': digest, 'source_revision': REVISION,
        'baseline_date': baseline_date,
        'source_records': len(rows), 'nodes': nodes, 'edges': edges, 'excluded_records': ignored,
        'counts': counts, 'demand_statuses': statuses, 'field_semantics': FIELDS, 'type_labels': LABELS,
        'limitations': ['这是官方合成样例，不是真实工厂经营记录。',
            '源日期保持原样；未声明时区，未平移到今天。',
            '产品计量单位和成本口径未完整声明，不自动补成件或人民币。',
            '工序定义不是已分配的生产任务；尚未执行排程或 What-if。',
            '需求与生产任务之间没有凭空创建订单分配关系。',
            '资源技能、分组、日历和路线保留原始配置，尚未由求解器展开。'],
        'readiness': [{'question': q, 'status': 'blocked', 'missing': m} for q, m in [
            ('设备停机两小时', '需选择计划时间范围并生成任务分配，执行日历与容量计算'),
            ('插入紧急订单', '需订单到任务的分配及排程计算'),
            ('增加操作人员', '需解释资源分组、技能和日历约束并执行排程'),
            ('原料晚到一天', '需物料供需分配和时间计算'),
            ('转移到替代设备', '需确认替代设备能力、换型时间与成本数据')]]}


def context(report, object_id):
    """Structural dependency closure, not a causal prediction or a graph sample.

    Stop at upstream customer/location/group/calendar fan-out to avoid dragging
    unrelated orders in through a shared shop or company hierarchy.
    """
    nodes = {n['id']: n for n in report['nodes']}
    if object_id not in nodes:
        raise KeyError(object_id)
    selected, pending = {object_id}, [object_id]
    incoming_kinds = {'operation', 'operationmaterial', 'operationresource', 'resourceskill',
                      'calendarbucket', 'buffer', 'itemsupplier', 'itemdistribution'}
    while pending:
        current = pending.pop()
        kind = nodes[current]['kind']
        for edge in report['edges']:
            candidate = None
            if edge['source'] == current:
                candidate = edge['target']
            elif edge['target'] == current and kind not in {'location', 'customer', 'supplier'}:
                source = nodes[edge['source']]
                if (source['kind'] in incoming_kinds and edge['type'] != 'HAS_OWNER') or (
                    edge['type'] == 'HAS_OWNER' and kind in {'operation', 'resource'}):
                    candidate = edge['source']
            if candidate and candidate not in selected:
                selected.add(candidate)
                pending.append(candidate)
    return {'nodes': [n for n in report['nodes'] if n['id'] in selected],
            'edges': [e for e in report['edges'] if e['source'] in selected and e['target'] in selected],
            'scope': '结构依赖闭包：含共享资源配置，不代表独占资源分配或因果影响。未截断。'}
