import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import { apiClientV2, getApiErrorStatus } from '@/api/client';
import { compactRelationshipLabel, curveClassName, curveStyleRules, deterministicGraphPositions, parallelCurveOffsets } from '@/lib/relationshipGraph';
import ObjectPanel, { type ObjectPanelRecord } from './ObjectPanel';
import type { ExplorerContext } from './ExplorerOperations';
import { explorerError } from './runtime';
import type { ObjectSetExpression } from './contract';

type Identity = { object_type: string; object_id: string };
type Edge = { source: Identity; target: Identity; relation_type: string };
type Graph = { objects: ObjectPanelRecord[]; edges: Edge[] };
type Catalog = { links: Array<{ api_name: string; source_type: string; target_type: string }> };
type Layer = { color: string; visible: boolean };
type Saved = { id: string; head_version: number };

const key = (item: Identity) => JSON.stringify([item.object_type, item.object_id]);
const edgeKey = (edge: Edge) => `${key(edge.source)}|${edge.relation_type}|${key(edge.target)}`;
const PALETTE = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#4b5563'];
const stamp = (item: ObjectPanelRecord) => typeof item.properties.start_timestamp === 'string' ? Date.parse(item.properties.start_timestamp) : NaN;

/** Vertex-style graph: several typed Object Sets, Search Around, layers, time cursor, saved versions. */
export default function ObjectGraph({ ontologyId, expression, context }: { ontologyId: string; expression: ObjectSetExpression; context: ExplorerContext }) {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const base = JSON.stringify(expression);
  // Search Around sets belong to the expression they were added to; a new expression starts a new graph.
  const [extra, setExtra] = useState<{ base: string; sets: ObjectSetExpression[] }>({ base, sets: [] });
  const sets = useMemo(() => [expression, ...(extra.base === base ? extra.sets : [])], [expression, extra, base]);
  const [selected, setSelected] = useState<ObjectPanelRecord | null>(null);
  const [layerOverrides, setLayerOverrides] = useState<Record<string, Partial<Layer>>>({});
  const [cursor, setCursor] = useState<number | null>(null);
  const [showRelationshipLabels, setShowRelationshipLabels] = useState(true);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [name, setName] = useState('');
  const [notice, setNotice] = useState('');

  const result = useQuery({ queryKey: ['explorer-graph', ontologyId, sets, context], enabled: !!context.data_view_id,
    queryFn: ({ signal }) => apiClientV2.post<Graph>(`/ontologies/${ontologyId}/object-query/graph:multi`, { sets, context }, { signal }),
  });
  const catalog = useQuery({ queryKey: ['explorer-catalog', ontologyId],
    queryFn: ({ signal }) => apiClientV2.get<Catalog>(`/ontologies/${ontologyId}/object-query/catalog`, { signal }) });

  const types = useMemo(() => [...new Set(result.data?.objects.map(o => o.object_type) ?? [])].sort(), [result.data]);
  const layers = useMemo(() => Object.fromEntries(types.map((type, index) => [type, {
    color: PALETTE[index % PALETTE.length], visible: true, ...layerOverrides[type] }])) as Record<string, Layer>, [types, layerOverrides]);
  const times = useMemo(() => (result.data?.objects.map(stamp) ?? []).filter(t => !Number.isNaN(t)).sort((a, b) => a - b), [result.data]);
  const effectiveCursor = cursor ?? (times.length ? times[times.length - 1] : null);
  const visible = useMemo(() => (result.data?.objects ?? []).filter(o => layers[o.object_type]?.visible !== false &&
    (effectiveCursor === null || Number.isNaN(stamp(o)) || stamp(o) <= effectiveCursor)), [result.data, layers, effectiveCursor]);

  useEffect(() => {
    if (!ref.current || !result.data) return;
    const objects = new Map(visible.map(item => [key(item), item]));
    // Deterministic order and positions: the same cohort always lays out the same way.
    const orderedObjects = [...visible].sort((left, right) => key(left).localeCompare(key(right)));
    const orderedEdges = result.data.edges.filter(edge => objects.has(key(edge.source)) && objects.has(key(edge.target)))
      .sort((left, right) => edgeKey(left).localeCompare(edgeKey(right)));
    const positions = deterministicGraphPositions(orderedObjects, orderedEdges, key, edge => key(edge.source), edge => key(edge.target));
    const curveOffsets = parallelCurveOffsets(orderedEdges, (_edge, index) => `edge:${index}`, edge => key(edge.source), edge => key(edge.target));
    const cy = cytoscape({ container: ref.current,
      elements: [...orderedObjects.map(item => ({ data: { id: key(item), label: `${item.object_type} · ${item.object_id}`, color: layers[item.object_type]?.color ?? '#2563eb' },
                                                   position: positions.get(key(item)) })),
        ...orderedEdges.map((edge, index) => {
          const id = `edge:${index}`;
          return { data: { id, source: key(edge.source), target: key(edge.target), label: compactRelationshipLabel(edge.relation_type) },
                   classes: curveClassName(curveOffsets.get(id) || 0) };
        })],
      layout: { name: 'preset', fit: false, padding: 35 },
      style: [{ selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', color: '#334155', 'font-size': 8, 'text-valign': 'bottom',
                                           'text-halign': 'center', 'text-margin-y': 5, width: 24, height: 24, shape: 'ellipse', 'text-wrap': 'wrap',
                                           'text-max-width': '116px', 'border-width': 1, 'border-color': '#ffffff' } },
        { selector: 'edge', style: {
          label: showRelationshipLabels ? 'data(label)' : '', color: '#475569', 'font-size': 7, 'font-weight': 500, 'text-rotation': 'autorotate',
          'text-background-color': '#ffffff', 'text-background-opacity': 0.94, 'text-background-padding': '2px', 'text-border-width': 1,
          'text-border-color': '#e2e8f0', 'text-border-opacity': 0.95, 'text-wrap': 'ellipsis', 'text-max-width': '96px', width: 1,
          'line-color': '#94a3b8', 'target-arrow-color': '#94a3b8', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.65,
          'curve-style': 'unbundled-bezier', 'control-point-distance': 0, 'control-point-weight': 0.5 } },
        ...curveStyleRules(curveOffsets.values())],
    });
    cyRef.current = cy;
    cy.fit(cy.elements(), 42);
    cy.on('tap', 'node', event => setSelected(objects.get(event.target.id()) ?? null));
    const observer = new ResizeObserver(() => { cy.resize(); cy.fit(cy.elements(), 42); }); observer.observe(ref.current);
    return () => { observer.disconnect(); cy.destroy(); cyRef.current = null; };
  }, [result.data, visible, layers, showRelationshipLabels]);

  const aroundOptions = selected ? (catalog.data?.links ?? []).flatMap(link => [
    ...(link.source_type === selected.object_type ? [{ link: link.api_name, direction: 'out' as const, label: `${link.api_name} → ${link.target_type}` }] : []),
    ...(link.target_type === selected.object_type ? [{ link: link.api_name, direction: 'in' as const, label: `${link.source_type} → ${link.api_name}` }] : []),
  ]) : [];

  const searchAround = (value: string) => {
    if (!selected || !value) return;
    const option = aroundOptions[Number(value)];
    const set = { kind: 'traverse', input: { kind: 'static', type_ref: { api_name: selected.object_type }, object_ids: [selected.object_id] },
                  link: { api_name: option.link, direction: option.direction } } as unknown as ObjectSetExpression;
    setExtra(current => ({ base, sets: [...(current.base === base ? current.sets : []), set] }));
  };

  const save = async () => {
    const positions = Object.fromEntries((cyRef.current?.nodes() ?? []).map(node => [node.id(), [node.position('x'), node.position('y')]]));
    const definition = { sets, data_view_id: context.data_view_id, positions, layers,
                         time_cursor: effectiveCursor === null ? null : new Date(effectiveCursor).toISOString() };
    try {
      const body = saved
        ? await apiClientV2.post<Saved & { unchanged: boolean }>(`/ontologies/${ontologyId}/graph-views/${saved.id}/versions`, { expected_version: saved.head_version, definition })
        : await apiClientV2.post<Saved>(`/ontologies/${ontologyId}/graph-views`, { name: name.trim(), definition });
      setSaved({ id: body.id, head_version: body.head_version });
      setNotice('unchanged' in body && body.unchanged ? '图没有变化，未生成新版本' : `已保存为第 ${body.head_version} 版`);
    } catch (error) {
      setNotice(getApiErrorStatus(error) === 409 ? '图已被他人更新，请重新打开后再保存' : explorerError(error));
    }
  };

  if (!context.data_view_id) return <p className="p-4">固定当前数据后即可打开对象关系图。</p>;
  return <section aria-label="对象关系图">
    <div className="flex flex-wrap items-center gap-3 p-3 text-xs">
      <span>固定视图 {context.data_view_id} · {sets.length} 个对象集合</span>
      <fieldset className="flex flex-wrap gap-2" aria-label="图层"><legend className="sr-only">图层</legend>
        {types.map(type => <label key={type} className="flex items-center gap-1">
          <input type="checkbox" checked={layers[type].visible} onChange={event => setLayerOverrides(current => ({ ...current, [type]: { ...current[type], visible: event.target.checked } }))} />
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: layers[type].color }} />{type}</label>)}
      </fieldset>
      {times.length > 1 && <label className="flex items-center gap-1">时间
        <input type="range" aria-label="时间游标" min={times[0]} max={times[times.length - 1]} step={3600_000} value={effectiveCursor ?? times[0]}
               onChange={event => setCursor(Number(event.target.value))} />
        <time>{effectiveCursor !== null ? new Date(effectiveCursor).toISOString().slice(0, 16).replace('T', ' ') : ''}</time></label>}
      {extra.base === base && extra.sets.length > 0 && <button type="button" onClick={() => setExtra({ base, sets: extra.sets.slice(0, -1) })}>撤销上一步 Search Around</button>}
      <button type="button" onClick={() => setShowRelationshipLabels(value => !value)}
              className={`rounded-lg border px-3 py-1.5 text-xs ${showRelationshipLabels ? 'border-slate-300 bg-white text-slate-700' : 'border-slate-800 bg-slate-800 text-white'}`}
              title={showRelationshipLabels ? '隐藏关系文字，保留关系线' : '显示关系文字'} aria-pressed={showRelationshipLabels}
              data-testid="toggle-object-relationship-labels">{showRelationshipLabels ? '隐藏关系文字' : '显示关系文字'}</button>
      {!saved && <input aria-label="图名称" placeholder="图名称" value={name} onChange={event => setName(event.target.value)} />}
      <button type="button" disabled={!saved && !name.trim()} onClick={() => void save()}>{saved ? `保存新版本（当前 v${saved.head_version}）` : '保存图'}</button>
      {notice && <span role="status">{notice}</span>}
    </div>
    {result.isPending && <p role="status">正在加载关系图…</p>}{result.error && <p role="alert">{explorerError(result.error)}</p>}
    <div className="flex"><div ref={ref} className="h-[440px] min-w-0 flex-1" />
      {selected && <div className="flex flex-col">
        {aroundOptions.length > 0 && <label className="p-2 text-xs">Search Around
          <select aria-label="从选中对象 Search Around" value="" onChange={event => searchAround(event.target.value)}>
            <option value="">选择关系…</option>{aroundOptions.map((option, index) => <option key={option.label} value={index}>{option.label}</option>)}
          </select></label>}
        <ObjectPanel object={selected} onClose={() => setSelected(null)} ontologyId={ontologyId} dataViewId={context.data_view_id} />
      </div>}
    </div>
    <div className="flex flex-wrap gap-2 p-3" aria-label="图对象列表">{visible.map(item => <button key={key(item)} onClick={() => setSelected(item)}>{item.object_type} · {item.object_id}</button>)}</div>
  </section>;
}
