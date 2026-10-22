import { Link, useParams, useSearchParams } from 'react-router-dom'
import { formatApiError } from '@/api/client'
import PageCanvas from './PageCanvas'
import { useApp, useSources, useUrlState } from './runtime'

/** Runs a published app: pages of bound widgets; selections and page live in the URL. */
export default function WorkshopAppPage() {
  const { appId = '' } = useParams()
  const [params] = useSearchParams()
  const ontologyId = params.get('ontology') ?? ''
  const version = params.get('version') ? Number(params.get('version')) : undefined
  const app = useApp(ontologyId, appId, version)
  const definition = app.data?.head.definition
  const state = useUrlState(definition)
  const sources = useSources(ontologyId, appId, definition, state.pinned, version)
  const page = definition?.pages.find(row => row.id === state.pageId) ?? definition?.pages[0]
  if (app.error) return <p role="alert" className="p-6 text-sm text-rose-700">{formatApiError(app.error)}</p>
  if (!definition || !page) return <p role="status" className="p-6 text-sm text-slate-500">应用加载中…</p>
  return <div className="flex min-h-full flex-col bg-slate-100" data-testid="workshop-app">
    <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-5 py-2.5">
      <span className="text-lg font-semibold text-slate-800">{app.data!.name}</span>
      <nav className="flex flex-wrap gap-1" aria-label="应用页面">{definition.pages.map(row => <button key={row.id} onClick={() => state.navigate(row.id)}
        className={`rounded px-3 py-1.5 text-sm ${row.id === page.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>{row.title}</button>)}</nav>
      <span className="ml-auto min-w-0 truncate text-xs text-slate-500">{definition.data_sources.map(source => { const resolved = sources.resolved[source.id]
        return <span key={source.id} className="ml-3">{source.label || source.id}：{resolved ? `Run ${resolved.run_id.slice(0, 8)}${resolved.completed_at ? ` · ${resolved.completed_at.slice(0, 16).replace('T', ' ')}` : ''}` : sources.errors.includes(source.id) ? '还没有完成的运行' : '加载中'}</span> })}</span>
      <span className="text-xs text-slate-400">v{app.data!.head.version}</span>
      <Link className="wb-button-secondary shrink-0" to={`/apps/workshop/${appId}/edit?ontology=${ontologyId}`}>编辑</Link>
    </header>
    <main className="p-3"><PageCanvas page={page} rt={{ ontologyId, sources: sources.resolved, vars: state.vars, setVar: state.setVar, navigate: state.navigate, refresh: sources.refresh }} /></main>
  </div>
}
