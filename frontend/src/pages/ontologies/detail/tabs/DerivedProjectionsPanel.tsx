import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'

type Query = { api_name: string; latest_version: string; output_schema: { columns?: Array<{ name: string }> } }
type Asset = { asset_key: string; version: string; name: string; input_schema?: { required?: string[] } }
type Projection = { id: string; api_name: string; name: string; status: 'active' | 'paused' | 'retired'; interval_minutes: number | null; etag: number; freshness: { status: string; reason?: string }; schedule_error?: { message?: string } }

export default function DerivedProjectionsPanel({ ontologyId, assets }: { ontologyId: string; assets: Asset[] }) {
  const client = useQueryClient()
  const key = ['derived-projections', ontologyId]
  const projections = useQuery({ queryKey: key, queryFn: () => apiClientV2.get<{ projections: Projection[] }>(`/ontologies/${ontologyId}/derived-projections`) })
  const queries = useQuery({ queryKey: ['queries', ontologyId], queryFn: () => apiClientV2.get<{ queries: Query[] }>(`/ontologies/${ontologyId}/queries`) })
  const [name, setName] = useState('设备观测持续摘要')
  const [apiName, setApiName] = useState('equipment_observation_summary')
  const [queryName, setQueryName] = useState('')
  const [assetKey, setAssetKey] = useState('')
  const [interval, setInterval] = useState(15)
  const [mapping, setMapping] = useState('{\n  "fields": [\n    {"target":"value","source":["value"],"data_type":"number","required":true,"nullable":false}\n  ]\n}')
  const [inputs, setInputs] = useState('{\n  "values": {"kind":"column","field":"value"}\n}')
  const [notice, setNotice] = useState('')
  const selectedQuery = queries.data?.queries.find(row => row.api_name === queryName) ?? queries.data?.queries[0]
  const selectedAsset = assets.find(row => row.asset_key === assetKey) ?? assets[0]
  const refresh = () => client.invalidateQueries({ queryKey: key })
  const create = useMutation({ mutationFn: () => apiClientV2.post(`/ontologies/${ontologyId}/derived-projections`, {
    api_name: apiName, name, interval_minutes: interval,
    spec: { query_api_name: selectedQuery?.api_name, query_version: selectedQuery?.latest_version, query_params: {}, time_range: 'all', limit: 10000,
      mapping: JSON.parse(mapping), asset_key: selectedAsset?.asset_key, asset_version: selectedAsset?.version, inputs: JSON.parse(inputs), max_rows: 10000 },
  }), onSuccess: async () => { setNotice('派生投影已保存为暂停状态；检查后可启用调度。'); await refresh() }, onError: error => setNotice(formatApiError(error)) })
  const update = useMutation({ mutationFn: ({ row, status }: { row: Projection; status: 'active' | 'paused' }) => apiClientV2.patch(`/ontologies/${ontologyId}/derived-projections/${row.id}`, { expected_etag: row.etag, status, interval_minutes: row.interval_minutes ?? interval }), onSuccess: refresh, onError: error => setNotice(formatApiError(error)) })
  const run = useMutation({ mutationFn: (row: Projection) => apiClientV2.post(`/ontologies/${ontologyId}/derived-projections/${row.id}:run`), onSuccess: async () => { setNotice('派生投影运行完成，输出和依赖摘要已保存。'); await refresh() }, onError: error => setNotice(formatApiError(error)) })

  return <section className="rounded-xl border bg-white p-5" aria-label="持续派生计算">
    <h2 className="text-lg font-semibold">持续派生计算</h2><p className="mt-1 text-sm text-slate-500">把已发布 Query 的列显式映射到固定 Function 版本；按间隔运行，并显示数据、定义或依赖是否已过期。</p>
    <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4"><label className="text-xs">名称<input aria-label="派生投影名称" className="wb-input mt-1 w-full" value={name} onChange={event => setName(event.target.value)} /></label><label className="text-xs">API 名称<input aria-label="派生投影 API 名称" className="wb-input mt-1 w-full" value={apiName} onChange={event => setApiName(event.target.value)} /></label><label className="text-xs">已发布 Query<select aria-label="派生投影 Query" className="wb-input mt-1 w-full" value={selectedQuery?.api_name ?? ''} onChange={event => setQueryName(event.target.value)}>{queries.data?.queries.map(row => <option key={row.api_name} value={row.api_name}>{row.api_name} · {row.latest_version}</option>)}</select></label><label className="text-xs">固定 Function<select aria-label="派生投影 Function" className="wb-input mt-1 w-full" value={selectedAsset?.asset_key ?? ''} onChange={event => setAssetKey(event.target.value)}>{assets.map(row => <option key={`${row.asset_key}:${row.version}`} value={row.asset_key}>{row.name} · {row.version}</option>)}</select></label></div>
    <p className="mt-2 text-xs text-slate-500">Query 输出列：{selectedQuery?.output_schema.columns?.map(row => row.name).join('、') || '—'}；Function 必填输入：{selectedAsset?.input_schema?.required?.join('、') || '—'}</p>
    <div className="mt-3 grid gap-3 lg:grid-cols-2"><label className="text-xs">字段映射 JSON<textarea aria-label="派生字段映射 JSON" className="wb-input mt-1 min-h-36 w-full font-mono" value={mapping} onChange={event => setMapping(event.target.value)} /></label><label className="text-xs">Function 输入绑定 JSON<textarea aria-label="派生输入绑定 JSON" className="wb-input mt-1 min-h-36 w-full font-mono" value={inputs} onChange={event => setInputs(event.target.value)} /></label></div>
    <div className="mt-3 flex items-end gap-3"><label className="text-xs">运行间隔（分钟）<input aria-label="派生运行间隔" type="number" min={5} max={1440} className="wb-input mt-1 w-32" value={interval} onChange={event => setInterval(Number(event.target.value))} /></label><button className="wb-button-primary" disabled={!selectedQuery || !selectedAsset || create.isPending} onClick={() => create.mutate()}>保存派生投影</button></div>
    {notice && <p role="status" className="mt-3 text-sm text-slate-700">{notice}</p>}
    <div className="mt-5 space-y-2">{projections.data?.projections.map(row => <div key={row.id} className="flex flex-wrap items-center justify-between gap-3 rounded border p-3"><div><strong>{row.name}</strong><p className="text-xs text-slate-500">{row.api_name} · {row.status} · 每 {row.interval_minutes ?? '—'} 分钟 · freshness: {row.freshness.status}{row.freshness.reason ? `（${row.freshness.reason}）` : ''}</p>{row.schedule_error && <p className="text-xs text-red-700">{row.schedule_error.message}</p>}</div><div className="flex gap-2"><button className="wb-button-secondary" disabled={run.isPending || row.status === 'retired'} onClick={() => run.mutate(row)}>立即运行</button><button className="wb-button-secondary" disabled={update.isPending || row.status === 'retired'} onClick={() => update.mutate({ row, status: row.status === 'active' ? 'paused' : 'active' })}>{row.status === 'active' ? '暂停调度' : '启用调度'}</button></div></div>)}</div>
  </section>
}
