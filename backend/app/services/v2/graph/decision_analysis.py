"""Fixed-version Semantica decision analysis on a dedicated FalkorDB projection.

The adapter repairs Cypher dialect differences, but leaves upstream algorithms
and score semantics intact. No database error is converted into a zero score.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime
from typing import Any

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


def _load_compat_graph(adapter: DecisionGraphAdapter) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Load a bounded decision graph for developer environments without Semantica.

    The production path below still uses the pinned Semantica implementation.
    This compatibility path is deliberately small and deterministic: it keeps
    local development and Docker health checks useful when the optional
    reasoning package cannot be installed, without inventing an empty score or
    swallowing FalkorDB query errors.
    """
    node_rows = adapter.execute_query('MATCH (d:Decision) RETURN d LIMIT 10001')
    nodes: dict[str, dict[str, Any]] = {}
    for row in node_rows:
        props = row.get('d') if isinstance(row, dict) else None
        props = props if isinstance(props, dict) else row if isinstance(row, dict) else {}
        decision_id = str(props.get('decision_id') or '')
        if decision_id:
            nodes[decision_id] = dict(props)

    edge_rows = adapter.execute_query(
        'MATCH (a:Decision)-[r]->(b:Decision) '
        'RETURN a.decision_id AS source, b.decision_id AS target, '
        'type(r) AS kind, r.weight AS weight, r.recorded_at AS recorded_at '
        'LIMIT 20001'
    )
    edges: list[dict[str, Any]] = []
    for row in edge_rows:
        source = str(row.get('source') or '')
        target = str(row.get('target') or '')
        if source and target:
            edges.append({
                'source': source,
                'target': target,
                'kind': str(row.get('kind') or '').upper(),
                'weight': float(row.get('weight')) if row.get('weight') is not None else 1.0,
                'recorded_at': row.get('recorded_at'),
            })
    if len(node_rows) > 10000 or len(edge_rows) > 20000:
        raise ValueError('决策图超过路径分析上限，未返回不完整路径')
    return nodes, edges


