"""Bounded, domain-neutral quantity propagation over an explicitly supplied graph."""
from collections import defaultdict, deque
from math import isfinite


class PropagationError(ValueError): pass


def propagate(nodes, edges, seeds, *, max_depth=20, minimum=0.0):
    if not 1 <= max_depth <= 100 or minimum < 0: raise PropagationError("invalid bounds")
    ids = {str(row["id"]) for row in nodes}
    if len(ids) != len(nodes): raise PropagationError("duplicate node")
    graph = defaultdict(list)
    for edge in edges:
        source, target = str(edge["source"]), str(edge["target"])
        factor, delay = float(edge.get("factor", 1)), float(edge.get("delay", 0))
        if source not in ids or target not in ids or not isfinite(factor) or factor < 0 or not isfinite(delay) or delay < 0:
            raise PropagationError("invalid edge")
        graph[source].append((target, factor, delay, str(edge.get("id", f"{source}:{target}"))))
    impacts, paths, queue = defaultdict(float), [], deque()
    for seed in seeds:
        node, value = str(seed["node"]), float(seed["value"])
        if node not in ids or not isfinite(value): raise PropagationError("invalid seed")
        impacts[node] += value; queue.append((node, value, 0, 0.0, [node]))
    while queue:
        source, value, depth, elapsed, path = queue.popleft()
        if depth >= max_depth: continue
        for target, factor, delay, edge_id in graph[source]:
            amount = value * factor
            if abs(amount) < minimum: continue
            if target in path: raise PropagationError("cycle reached within propagation path")
            impacts[target] += amount
            next_path = [*path, target]
            paths.append({"edge_id": edge_id, "source": source, "target": target, "value": amount,
                          "depth": depth + 1, "elapsed": elapsed + delay, "path": next_path})
            queue.append((target, amount, depth + 1, elapsed + delay, next_path))
    return {"impacts": [{"node": key, "value": impacts[key]} for key in sorted(impacts)], "paths": paths,
            "complete": all(row["depth"] < max_depth for row in paths)}
