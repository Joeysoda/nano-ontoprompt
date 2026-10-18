import { useEffect, useState } from 'react';
import { apiClientV2 } from '@/api/client';
import { explorerError } from './runtime';
import type { ObjectPanelRecord } from './ObjectPanel';

type CatalogType = { kind: string; api_name: string; properties: Array<{ api_name: string }> };
type Catalog = { types: CatalogType[]; links: Array<{ api_name: string; source_type: string; target_type: string }> };
type EventRow = { object_type: string; object_id: string; properties: Record<string, unknown> };

const PAGE = 20;

/** Event Objects linked to this object (Vertex: events are Object Types with start/end timestamps).
 *  Mounted with a per-object key, so state starts fresh for every object. */
export default function ObjectEvents({ ontologyId, dataViewId, object }: { ontologyId: string; dataViewId: string; object: ObjectPanelRecord }) {
  const [rows, setRows] = useState<EventRow[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    (async () => {
      const catalog = await apiClientV2.get<Catalog>(`/ontologies/${ontologyId}/object-query/catalog`, { signal });
      const isEvent = (name: string) => {
        const props = new Set(catalog.types.find(type => type.api_name === name)?.properties.map(p => p.api_name));
        return props.has('start_timestamp') && props.has('end_timestamp');
      };
      const links = catalog.links.filter(link => link.target_type === object.object_type && isEvent(link.source_type));
      const pages = await Promise.all(links.map(link => apiClientV2.post<{ objects: EventRow[]; page: { has_more: boolean } }>(
        `/ontologies/${ontologyId}/object-query/load`, {
          expression: { kind: 'traverse', input: { kind: 'static', type_ref: { api_name: object.object_type }, object_ids: [object.object_id] },
                        link: { api_name: link.api_name, direction: 'in' } },
          context: { ontology_id: ontologyId, consistency: 'snapshot', data_view_id: dataViewId },
          read: { page_size: PAGE, order_by: [{ property: { api_name: 'start_timestamp' }, direction: 'desc' }] },
        }, { signal })));
      const merged = pages.flatMap(page => page.objects)
        .sort((a, b) => String(b.properties.start_timestamp).localeCompare(String(a.properties.start_timestamp)));
      setTruncated(pages.some(page => page.page.has_more) || merged.length > PAGE);
      setRows(merged.slice(0, PAGE));
    })().catch(err => { if (!signal.aborted) setError(explorerError(err)); });
    return () => controller.abort();
  }, [ontologyId, dataViewId, object.object_type, object.object_id]);

  return <section className="border-t px-3 py-3" aria-label="关联事件">
    <p className="mb-2 text-xs font-medium text-slate-500">关联事件{rows ? ` · ${rows.length}${truncated ? '+' : ''}` : ''}</p>
    {rows === null && !error && <p role="status" className="text-xs text-slate-500">正在加载事件…</p>}
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {rows && rows.length === 0 && <p className="text-xs text-slate-500">此对象在当前视图中没有关联事件。</p>}
    <ol className="space-y-1">
      {rows?.map(row => <li key={row.object_id} className="flex items-center justify-between gap-2 text-xs" data-testid="linked-event">
        <span className="truncate"><strong className="mr-1">{row.object_type}</strong>{String(row.properties.code ?? '')}</span>
        <time className="shrink-0 text-slate-500" dateTime={String(row.properties.start_timestamp)}>
          {new Date(String(row.properties.start_timestamp)).toLocaleString('zh-CN', { hour12: false })}
        </time>
      </li>)}
    </ol>
    {truncated && <p className="mt-1 text-xs text-slate-500">仅显示最近 {PAGE} 条；完整列表请在 Explorer 中按关系 Search Around。</p>}
  </section>;
}
