import { useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { formatApiError } from '@/api/client'
import PageCanvas from './PageCanvas'
import { timeFieldOf, timeFieldsOf, useApp, useSources, useUrlState } from './runtime'
import { REFRESH_OPTIONS, TIME_RANGES, type DataSource, type ResolvedSource } from './spec'

const stamp = (iso: string | null) => (iso ? ` · ${iso.slice(0, 16).replace('T', ' ')}` : '')

const UNIT = { object_set: '个对象', alerts: '条告警', aggregate: '组' }

function sourceStatus(source: DataSource, resolved: ResolvedSource | undefined, failed: boolean) {
  if (!resolved) return failed ? '加载失败' : '加载中'
  if (resolved.kind === 'study_run') return `Run ${resolved.run_id.slice(0, 8)}${stamp(resolved.completed_at)}`
  const cut = resolved.outputs.truncated ? '（已截断，统计不完整）' : ''
  const window = resolved.outputs.time
  const timeAware = source.kind === 'query' ? !!window?.field : !!timeFieldOf(source)
  const version = resolved.version ? ` · v${resolved.version}` : ''
  const time = !timeAware ? ' · 不受时间范围影响' : window?.range && window.range !== 'all' ? (window.anchor ? ` · 截至 ${window.anchor.slice(0, 10)}` : ' · 没有时间数据') : ''
  return `${resolved.outputs.count} ${UNIT[resolved.kind]}${cut}${time}${version}`
}

/** Runs a published app: pages of bound widgets; selections and page live in the URL. */
export default function WorkshopAppPage() {
  const { appId = '' } = useParams()
  const [params] = useSearchParams()
  const ontologyId = params.get('ontology') ?? ''
  const version = params.get('version') ? Number(params.get('version')) : undefined
  const app = useApp(ontologyId, appId, version)
  const definition = app.data?.head.definition
  const state = useUrlState(definition)
  const sources = useSources(ontologyId, appId, definition, state.pinned, version, state.timeRange, false, state.vars)
  const timed = (definition?.data_sources ?? []).some(source => source.kind === 'query' || timeFieldOf(source))
  const page = definition?.pages.find(row => row.id === state.pageId) ?? definition?.pages[0]
  const reload = useMutation({ mutationFn: sources.reload })
  const { mutate: reloadNow } = reload
  useEffect(() => {
    if (!state.refreshMinutes) return
    const timer = window.setInterval(() => reloadNow(), state.refreshMinutes * 60_000)
    return () => window.clearInterval(timer)
  }, [state.refreshMinutes, reloadNow])
  if (app.error) return <p role="alert" className="p-6 text-sm text-rose-700">{formatApiError(app.error)}</p>
  if (!definition || !page) return <p role="status" className="p-6 text-sm text-slate-500">应用加载中…</p>
  return <div className="flex min-h-full flex-col bg-slate-100" data-testid="workshop-app">
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-5 py-2.5">
      <span className="whitespace-nowrap text-lg font-semibold text-slate-800">{app.data!.name}</span>
      <nav className="flex flex-wrap gap-1 whitespace-nowrap" aria-label="应用页面">{definition.pages.map(row => <button key={row.id} onClick={() => state.navigate(row.id)}
        className={`rounded px-3 py-1.5 text-sm ${row.id === page.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>{row.title}</button>)}</nav>
      <span className="ml-auto min-w-0 truncate text-xs text-slate-500">{definition.data_sources.map(source => <span key={source.id} className="ml-3" data-testid={`source-status-${source.id}`}>{source.label || source.id}：{sourceStatus(source, sources.resolved[source.id], sources.errors.includes(source.id))}</span>)}</span>
      <label className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-slate-500" title={timed ? '以每个数据源里最新的时间为终点往前算；没有设置时间字段的数据源不受影响' : '本应用的数据源都没有设置时间字段，时间范围不起作用'}>时间<select className="wb-input py-1 text-xs disabled:opacity-50" aria-label="时间范围" disabled={!timed} value={timed ? state.timeRange : 'all'} onChange={event => state.setControl('t', event.target.value)}>{TIME_RANGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-slate-500">刷新<select className="wb-input py-1 text-xs" aria-label="刷新方式" value={state.refreshMinutes} onChange={event => state.setControl('r', event.target.value)}>{REFRESH_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button className="wb-button-secondary shrink-0" disabled={reload.isPending} onClick={() => reload.mutate()} title="重新拍一份本体快照并重新加载全部数据源">{reload.isPending ? '刷新中…' : '刷新数据'}</button>
      <span className="text-xs text-slate-400">v{app.data!.head.version}</span>
      <Link className="wb-button-secondary shrink-0" to={`/apps/workshop/${appId}/edit?ontology=${ontologyId}`}>编辑</Link>
    </header>
    <main className="p-3"><PageCanvas page={page} rt={{ ontologyId, sources: sources.resolved, vars: state.vars, setVar: state.setVar, navigate: state.navigate, refresh: sources.refresh, timeRange: state.timeRange, timeFields: timeFieldsOf(definition) }} /></main>
  </div>
}
