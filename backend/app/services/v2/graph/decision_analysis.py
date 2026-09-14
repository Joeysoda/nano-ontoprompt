"""Fixed-version Semantica decision analysis on a dedicated FalkorDB projection.

The adapter repairs Cypher dialect differences, but leaves upstream algorithms
and score semantics intact. No database error is converted into a zero score.
"""
from __future__ import annotations

import re
from dataclasses import asdict

from app.services.v2.graph.falkordb_service import FalkorDBService


class DecisionGraphAdapter:
    def __init__(self, ontology_id):
        service = FalkorDBService()
        if not service.available:
            raise RuntimeError('FalkorDB 不可用')
        # A separate projection keeps :Decision queries out of instance graphs.
        self.graph = service._graph('decisions_' + ontology_id)
        self.last_error = None

    def execute_query(self, query, params=None):
        # Upstream mixes quantified path syntax with legacy variable-length
        # patterns. Normalize only these known dialect constructs.
        query = re.sub(r'\[([^\]]+)\]([<>-]+)\{1,(\d+)\}',
                       lambda m: '[' + m[1] + '*1..' + m[3] + ']' + m[2], query)
        query = query.replace('|:', '|')
        query = query.replace('path[i].decision_id', 'nodes(path)[i].decision_id')
        query = query.replace('path[i+1].decision_id', 'nodes(path)[i+1].decision_id')
        query = query.replace('ALL(i IN range(0, length(path)-2) |',
                              'ALL(i IN range(0, length(path)-2) WHERE')
        if 'as loop_path' in query:
            # FalkorDB's repeated bound endpoint plan can miss a closed path.
            # Bind the end separately and constrain equality explicitly.
            query = query.replace(']->(d1)', ']->(cycle_end:Decision)')
            query = re.sub(r'WHERE ALL\(.*?\)\s*RETURN',
                           'WHERE cycle_end.decision_id = d1.decision_id RETURN',
                           query, flags=re.S)
        try:
            result = self.graph.query(query, params=params or {}, timeout=5000)
            names = [str(col[1]) for col in result.header]
            rows = [dict(zip(names, [getattr(v, 'properties', v) for v in row]))
                    for row in result.result_set]
            if 'as loop_path' in query:
                # Evaluate the original consecutive-node exclusion in Python:
                # property access on indexed path nodes is not portable.
                rows = [r for r in rows if all(a['decision_id'] != b['decision_id']
                        for a, b in zip(r['loop_path'], r['loop_path'][1:]))]
            return rows
        except Exception as exc:
            self.last_error = exc
            raise


def analyze(ontology_id, operation, decision_id=None, target_id=None,
            direction='downstream', max_depth=5, at_time=None):
    from semantica.context.causal_analyzer import CausalChainAnalyzer
    from semantica.context.context_graph import ContextGraph

    if not 1 <= max_depth <= 20 or direction not in ('upstream', 'downstream'):
        raise ValueError('无效方向或深度（1–20）')
    adapter = DecisionGraphAdapter(ontology_id)
    analyzer = CausalChainAnalyzer(adapter)
    if operation == 'distance':
        # This upstream method requires ContextGraph. Build a bounded, explicit
        # analysis snapshot; never substitute the UI's 500-edge sample.
        nodes = adapter.execute_query('MATCH (d:Decision) RETURN d LIMIT 10001')
        edges = adapter.execute_query('MATCH (a:Decision)-[r]->(b:Decision) RETURN a.decision_id AS source, b.decision_id AS target, type(r) AS kind, r.weight AS weight LIMIT 20001')
        if len(nodes) > 10000 or len(edges) > 20000:
            raise ValueError('决策图超过路径分析上限，未返回不完整路径')
        snapshot = ContextGraph()
        for row in nodes:
            props = dict(row['d'])
            node_id = props.pop('decision_id')
            snapshot.add_node(node_id, 'decision', **props)
        for edge in edges:
            snapshot.add_edge(edge['source'], edge['target'], edge['kind'],
                              weight=edge['weight'] if edge['weight'] is not None else 1.0)
        result = CausalChainAnalyzer(snapshot).interpret_causal_distance(decision_id, target_id)
    elif operation == 'chain':
        result = analyzer.get_causal_chain(decision_id, direction, max_depth)
    elif operation == 'time':
        if not at_time:
            raise ValueError('请选择截止时间')
        result = analyzer.trace_at_time(decision_id, at_time, direction, max_depth)
    elif operation == 'influenced':
        result = analyzer.get_influenced_decisions(decision_id, max_depth)
    elif operation == 'precedents':
        result = analyzer.get_precedent_chain(decision_id, max_depth)
    elif operation == 'roots':
        result = analyzer.find_root_causes(decision_id, max_depth)
    elif operation == 'loops':
        result = analyzer.find_causal_loops(max_depth)
    elif operation == 'score':
        result = analyzer.get_causal_impact_score(decision_id)
    elif operation == 'network':
        result = analyzer.analyze_causal_network()
    else:
        raise ValueError('未知分析操作')
    if adapter.last_error:
        raise RuntimeError('原版分析查询失败，未将失败解释为零分') from adapter.last_error
    if isinstance(result, list):
        result = [asdict(item) if hasattr(item, '__dataclass_fields__') else item for item in result]
    return {'operation': operation, 'engine': 'semantica.context.CausalChainAnalyzer',
            'result': result, 'score_notice': '评分和权重为原版图分析指标，不是故障概率或已验证的因果效应'}
