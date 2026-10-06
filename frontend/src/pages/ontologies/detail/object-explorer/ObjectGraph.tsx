import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import { apiClientV2 } from '@/api/client';
import ObjectPanel, { type ObjectPanelRecord } from './ObjectPanel';
import type { ExplorerContext } from './ExplorerOperations';
import { explorerError } from './runtime';
import type { ObjectSetExpression } from './contract';

type Identity = { object_type: string; object_id: string };
type Graph = { objects: ObjectPanelRecord[]; edges: Array<{ source: Identity; target: Identity; relation_type: string }> };
const key = (item: Identity) => JSON.stringify([item.object_type, item.object_id]);

export default function ObjectGraph({ ontologyId, expression, context }: { ontologyId: string; expression: ObjectSetExpression; context: ExplorerContext }) {
  const ref = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<ObjectPanelRecord | null>(null);
  const result = useQuery({ queryKey: ['explorer-graph', ontologyId, expression, context], enabled: !!context.data_view_id,
    queryFn: ({ signal }) => apiClientV2.post<Graph>(`/ontologies/${ontologyId}/object-query/graph`, { expression, context }, { signal }),
  });
  useEffect(() => {
    if (!ref.current || !result.data) return;
    const objects = new Map(result.data.objects.map(item => [key(item), item]));
    const cy = cytoscape({ container: ref.current,
      elements: [...result.data.objects.map(item => ({ data: { id: key(item), label: `${item.object_type} · ${item.object_id}` } })),
        ...result.data.edges.map((edge, index) => ({ data: { id: `edge:${index}`, source: key(edge.source), target: key(edge.target), label: edge.relation_type } }))],
      layout: { name: 'cose', animate: false },
      style: [ { selector: 'node', style: { label: 'data(label)', 'background-color': '#2563eb', 'font-size': 10 } },
        { selector: 'edge', style: { label: 'data(label)', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'font-size': 9 } } ],
    });
    cy.on('tap', 'node', event => setSelected(objects.get(event.target.id()) ?? null));
    const observer = new ResizeObserver(() => cy.resize()); observer.observe(ref.current);
    return () => { observer.disconnect(); cy.destroy(); };
  }, [result.data]);
  if (!context.data_view_id) return <p className="p-4">固定当前数据后即可打开对象关系图。</p>;
  return <section aria-label="对象关系图"><p className="p-3 text-xs">固定视图 {context.data_view_id} · 当前集合内部关系；通过 Search Around 扩展范围。</p>
    {result.isPending && <p role="status">正在加载关系图…</p>}{result.error && <p role="alert">{explorerError(result.error)}</p>}
    <div className="flex"><div ref={ref} className="h-[440px] min-w-0 flex-1" />{selected && <ObjectPanel object={selected} onClose={() => setSelected(null)} ontologyId={ontologyId} dataViewId={context.data_view_id} />}</div>
    <div className="flex flex-wrap gap-2 p-3" aria-label="图对象列表">{result.data?.objects.map(item => <button key={key(item)} onClick={() => setSelected(item)}>{item.object_type} · {item.object_id}</button>)}</div>
  </section>;
}
