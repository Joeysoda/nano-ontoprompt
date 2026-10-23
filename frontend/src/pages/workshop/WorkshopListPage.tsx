import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiClientV2, formatApiError } from '@/api/client'
import AutobindDialog from './AutobindDialog'
import { appsUrl } from './runtime'
import { emptyDefinition, type AppDefinition, type AppRecord } from './spec'

/** Apps of the current ontology (picked in the top bar): open one, start an empty one, or generate one from an object type. */
export default function WorkshopListPage() {
  const [params] = useSearchParams()
  const ontologyId = params.get('ontology') ?? ''
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [generating, setGenerating] = useState(false)
  const apps = useQuery({ queryKey: ['workshop-apps', ontologyId], enabled: !!ontologyId, queryFn: () => apiClientV2.get<{ apps: Array<{ id: string; name: string; head_version: number; updated_at: string }> }>(appsUrl(ontologyId)) })
  const create = useMutation({ mutationFn: (body: { name: string; definition: AppDefinition }) => apiClientV2.post<AppRecord>(appsUrl(ontologyId), body),
    onSuccess: (row, body) => navigate(body.definition.pages[0]?.widgets.length ? `/apps/workshop/${row.id}?ontology=${ontologyId}` : `/apps/workshop/${row.id}/edit?ontology=${ontologyId}`) })
  return <div className="wb-page max-w-[1000px]" data-testid="workshop-list">
    <div className="wb-page-header"><div><p className="wb-eyebrow">应用</p><h1 className="wb-page-title mt-2">应用搭建</h1>
      <p className="wb-page-subtitle">用指标条、图表、地图、表格等标准组件拼出业务应用；也可以选一类对象，一键生成一页看板。</p></div></div>
    {!ontologyId && <p className="mt-6 text-sm text-slate-500">先在页面顶部的“当前本体”里选一个本体。</p>}
    {ontologyId && <>
      <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="应用列表">{apps.data?.apps.map(row => <li key={row.id} className="wb-surface flex items-center gap-3 p-4">
        <span className="flex-1"><span className="block font-medium">{row.name}</span><span className="text-xs text-slate-500">v{row.head_version} · {row.updated_at.slice(0, 16).replace('T', ' ')}</span></span>
        <Link className="wb-button-primary" to={`/apps/workshop/${row.id}?ontology=${ontologyId}`}>打开</Link><Link className="wb-button-secondary" to={`/apps/workshop/${row.id}/edit?ontology=${ontologyId}`}>编辑</Link></li>)}</ul>
      {apps.data && !apps.data.apps.length && <p className="mt-4 text-sm text-slate-500">这个本体还没有应用。</p>}
      <div className="mt-6 flex flex-wrap gap-2"><input className="wb-input w-72" placeholder="新应用名称" aria-label="新应用名称" value={name} onChange={event => setName(event.target.value)} />
        <button className="wb-button-secondary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate({ name: name.trim(), definition: emptyDefinition() })}>新建空白应用</button>
        <button className="wb-button-primary" onClick={() => setGenerating(true)}>从对象类型生成…</button></div>
      {create.error && <p role="alert" className="mt-2 text-sm text-rose-700">{formatApiError(create.error)}</p>}
      {generating && <AutobindDialog ontologyId={ontologyId} prefix="auto_" applyLabel="生成应用" onClose={() => setGenerating(false)}
        onApply={(picked, result) => { setGenerating(false); create.mutate({ name: name.trim() || result.plan.page_title,
          definition: { schema: 'workshop-app/v1', data_sources: picked.data_sources, variables: [], pages: [picked.page], settings: { time_range: 'all', refresh_minutes: 0 } } }) }} />}
    </>}
  </div>
}
