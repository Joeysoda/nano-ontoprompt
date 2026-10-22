import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiClientV2, formatApiError } from '@/api/client'
import { ontologyApi } from '@/api/ontologies'
import { appsUrl } from './runtime'
import { emptyDefinition, type AppRecord } from './spec'

/** Pick an ontology, then open or create an app built from standard widgets. */
export default function WorkshopListPage() {
  const [params, setParams] = useSearchParams()
  const ontologyId = params.get('ontology') ?? ''
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const ontologies = useQuery({ queryKey: ['ontologies', 'workshop'], queryFn: () => ontologyApi.list({ page_size: 200 }) })
  const apps = useQuery({ queryKey: ['workshop-apps', ontologyId], enabled: !!ontologyId, queryFn: () => apiClientV2.get<{ apps: Array<{ id: string; name: string; head_version: number; updated_at: string }> }>(appsUrl(ontologyId)) })
  const create = useMutation({ mutationFn: () => apiClientV2.post<AppRecord>(appsUrl(ontologyId), { name: name.trim(), definition: emptyDefinition() }),
    onSuccess: row => navigate(`/apps/workshop/${row.id}/edit?ontology=${ontologyId}`) })
  return <div className="wb-page max-w-[1000px]" data-testid="workshop-list">
    <div className="wb-page-header"><div><p className="wb-eyebrow">应用</p><h1 className="wb-page-title mt-2">应用搭建</h1>
      <p className="wb-page-subtitle">用指标条、表格、图表、动作等标准组件拼出业务应用，数据绑定到 Study 的运行结果。</p></div></div>
    <label className="mt-6 block text-sm">本体<select className="wb-input mt-1 w-full max-w-md" aria-label="本体" value={ontologyId} onChange={event => setParams({ ontology: event.target.value })}>
      <option value="">选择本体…</option>{ontologies.data?.items.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    {ontologyId && <>
      <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="应用列表">{apps.data?.apps.map(row => <li key={row.id} className="wb-surface flex items-center gap-3 p-4">
        <span className="flex-1"><span className="block font-medium">{row.name}</span><span className="text-xs text-slate-500">v{row.head_version} · {row.updated_at.slice(0, 16).replace('T', ' ')}</span></span>
        <Link className="wb-button-primary" to={`/apps/workshop/${row.id}?ontology=${ontologyId}`}>打开</Link><Link className="wb-button-secondary" to={`/apps/workshop/${row.id}/edit?ontology=${ontologyId}`}>编辑</Link></li>)}</ul>
      {apps.data && !apps.data.apps.length && <p className="mt-4 text-sm text-slate-500">这个本体还没有应用。</p>}
      <div className="mt-6 flex gap-2"><input className="wb-input w-72" placeholder="新应用名称" aria-label="新应用名称" value={name} onChange={event => setName(event.target.value)} />
        <button className="wb-button-primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>新建应用</button></div>
      {create.error && <p role="alert" className="mt-2 text-sm text-rose-700">{formatApiError(create.error)}</p>}
    </>}
  </div>
}