def _compat_walk(
    start: str,
    edges: list[dict[str, Any]],
    *,
    kinds: set[str],
    direction: str,
    max_depth: int,
    at_time: str | None = None,
) -> list[dict[str, Any]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge['kind'] not in kinds:
            continue
        if at_time is not None and edge.get('recorded_at'):
            try:
                recorded = datetime.fromisoformat(str(edge['recorded_at']).replace('Z', '+00:00'))
                cutoff = datetime.fromisoformat(str(at_time).replace('Z', '+00:00'))
                if recorded > cutoff:
                    continue
            except ValueError:
                # Keep malformed timestamps visible rather than treating the
                # edge as a valid historical event.
                continue
        if direction == 'upstream':
            adjacency[edge['target']].append({**edge, 'neighbor': edge['source']})
        else:
            adjacency[edge['source']].append({**edge, 'neighbor': edge['target']})

    seen = {start}
    queue = deque([(start, 0)])
    result: list[dict[str, Any]] = []
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in adjacency.get(current, []):
            neighbor = str(edge['neighbor'])
            if neighbor in seen:
                continue
            seen.add(neighbor)
            result.append({
                'decision_id': neighbor,
                'depth': depth + 1,
                'relationship': edge['kind'],
                'weight': edge['weight'],
            })
            queue.append((neighbor, depth + 1))
    return result


def _compat_shortest_path(source: str, target: str, edges: list[dict[str, Any]], max_depth: int) -> tuple[list[str], float]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge['kind'] in {'CAUSED', 'INFLUENCED'}:
            adjacency[edge['source']].append(edge)
    queue = deque([(source, [source], 1.0)])
    visited = {source}
    while queue:
        current, path, confidence = queue.popleft()
        if current == target:
            return path, confidence
        if len(path) - 1 >= max_depth:
            continue
        for edge in adjacency.get(current, []):
            next_id = edge['target']
            if next_id in visited:
                continue
            visited.add(next_id)
            queue.append((next_id, path + [next_id], confidence * float(edge['weight'])))
    return [], 0.0


def _compat_loops(edges: list[dict[str, Any]], max_depth: int) -> list[dict[str, Any]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge['kind'] in {'CAUSED', 'INFLUENCED'}:
            adjacency[edge['source']].append(edge['target'])
    cycles: set[tuple[str, ...]] = set()

    def canonical(path: list[str]) -> tuple[str, ...]:
        rotations = [tuple(path[index:] + path[:index]) for index in range(len(path))]
        return min(rotations)

    def visit(start: str, current: str, path: list[str]) -> None:
        if len(path) > max_depth:
            return
        for neighbor in adjacency.get(current, []):
            if neighbor == start and len(path) >= 2:
                cycles.add(canonical(path))
            elif neighbor not in path:
                visit(start, neighbor, path + [neighbor])

    for node in adjacency:
        visit(node, node, [node])
    return [
        {'loop_length': len(cycle), 'path': list(cycle) + [cycle[0]]}
        for cycle in sorted(cycles)
    ]


def _compat_analyze(
    adapter: DecisionGraphAdapter,
    operation: str,
    decision_id: str | None,
    target_id: str | None,
    direction: str,
    max_depth: int,
    at_time: str | None,
) -> dict[str, Any]:
    nodes, edges = _load_compat_graph(adapter)
    causal_kinds = {'CAUSED', 'INFLUENCED'}
    if operation in {'chain', 'influenced', 'time'}:
        if not decision_id:
            raise ValueError('该分析操作需要 decision_id')
        result = _compat_walk(
            decision_id,
            edges,
            kinds=causal_kinds,
            direction=direction,
            max_depth=max_depth,
            at_time=at_time if operation == 'time' else None,
        )
    elif operation == 'distance':
        if not decision_id or not target_id:
            raise ValueError('distance 需要 decision_id 和 target_id')
        path, confidence = _compat_shortest_path(decision_id, target_id, edges, max_depth)
        result = {'causal_path': path, 'confidence_decay': confidence}
    elif operation == 'loops':
        result = _compat_loops(edges, max_depth)
    elif operation == 'precedents':
        if not decision_id:
            raise ValueError('precedents 需要 decision_id')
        result = _compat_walk(
            decision_id, edges, kinds={'PRECEDENT_FOR'}, direction='downstream', max_depth=max_depth
        )
    elif operation == 'roots':
        if not decision_id:
            raise ValueError('roots 需要 decision_id')
        reachable = _compat_walk(
            decision_id, edges, kinds={'PRECEDENT_FOR'}, direction='upstream', max_depth=max_depth
        )
        # A root is a node with no incoming PRECEDENT_FOR edge.  The source of
        # an edge is not a predecessor of itself; using the source set here
        # incorrectly discarded the actual root in a simple A -> C chain.
        precedent_targets = {edge['target'] for edge in edges if edge['kind'] == 'PRECEDENT_FOR'}
        result = [row for row in reachable if row['decision_id'] not in precedent_targets]
        if not result and decision_id not in precedent_targets:
            result = [{'decision_id': decision_id, 'depth': 0, 'relationship': 'ROOT', 'weight': 1.0}]
    elif operation == 'score':
        if not decision_id:
            raise ValueError('score 需要 decision_id')
        descendants = _compat_walk(decision_id, edges, kinds=causal_kinds, direction='downstream', max_depth=max_depth)
        result = max((float(row['weight']) for row in descendants), default=0.0)
    elif operation == 'network':
        causal_edges = [edge for edge in edges if edge['kind'] in causal_kinds]
        result = {
            'node_count': len(nodes),
            'edge_count': len(causal_edges),
            'edge_types': sorted({edge['kind'] for edge in causal_edges}),
        }
    else:
        raise ValueError('未知分析操作')
    return {
        'operation': operation,
        'engine': 'deterministic-compatible (Semantica package unavailable)',
        'result': result,
        'score_notice': '兼容分析使用确定性图算法；评分和权重不是故障概率或已验证的因果效应',
    }


def analyze(ontology_id, operation, decision_id=None, target_id=None,
            direction='downstream', max_depth=5, at_time=None):
    if not 1 <= max_depth <= 20 or direction not in ('upstream', 'downstream'):
        raise ValueError('无效方向或深度（1–20）')
    adapter = DecisionGraphAdapter(ontology_id)
    try:
        from semantica.context.causal_analyzer import CausalChainAnalyzer
        from semantica.context.context_graph import ContextGraph
    except ModuleNotFoundError:
        return _compat_analyze(adapter, operation, decision_id, target_id, direction, max_depth, at_time)

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
