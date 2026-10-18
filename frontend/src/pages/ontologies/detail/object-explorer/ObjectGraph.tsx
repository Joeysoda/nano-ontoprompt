import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import { apiClientV2 } from '@/api/client';
import ObjectPanel, { type ObjectPanelRecord } from './ObjectPanel';
import type { ExplorerContext } from './ExplorerOperations';
import { explorerError } from './runtime';
import type { ObjectSetExpression } from './contract';
import { compactRelationshipLabel, curveClassName, curveStyleRules, deterministicGraphPositions, parallelCurveOffsets } from '@/lib/relationshipGraph';

type Identity = { object_type: string; object_id: string };
type Graph = { objects: ObjectPanelRecord[]; edges: Array<{ source: Identity; target: Identity; relation_type: string }> };
const key = (item: Identity) => JSON.stringify([item.object_type, item.object_id]);

export default function ObjectGraph({ ontologyId, expression, context }: { ontologyId: string; expression: ObjectSetExpression; context: ExplorerContext }) {
  const ref = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<ObjectPanelRecord | null>(null);
  const [showRelationshipLabels, setShowRelationshipLabels] = useState(true);
  const result = useQuery({ queryKey: ['explorer-graph', ontologyId, expression, context], enabled: !!context.data_view_id,
    queryFn: ({ signal }) => apiClientV2.post<Graph>(`/ontologies/${ontologyId}/object-query/graph`, { expression, context }, { signal }),
  });
  useEffect(() => {
    if (!ref.current || !result.data) return;
    const objects = new Map(result.data.objects.map(item => [key(item), item]));
    const orderedObjects = [...result.data.objects].sort((left, right) => key(left).localeCompare(key(right)));
    const orderedEdges = [...result.data.edges].sort((left, right) => `${key(left.source)}|${left.relation_type}|${key(left.target)}`.localeCompare(`${key(right.source)}|${right.relation_type}|${key(right.target)}`));
    const stablePositions = deterministicGraphPositions(orderedObjects, orderedEdges, key, edge => key(edge.source), edge => key(edge.target));
    const curveOffsets = parallelCurveOffsets(orderedEdges, (_edge, index) => `edge:${index}`, edge => key(edge.source), edge => key(edge.target));
    const cy = cytoscape({ container: ref.current,
      elements: [...orderedObjects.map(item => ({ data: { id: key(item), label: `${item.object_type} · ${item.object_id}` }, position: stablePositions.get(key(item)) })),
        ...orderedEdges.map((edge, index) => {
          const id = `edge:${index}`;
          const curveOffset = curveOffsets.get(id) || 0;
          return { data: { id, source: key(edge.source), target: key(edge.target), label: compactRelationshipLabel(edge.relation_type), raw: edge }, classes: curveClassName(curveOffset) };
        })],
      layout: { name: 'preset', fit: false, padding: 35 },
      style: [ { selector: 'node', style: { label: 'data(label)', 'background-color': '#2563eb', color: '#334155', 'font-size': 8, 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 5, width: 24, height: 24, shape: 'ellipse', 'text-wrap': 'wrap', 'text-max-width': '116px', 'border-width': 1, 'border-color': '#ffffff' } },
        { selector: 'edge', style: {
          label: showRelationshipLabels ? 'data(label)' : '',
          color: '#475569',
          'font-size': 7,
          'font-weight': 500,
          'text-rotation': 'autorotate',
          'text-background-color': '#ffffff',
          'text-background-opacity': 0.94,
          'text-background-padding': '2px',
          'text-border-width': 1,
          'text-border-color': '#e2e8f0',
          'text-border-opacity': 0.95,
          'text-wrap': 'ellipsis',
          'text-max-width': '96px',
          width: 1,
          'line-color': '#94a3b8',
          'target-arrow-color': '#94a3b8',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.65,
          'curve-style': 'unbundled-bezier',
          'control-point-distance': 0,
          'control-point-weight': 0.5,
        } }, ...curveStyleRules(curveOffsets.values()) ],
    });
    cy.fit(cy.elements(), 42);
    cy.on('tap', 'node', event => setSelected(objects.get(event.target.id()) ?? null));
    cy.one('layoutstop', () => cy.fit(cy.elements(), 42));
    const observer = new ResizeObserver(() => { cy.resize(); cy.fit(cy.elements(), 42); }); observer.observe(ref.current);
    return () => { observer.disconnect(); cy.destroy(); };
  }, [result.data, showRelationshipLabels]);
  if (!context.data_view_id) return <p className="p-4">固定当前数据后即可打开对象关系图。</p>;
  return <section aria-label="对象关系图"><div className="flex flex-wrap items-center justify-between gap-2 p-3"><p className="text-xs">固定视图 {context.data_view_id} · 当前集合内部关系；通过 Search Around 扩展范围。</p><button onClick={() => setShowRelationshipLabels(value => !value)} className={`rounded-lg border px-3 py-1.5 text-xs ${showRelationshipLabels ? 'border-slate-300 bg-white text-slate-700' : 'border-slate-800 bg-slate-800 text-white'}`} title={showRelationshipLabels ? '隐藏关系文字，保留关系线' : '显示关系文字'} aria-pressed={showRelationshipLabels} data-testid="toggle-object-relationship-labels">{showRelationshipLabels ? '隐藏关系文字' : '显示关系文字'}</button></div>
    {result.isPending && <p role="status">正在加载关系图…</p>}{result.error && <p role="alert">{explorerError(result.error)}</p>}
    <div className="flex"><div ref={ref} className="h-[440px] min-w-0 flex-1" />{selected && <ObjectPanel object={selected} onClose={() => setSelected(null)} ontologyId={ontologyId} dataViewId={context.data_view_id} />}</div>
    <div className="flex flex-wrap gap-2 p-3" aria-label="图对象列表">{result.data?.objects.map(item => <button key={key(item)} onClick={() => setSelected(item)}>{item.object_type} · {item.object_id}</button>)}</div>
  </section>;
}
