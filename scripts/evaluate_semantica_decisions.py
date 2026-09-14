"""Read-only upstream decision capability probes; no business records are created.

Run with the same Semantica installation as the backend. JSON reports include
failures rather than treating an unsupported backend as an empty causal chain.
"""
import inspect
import json
from dataclasses import asdict, is_dataclass


def evaluate():
    from semantica.context.causal_analyzer import CausalChainAnalyzer
    from semantica.context.context_graph import ContextGraph

    graph = ContextGraph()
    signatures = {
        name: str(inspect.signature(getattr(CausalChainAnalyzer, name)))
        for name in dir(CausalChainAnalyzer)
        if not name.startswith('_') and callable(getattr(CausalChainAnalyzer, name))
    }
    report = {'public_methods': signatures, 'checks': []}
    for i in range(1, 5):
        graph.add_node(node_id=f'D{i}', node_type='decision', content=f'Test decision {i}',
                       scenario=f'Test decision {i}', confidence=0.8)
    for source, target, kind, recorded in [
        ('D1', 'D2', 'CAUSED', '2026-01-01T00:00:00Z'),
        ('D2', 'D3', 'INFLUENCED', '2026-01-03T00:00:00Z'),
        ('D1', 'D4', 'ABOUT', '2026-01-01T00:00:00Z'),
    ]:
        graph.add_edge(source_id=source, target_id=target, edge_type=kind,
                       weight=0.8, recorded_at=recorded)
    analyzer = CausalChainAnalyzer(graph)

    def check(name, call, expected):
        try:
            output = call()
            if isinstance(output, list):
                output = [asdict(x) if is_dataclass(x) else x for x in output]
            report['checks'].append({'name': name, 'passed': expected(output), 'output': output})
        except Exception as exc:
            report['checks'].append({'name': name, 'passed': False,
                                     'error': f'{type(exc).__name__}: {exc}'})

    check('directed_distance', lambda: analyzer.interpret_causal_distance('D1', 'D3'),
          lambda r: r['causal_path'] == ['D1', 'D2', 'D3'] and r['confidence_decay'] == 0.64)
    check('reverse_unreachable', lambda: analyzer.interpret_causal_distance('D3', 'D1'),
          lambda r: r['causal_path'] == [])
    check('ordinary_relation_excluded', lambda: analyzer.interpret_causal_distance('D1', 'D4'),
          lambda r: r['causal_path'] == [])
    check('time_cutoff', lambda: analyzer.trace_at_time('D1', '2026-01-02T00:00:00Z', 'downstream'),
          lambda r: [x['decision_id'] for x in r] == ['D2'])
    check('downstream', lambda: analyzer.get_causal_chain('D1', 'downstream'),
          lambda r: {x['decision_id'] for x in r} == {'D2', 'D3'})
    for name, args in [('get_influenced_decisions', ('D1',)),
                       ('get_precedent_chain', ('D1',)), ('find_causal_loops', ()),
                       ('find_root_causes', ('D3',)), ('analyze_causal_network', ())]:
        check(name, lambda n=name, a=args: getattr(analyzer, n)(*a), lambda r: r is not None)
    return report


if __name__ == '__main__':
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2, default=str))
