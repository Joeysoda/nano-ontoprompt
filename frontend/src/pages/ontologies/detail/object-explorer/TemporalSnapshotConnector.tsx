import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { apiClientV2 } from '@/api/client';
import { explorerError } from './runtime';
import type { Catalog } from './ExplorerOperations';

type Snapshot = { id: string; status: string; snapshot_hash: string; event_count: number };
type Mapping = { event_type: string; start_field: string; end_field: string; subject_type: string; subject_field: string; event_link: string; sensor_type: string; sensor_field: string; sensor_link: string; tsp_property: string; value_field: string; unit: string; interpolation: string };
const empty: Mapping = { event_type: '', start_field: '', end_field: '', subject_type: '', subject_field: '', event_link: '', sensor_type: '', sensor_field: '', sensor_link: '', tsp_property: '', value_field: '', unit: '', interpolation: 'none' };

export default function TemporalSnapshotConnector({ ontologyId, snapshotId, payload }: { ontologyId: string; snapshotId: string; payload: Record<string, unknown> }) {
  const navigate = useNavigate();
  const [mapping, setMapping] = useState<Mapping>(empty);
  const [withEvents, setWithEvents] = useState(false);
  const root = `/ontologies/${ontologyId}/object-query`;
  const snapshot = useQuery({ queryKey: ['temporal-snapshot', ontologyId, snapshotId], queryFn: ({ signal }) => apiClientV2.get<Snapshot>(`${root}/temporal-snapshots/${snapshotId}`, { signal }) });
  const catalog = useQuery({ queryKey: ['object-query-types', ontologyId], queryFn: ({ signal }) => apiClientV2.get<Catalog>(`${root}/catalog`, { signal }) });
  const publish = useMutation({ mutationFn: () => apiClientV2.post<{ view_id: string }>(`${root}/data-views`, {
    source_ontology_id: ontologyId, source_manifest_digest: snapshot.data!.snapshot_hash, source_snapshot_id: snapshotId,
    ...(withEvents ? { temporal_surface: { ...mapping, ...(mapping.sensor_type ? {} : { sensor_type: null, sensor_field: null, sensor_link: null, tsp_property: null, value_field: null, unit: null, interpolation: null }) } } : {}),
  }), onSuccess: view => navigate(`/ontologies/${ontologyId}?tab=objects&dataView=${encodeURIComponent(view.view_id)}`) });
  const fields = Object.keys(payload);
  const update = (key: keyof Mapping, value: string) => setMapping(current => ({ ...current, [key]: value }));
  const types = catalog.data?.types.filter(item => item.kind === 'object') ?? [];
  const eventLinks = catalog.data?.links.filter(item => item.source_type === mapping.event_type) ?? [];
  const sensorLinks = catalog.data?.links.filter(item => item.source_type === mapping.sensor_type) ?? [];
  const canMap = !!(mapping.event_type && mapping.subject_type && mapping.event_link && mapping.start_field && mapping.end_field && mapping.subject_field && (!mapping.sensor_type || (mapping.sensor_field && mapping.sensor_link && mapping.tsp_property && mapping.value_field && mapping.unit)));
  return <section className="rounded-xl border bg-white p-4" aria-label="发布快照查询视图">
    <h3 className="font-semibold">在 Object Explorer 中查看已发布快照</h3>
    <p className="text-xs text-slate-500">快照 {snapshotId} · {snapshot.data?.event_count ?? '…'} 条已提交事件。Event 对象需要已发布的类型、关系和带时区的时间字段。</p>
    <label className="mt-3 flex gap-2"><input type="checkbox" checked={withEvents} onChange={event => setWithEvents(event.target.checked)} />映射为标准 Event Object</label>
    {withEvents && <div className="mt-3 grid gap-2 md:grid-cols-3">
      <label>Event 类型<select value={mapping.event_type} onChange={event => update('event_type', event.target.value)}><option value="">选择</option>{types.map(item => <option key={item.api_name}>{item.api_name}</option>)}</select></label>
      <label>业务对象类型<select value={mapping.subject_type} onChange={event => update('subject_type', event.target.value)}><option value="">选择</option>{types.map(item => <option key={item.api_name}>{item.api_name}</option>)}</select></label>
      <label>Event 关系<select value={mapping.event_link} onChange={event => update('event_link', event.target.value)}><option value="">选择</option>{eventLinks.filter(link => link.target_type === mapping.subject_type).map(link => <option key={link.api_name}>{link.api_name}</option>)}</select></label>
      {(['start_field', 'end_field', 'subject_field'] as const).map((key, index) => <label key={key}>{['开始时间字段', '结束时间字段', '业务对象 ID 字段'][index]}<select value={mapping[key]} onChange={event => update(key, event.target.value)}><option value="">选择</option>{fields.map(field => <option key={field}>{field}</option>)}</select></label>)}
      <label>Sensor 类型（可选）<select value={mapping.sensor_type} onChange={event => update('sensor_type', event.target.value)}><option value="">无时间序列</option>{types.map(item => <option key={item.api_name}>{item.api_name}</option>)}</select></label>
      {mapping.sensor_type && <>
        <label>Sensor ID 字段<select value={mapping.sensor_field} onChange={event => update('sensor_field', event.target.value)}><option value="">选择</option>{fields.map(field => <option key={field}>{field}</option>)}</select></label>
        <label>Sensor 关系<select value={mapping.sensor_link} onChange={event => update('sensor_link', event.target.value)}><option value="">选择</option>{sensorLinks.filter(link => link.target_type === mapping.subject_type).map(link => <option key={link.api_name}>{link.api_name}</option>)}</select></label>
        <label>时间序列属性<select value={mapping.tsp_property} onChange={event => update('tsp_property', event.target.value)}><option value="">选择</option>{types.find(item => item.api_name === mapping.subject_type)?.properties.filter(item => item.data_type === 'string').map(item => <option key={item.api_name}>{item.api_name}</option>)}</select></label>
        <label>数值字段<select value={mapping.value_field} onChange={event => update('value_field', event.target.value)}><option value="">选择</option>{fields.map(field => <option key={field}>{field}</option>)}</select></label>
        <label>单位<input value={mapping.unit} onChange={event => update('unit', event.target.value)} placeholder="例如 °C" /></label>
        <label>插值<select value={mapping.interpolation} onChange={event => update('interpolation', event.target.value)}><option value="none">不插值</option><option value="step">阶梯</option><option value="linear">线性</option></select></label>
      </>}
    </div>}
    {!types.length && withEvents && <p role="status">当前本体尚无可用的已发布事件类型。</p>}
    {snapshot.error && <p role="alert">{explorerError(snapshot.error)}</p>}{publish.error && <p role="alert">{explorerError(publish.error)}</p>}
    <button className="mt-3 rounded bg-slate-900 px-3 py-2 text-white disabled:opacity-50" disabled={snapshot.data?.status !== 'published' || publish.isPending || (withEvents && !canMap)} onClick={() => publish.mutate()}>{publish.isPending ? '正在建立查询视图…' : '打开已发布快照'}</button>
  </section>;
}
