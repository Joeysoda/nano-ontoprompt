/** Shared display helpers for relationship graphs.
 *
 * The graph always keeps its relationship geometry.  Callers can switch only
 * the label style, which makes dense ontology views reversible without hiding
 * data or breaking edge selection.
 */
export function compactRelationshipLabel(value: unknown, maxLength = 24) {
  return String(value || "关联").replace(/_/g, " ").slice(0, maxLength);
}

export function curveClassName(offset: number) {
  return `curve-${offset < 0 ? "negative" : offset > 0 ? "positive" : "center"}-${Math.abs(offset)}`;
}

export function curveStyleRules(offsets: Iterable<number>) {
  return Array.from(new Set(offsets), (offset) => ({
    selector: `.${curveClassName(offset)}`,
    style: { "control-point-distance": offset },
  }));
}

export function parallelCurveOffsets<T>(
  items: T[],
  getId: (item: T, index: number) => string,
  getSource: (item: T) => string,
  getTarget: (item: T) => string,
  spacing = 68,
) {
  const counts = new Map<string, number>();
  for (const item of items) {
    const pair = [getSource(item), getTarget(item)].sort().join("\u0000");
    counts.set(pair, (counts.get(pair) || 0) + 1);
  }
  const seen = new Map<string, number>();
  const offsets = new Map<string, number>();
  items.forEach((item, index) => {
    const pair = [getSource(item), getTarget(item)].sort().join("\u0000");
    const ordinal = seen.get(pair) || 0;
    seen.set(pair, ordinal + 1);
    const total = counts.get(pair) || 1;
    offsets.set(getId(item, index), total === 1 ? 0 : (ordinal - (total - 1) / 2) * spacing);
  });
  return offsets;
}

export type GraphPosition = { x: number; y: number };

const compareStable = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

/**
 * Calculate a repeatable, topology-aware position for every graph node.
 *
 * Force-directed layouts are useful while exploring, but their random initial
 * state makes a demo move every time it is opened.  This helper puts each
 * connected component around its highest-degree node and orders every ring by
 * the stable node id.  The result is deterministic for the same node/edge
 * set and can be used with Cytoscape's `preset` layout.
 */
export function deterministicGraphPositions<TNode, TEdge>(
  nodes: TNode[],
  edges: TEdge[],
  getNodeId: (node: TNode) => string,
  getSource: (edge: TEdge) => string,
  getTarget: (edge: TEdge) => string,
  spacing = 190,
) {
  const nodeIds = Array.from(new Set(nodes.map(getNodeId).map(String))).sort(compareStable);
  const knownIds = new Set(nodeIds);
  const adjacency = new Map<string, Set<string>>(
    nodeIds.map((id) => [id, new Set<string>()]),
  );
  edges.forEach((edge) => {
    const source = String(getSource(edge));
    const target = String(getTarget(edge));
    if (!knownIds.has(source) || !knownIds.has(target) || source === target) return;
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  });

  const degree = new Map(nodeIds.map((id) => [id, adjacency.get(id)?.size || 0]));
  const remaining = new Set(nodeIds);
  const components: string[][] = [];
  while (remaining.size) {
    const seed = Array.from(remaining).sort(compareStable)[0];
    const queue = [seed];
    const component: string[] = [];
    remaining.delete(seed);
    while (queue.length) {
      const current = queue.shift()!;
      component.push(current);
      Array.from(adjacency.get(current) || [])
        .sort(compareStable)
        .forEach((neighbor) => {
          if (!remaining.has(neighbor)) return;
          remaining.delete(neighbor);
          queue.push(neighbor);
        });
    }
    components.push(component.sort(compareStable));
  }
  components.sort((left, right) => compareStable(left[0], right[0]));

  const depthsByComponent = components.map((component) => {
    const root = [...component].sort(
      (left, right) =>
        (degree.get(right) || 0) - (degree.get(left) || 0) ||
        compareStable(left, right),
    )[0];
    const depths = new Map<string, number>([[root, 0]]);
    const queue = [root];
    while (queue.length) {
      const current = queue.shift()!;
      Array.from(adjacency.get(current) || [])
        .sort(compareStable)
        .forEach((neighbor) => {
          if (depths.has(neighbor)) return;
          depths.set(neighbor, (depths.get(current) || 0) + 1);
          queue.push(neighbor);
        });
    }
    return { component, depths, maxDepth: Math.max(...depths.values(), 0) };
  });

  // Long chains otherwise become extremely tall after Cytoscape fits the
  // graph. Keep the same deterministic order while compressing only the
  // radial distance needed by deep components. Dense, shallow graphs (such
  // as the wood demo) retain the original spacing and remain readable.
  const deepestComponent = Math.max(
    ...depthsByComponent.map(({ maxDepth }) => maxDepth),
    0,
  );
  const effectiveSpacing = Math.max(
    112,
    Math.min(spacing, 980 / Math.max(1, deepestComponent)),
  );
  const blockSize = Math.max(
    effectiveSpacing * 4,
    ...depthsByComponent.map(({ maxDepth }) => (maxDepth + 1) * effectiveSpacing * 2 + effectiveSpacing),
  );
  const columns = Math.max(1, Math.ceil(Math.sqrt(depthsByComponent.length)));
  const positions = new Map<string, GraphPosition>();

  depthsByComponent.forEach(({ component, depths }, componentIndex) => {
    const column = componentIndex % columns;
    const row = Math.floor(componentIndex / columns);
    const center = {
      x: column * blockSize + blockSize / 2,
      y: row * blockSize + blockSize / 2,
    };
    const rings = new Map<number, string[]>();
    component.forEach((id) => {
      const depth = depths.get(id) || 0;
      rings.set(depth, [...(rings.get(depth) || []), id]);
    });
    rings.forEach((ids, depth) => {
      if (depth === 0) {
        ids.forEach((id) => positions.set(id, center));
        return;
      }
      ids.sort(compareStable).forEach((id, index) => {
        const angle = -Math.PI / 2 + (index / ids.length) * Math.PI * 2;
        const radius = depth * effectiveSpacing;
        positions.set(id, {
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
        });
      });
    });
  });

  return positions;
}
