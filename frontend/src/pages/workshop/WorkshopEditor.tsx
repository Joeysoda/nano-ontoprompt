import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { apiClientV2, formatApiError } from '@/api/client'
import { asRows, discoverFields, discoverPaths, getPath } from '@/components/dashboard/binding'
import PageCanvas from './PageCanvas'
import { duplicateWidget, resourceReferences, validateDefinition } from './editorModel'
import { appsUrl, timeFieldOf, timeFieldsOf, useApp, useSources } from './runtime'
import { applyTemplate, defaultRegion, moveToRegion, placeBox, placeNew, regionsOf, spanOf, TEMPLATES, type TemplateId } from './templates'
import { nextId, specOf, WIDGETS, type AppDefinition, type DataSource, type FieldSpec, type LayoutItem, type Page, type Widget, DEFAULT_SETTINGS, REFRESH_OPTIONS, TIME_RANGES, type AppSettings, type WidgetType } from './spec'

type Study = { id: string; name: string }
type StudyDetail = { versions: Array<{ version: number; spec: { nodes: Array<{ key: string; kind: string }> } }> }

/** Three-panel builder: widget palette, live grid canvas, property inspector. Publishing saves an immutable version. */
export default function WorkshopEditor() {
  const { appId = '' } = useParams()
  const [params] = useSearchParams()
  const ontologyId = params.get('ontology') ?? ''
  const queryClient = useQueryClient()
  const app = useApp(ontologyId, appId)
  // Local edits remember the version they started from; a newer server version replaces them.
  const [edits, setEdits] = useState<{ version: number; definition: AppDefinition } | null>(null)
  const baseVersion = app.data?.head.version ?? 0
  const draft = edits && edits.version === baseVersion ? edits.definition : app.data?.head.definition ?? null
  const setDraft = (definition: AppDefinition) => setEdits({ version: baseVersion, definition })
  const [chosenPage, setPageId] = useState('')
  const [selected, setSelected] = useState('')
  const [chosenRegion, setRegion] = useState<string | null>(null)
  const [preview, setPreview] = useState(false)
  const [vars, setVars] = useState<Record<string, string | null>>({})
  const [comment, setComment] = useState('')
  const [history, setHistory] = useState<{ version: number; past: AppDefinition[]; future: AppDefinition[] }>({ version: 0, past: [], future: [] })
  const [notice, setNotice] = useState('')
  const pageId = chosenPage || draft?.pages[0]?.id || ''
  const sources = useSources(ontologyId, appId, draft ?? undefined, {}, undefined, (draft?.settings ?? DEFAULT_SETTINGS).time_range)
  const publish = useMutation({
    mutationFn: () => apiClientV2.post<{ head_version: number; unchanged: boolean }>(`${appsUrl(ontologyId)}/${appId}/versions`, { expected_version: baseVersion, definition: draft, comment }),
    onSuccess: async () => { setComment(''); await queryClient.invalidateQueries({ queryKey: ['workshop-app', ontologyId, appId] }); sources.refresh() },
  })
  if (app.error) return <p role="alert" className="p-6 text-sm text-rose-700">{formatApiError(app.error)}</p>
  if (!draft) return <p role="status" className="p-6 text-sm text-slate-500">加载中…</p>
  const page = draft.pages.find(row => row.id === pageId) ?? draft.pages[0]
  const widget = page.widgets.find(row => row.id === selected)
  const dirty = JSON.stringify(draft) !== JSON.stringify(app.data?.head.definition)
  const activeHistory = history.version === baseVersion ? history : { version: baseVersion, past: [], future: [] }
  const issues = validateDefinition(draft)
  const update = (next: AppDefinition) => {
    if (JSON.stringify(next) === JSON.stringify(draft)) return
    setHistory({ version: baseVersion, past: [...activeHistory.past.slice(-49), structuredClone(draft)], future: [] })
    setDraft(next)
    setNotice('')
  }
  const undo = () => {
    const previous = activeHistory.past.at(-1)
    if (!previous) return
    setHistory({ version: baseVersion, past: activeHistory.past.slice(0, -1), future: [structuredClone(draft), ...activeHistory.future].slice(0, 50) })
    setDraft(previous)
    setSelected('')
  }
  const redo = () => {
    const next = activeHistory.future[0]
    if (!next) return
    setHistory({ version: baseVersion, past: [...activeHistory.past, structuredClone(draft)].slice(-50), future: activeHistory.future.slice(1) })
    setDraft(next)
    setSelected('')
  }
  const updatePage = (patch: Partial<Page>) => update({ ...draft, pages: draft.pages.map(row => row.id === page.id ? { ...row, ...patch } : row) })
  const updateWidget = (patch: Partial<Widget>) => updatePage({ widgets: page.widgets.map(row => row.id === selected ? { ...row, ...patch } : row) })
  const regions = regionsOf(page)
  // New widgets go to the region picked on the canvas; without one, to the region that suits the widget type.
  const activeRegion = regions.some(row => row.id === chosenRegion) ? chosenRegion : null
  const addWidget = (type: string) => {
    const spec = specOf(type)!
    const id = nextId(type.replace(/_.*/, ''), draft.pages.flatMap(row => row.widgets.map(item => item.id)))
    const region = regions.length ? activeRegion ?? defaultRegion(page, spec.type) : null
    updatePage({ widgets: [...page.widgets, { id, type: spec.type, title: spec.label, source: draft.data_sources[0]?.id ?? null, path: '', props: structuredClone(spec.defaults), region }],
      layout: [...page.layout, placeNew(page, spec.type as WidgetType, region, id)] })
    setSelected(id)
  }
  const settings = draft.settings ?? DEFAULT_SETTINGS
  const rt = { ontologyId, sources: sources.resolved, vars, setVar: (id: string, value: string | null) => setVars(current => ({ ...current, [id]: value })),
    navigate: (target: string) => setPageId(target), refresh: sources.refresh, timeRange: settings.time_range, timeFields: timeFieldsOf(draft) }

  return <div className="flex h-full min-h-[calc(100vh-40px)] flex-col bg-slate-100" data-testid="workshop-editor">
    <header className="flex flex-wrap items-center gap-3 border-b bg-white px-4 py-2">
      <strong>{app.data!.name}</strong><span className="text-xs text-slate-500">编辑中 · 基于 v{baseVersion}{dirty ? ' · 有未发布的修改' : ''}</span>
      <div className="ml-auto flex gap-1"><button className="wb-button-secondary" disabled={!activeHistory.past.length} onClick={undo} title="撤销上一步">撤销</button><button className="wb-button-secondary" disabled={!activeHistory.future.length} onClick={redo} title="恢复撤销的修改">重做</button></div>
      <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={preview} onChange={event => setPreview(event.target.checked)} aria-label="预览模式" />预览（可点击交互）</label>
      <input className="wb-input w-56" placeholder="版本说明" aria-label="版本说明" value={comment} onChange={event => setComment(event.target.value)} />
      <button className="wb-button-primary" disabled={!dirty || publish.isPending || issues.length > 0} title={issues.length ? '请先修复配置问题' : ''} onClick={() => publish.mutate()}>发布新版本</button>
      <Link className="wb-button-secondary" to={`/apps/workshop/${appId}?ontology=${ontologyId}`}>打开应用</Link>
    </header>
    {publish.error && <p role="alert" className="bg-rose-50 px-4 py-2 text-sm text-rose-700">{formatApiError(publish.error)}</p>}
    {notice && <p role="status" className="bg-amber-50 px-4 py-2 text-sm text-amber-800">{notice}</p>}
    {issues.length > 0 && <div role="alert" className="bg-rose-50 px-4 py-2 text-sm text-rose-800"><strong>发布前需修复 {issues.length} 个问题：</strong> {issues.slice(0, 3).map(issue => `${issue.location}：${issue.message}`).join('；')}{issues.length > 3 ? '…' : ''}</div>}
    <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)_340px]">
      <aside className="space-y-4 overflow-auto border-r bg-white p-3" aria-label="组件库">
        <section><h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">添加组件</h2>
          {regions.length > 0 && <p className="mb-2 text-[11px] text-slate-500" data-testid="add-target">添加到：{activeRegion ? regions.find(row => row.id === activeRegion)!.label : '按组件类型自动选区域'}</p>}
          {WIDGETS.map(spec => <button key={spec.type} onClick={() => addWidget(spec.type)} title={spec.description} className="mb-1 block w-full rounded border px-2 py-1.5 text-left text-sm hover:border-blue-400">＋ {spec.label}</button>)}</section>
        <DataSources draft={draft} update={update} ontologyId={ontologyId} errors={sources.errors} onBlocked={setNotice} />
        <Variables draft={draft} update={update} onBlocked={setNotice} />
        <SettingsPanel settings={settings} onChange={patch => update({ ...draft, settings: { ...settings, ...patch } })} />
      </aside>
      <main className="min-w-0 overflow-auto p-3">
        <div className="mb-2 flex flex-wrap items-center gap-1" role="tablist" aria-label="页面">
          {draft.pages.map(row => <button key={row.id} role="tab" aria-selected={row.id === page.id} onClick={() => { setPageId(row.id); setSelected('') }} className={`rounded px-3 py-1 text-sm ${row.id === page.id ? 'bg-blue-600 text-white' : 'bg-white'}`}>{row.title}</button>)}
          <button className="rounded border bg-white px-2 py-1 text-sm" onClick={() => { const id = nextId('page', draft.pages.map(row => row.id)); update({ ...draft, pages: [...draft.pages, { id, title: `页面 ${draft.pages.length + 1}`, widgets: [], layout: [] }] }); setPageId(id); setSelected('') }}>＋ 页面</button>
        </div>
        {page.widgets.length === 0 && !regions.length && <p className="rounded border border-dashed bg-white p-6 text-center text-sm text-slate-500">从左侧添加组件，拖动标题栏移动，拖右下角调整大小。</p>}
        <PageCanvas page={page} rt={rt} editing={!preview} selected={selected} onSelect={setSelected} onLayout={layout => updatePage({ layout })}
          activeRegion={activeRegion} onActiveRegion={setRegion} onAddToRegion={setRegion} />
      </main>
      <aside className="overflow-auto border-l bg-white p-3" aria-label="属性">
        {widget ? <WidgetInspector widget={widget} draft={draft} page={page} onRegion={region => updatePage(moveToRegion(page, widget.id, region))} source={widget.source ? sources.resolved[widget.source]?.outputs : undefined} onChange={updateWidget}
          layout={page.layout.find(item => item.i === widget.id)} onLayout={item => updatePage({ layout: [...page.layout.filter(row => row.i !== widget.id), item] })}
          onDuplicate={() => { const copy = duplicateWidget(page, widget, draft.pages.flatMap(row => row.widgets.map(item => item.id))); const withCopy = { ...page, widgets: [...page.widgets, copy.widget] }
            updatePage({ widgets: withCopy.widgets, layout: [...page.layout, placeBox(withCopy, copy.widget.region ?? null, copy.layout.w, copy.layout.h, copy.widget.id)] }); setSelected(copy.widget.id) }}
          onDelete={() => { updatePage({ widgets: page.widgets.filter(row => row.id !== widget.id), layout: page.layout.filter(row => row.i !== widget.id) }); setSelected('') }} />
          : <PageInspector page={page} canDelete={draft.pages.length > 1} onChange={updatePage} onDelete={() => { const refs = resourceReferences(draft, 'page', page.id); if (refs.length) { setNotice(`不能删除“${page.title}”：${refs.join('、')} 仍会跳转到此页面。`); return } const remaining = draft.pages.filter(row => row.id !== page.id); update({ ...draft, pages: remaining }); setPageId(remaining[0].id) }} />}
      </aside>
    </div>
  </div>
}

function PageInspector({ page, canDelete, onChange, onDelete }: { page: Page; canDelete: boolean; onChange: (patch: Partial<Page>) => void; onDelete: () => void }) {
  const template = (page.template ?? 'free') as TemplateId
  return <div className="space-y-3"><h2 className="text-sm font-semibold">页面设置</h2>
    <Text label="页面标题" value={page.title} onChange={value => onChange({ title: value })} />
    <label className="block text-xs text-slate-500">版面模板<select className="wb-input mt-0.5 w-full" aria-label="版面模板" value={template} onChange={event => onChange(applyTemplate(page, event.target.value as TemplateId))}>
      {(Object.keys(TEMPLATES) as TemplateId[]).map(id => <option key={id} value={id}>{TEMPLATES[id].label}</option>)}</select></label>
    <p className="text-xs text-slate-500">{TEMPLATES[template].description}{page.widgets.length > 0 ? ' 切换模板会把现有组件按类型分到各区域并自动排好。' : ''}</p>
    <p className="text-xs text-slate-500">页面编号 {page.id}（跳页事件引用它）。点击画布中的组件编辑组件属性。</p>
    {canDelete && <button className="text-xs text-rose-700" onClick={onDelete}>删除此页面</button>}</div>
}

const SOURCE_KINDS = [['study_run', 'Study 运行结果'], ['object_set', '本体对象集'], ['alerts', '规则告警']] as const
type AddableKind = (typeof SOURCE_KINDS)[number][0]
type CatalogType = { api_name: string; properties: Array<{ api_name: string }> }

function sourceSummary(source: DataSource) {
  if (source.kind === 'study_run') return `Study · ${source.node}`
  if (source.kind === 'object_set') return `对象 · ${source.object_type}${source.filters?.length ? ` · ${source.filters.map(row => `${row.field}=${String(row.value)}`).join('、')}` : ''}`
  if (source.kind === 'aggregate') return `汇总 · ${source.object_type} 按 ${source.group_by.join('、') || '全部'}`
  return `告警 · ${source.status ?? '全部'}`
}

const timeNote = (source: DataSource) => { const field = timeFieldOf(source); return field ? ` · 时间=${field}` : '' }

function DataSources({ draft, update, ontologyId, errors, onBlocked }: { draft: AppDefinition; update: (next: AppDefinition) => void; ontologyId: string; errors: string[]; onBlocked: (message: string) => void }) {
  // Aggregate sources are generated by one-click binding, not typed in here.
  const [kind, setKind] = useState<AddableKind>('object_set')
  const studies = useQuery({ queryKey: ['studies', ontologyId], enabled: kind === 'study_run', queryFn: () => apiClientV2.get<{ items: Study[] }>(`/ontologies/${ontologyId}/scenario-study-definitions`) })
  const catalog = useQuery({ queryKey: ['query-catalog', ontologyId], enabled: kind === 'object_set', queryFn: () => apiClientV2.get<{ types: CatalogType[] }>(`/ontologies/${ontologyId}/object-query/catalog`) })
  const [study, setStudy] = useState('')
  const detail = useQuery({ queryKey: ['study-detail', ontologyId, study], enabled: !!study, queryFn: () => apiClientV2.get<StudyDetail>(`/ontologies/${ontologyId}/scenario-study-definitions/${study}`) })
  const nodes = useMemo(() => { const latest = [...(detail.data?.versions ?? [])].sort((a, b) => b.version - a.version)[0]
    return (latest?.spec.nodes ?? []).filter(node => node.kind === 'function').map(node => node.key) }, [detail.data])
  const [node, setNode] = useState('')
  const [objectType, setObjectType] = useState('')
  const [filter, setFilter] = useState({ field: '', value: '' })
  const [alertStatus, setAlertStatus] = useState('open')
  const [timeField, setTimeField] = useState('')
  const fields = catalog.data?.types.find(row => row.api_name === objectType)?.properties.map(row => row.api_name) ?? []
  const ready = kind === 'study_run' ? !!study && !!node : kind === 'object_set' ? !!objectType : true
  const add = () => {
    const id = nextId('source', draft.data_sources.map(row => row.id))
    const source: DataSource = kind === 'study_run' ? { id, kind, label: studies.data?.items.find(row => row.id === study)?.name ?? id, study_id: study, node }
      : kind === 'object_set' ? { id, kind, label: objectType, object_type: objectType, ...(filter.field ? { filters: [{ field: filter.field, op: 'eq' as const, value: filter.value }] } : {}), ...(timeField ? { time_field: timeField } : {}) }
      : { id, kind, label: '告警', status: alertStatus ? alertStatus as 'open' : null }
    update({ ...draft, data_sources: [...draft.data_sources, source] })
    setStudy(''); setNode(''); setObjectType(''); setFilter({ field: '', value: '' }); setTimeField('')
  }
  return <section><h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">数据源</h2>
    {draft.data_sources.map(source => <div key={source.id} className="mb-1 rounded border px-2 py-1 text-xs"><strong>{source.label || source.id}</strong> · {sourceSummary(source)}{timeNote(source)}{errors.includes(source.id) && <span className="block text-amber-700">{source.kind === 'study_run' ? '还没有完成的运行' : '加载失败'}</span>}
      <button className="float-right text-rose-700" onClick={() => { const refs = resourceReferences(draft, 'source', source.id); if (refs.length) onBlocked(`不能删除数据源“${source.label || source.id}”：${refs.join('、')} 仍在使用它。`); else update({ ...draft, data_sources: draft.data_sources.filter(row => row.id !== source.id) }) }} aria-label={`删除数据源 ${source.id}`}>×</button></div>)}
    <select className="wb-input mt-1 w-full" aria-label="数据源类型" value={kind} onChange={event => setKind(event.target.value as AddableKind)}>{SOURCE_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
    {kind === 'study_run' && <>
      <select className="wb-input mt-1 w-full" aria-label="选择 Study" value={study} onChange={event => { setStudy(event.target.value); setNode('') }}><option value="">选择 Study…</option>{studies.data?.items.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select>
      {study && <select className="wb-input mt-1 w-full" aria-label="选择函数节点" value={node} onChange={event => setNode(event.target.value)}><option value="">选择输出节点…</option>{nodes.map(key => <option key={key} value={key}>{key}</option>)}</select>}</>}
    {kind === 'object_set' && <>
      <select className="wb-input mt-1 w-full" aria-label="选择对象类型" value={objectType} onChange={event => { setObjectType(event.target.value); setFilter({ field: '', value: '' }) }}><option value="">选择对象类型…</option>{catalog.data?.types.map(row => <option key={row.api_name} value={row.api_name}>{row.api_name}</option>)}</select>
      {objectType && <div className="mt-1 flex gap-1"><select className="wb-input min-w-0 flex-1" aria-label="筛选字段" value={filter.field} onChange={event => setFilter({ ...filter, field: event.target.value })}><option value="">不筛选</option>{fields.map(name => <option key={name} value={name}>{name}</option>)}</select>
        {filter.field && <input className="wb-input min-w-0 flex-1" aria-label="筛选值" placeholder="等于…" value={filter.value} onChange={event => setFilter({ ...filter, value: event.target.value })} />}</div>}
      {objectType && <select className="wb-input mt-1 w-full" aria-label="时间字段" value={timeField} onChange={event => setTimeField(event.target.value)}><option value="">不受时间范围影响</option>{fields.map(name => <option key={name} value={name}>时间字段：{name}</option>)}</select>}</>}
    {kind === 'alerts' && <select className="wb-input mt-1 w-full" aria-label="告警状态" value={alertStatus} onChange={event => setAlertStatus(event.target.value)}><option value="open">未处理</option><option value="acknowledged">已确认</option><option value="resolved">已解决</option><option value="">全部</option></select>}
    <button className="mt-1 w-full rounded border px-2 py-1 text-sm disabled:opacity-50" disabled={!ready} onClick={add}>＋ 添加数据源</button></section>
}

function Variables({ draft, update, onBlocked }: { draft: AppDefinition; update: (next: AppDefinition) => void; onBlocked: (message: string) => void }) {
  const [name, setName] = useState('')
  const valid = /^[A-Za-z][A-Za-z0-9_-]{0,59}$/.test(name) && !draft.variables.some(row => row.id === name)
  return <section><h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">变量</h2>
    <p className="mb-1 text-[11px] text-slate-500">组件之间共享的选中值，例如“选中的产品线”。</p>
    {draft.variables.map(row => <div key={row.id} className="mb-1 flex items-center rounded border px-2 py-1 text-xs"><span className="flex-1">{row.id}</span>
      <button className="text-rose-700" onClick={() => { const refs = resourceReferences(draft, 'variable', row.id); if (refs.length) onBlocked(`不能删除变量“${row.id}”：${refs.join('、')} 仍在使用它。`); else update({ ...draft, variables: draft.variables.filter(item => item.id !== row.id) }) }} aria-label={`删除变量 ${row.id}`}>×</button></div>)}
    <div className="flex gap-1"><input className="wb-input min-w-0 flex-1" placeholder="英文名，如 line" aria-label="新变量名" value={name} onChange={event => setName(event.target.value.trim())} />
      <button className="rounded border px-2 text-sm disabled:opacity-50" disabled={!valid} onClick={() => { update({ ...draft, variables: [...draft.variables, { id: name, label: name }] }); setName('') }}>添加</button></div></section>
}

function WidgetInspector({ widget, draft, page, onRegion, source, onChange, onDelete, onDuplicate, layout, onLayout }: { widget: Widget; draft: AppDefinition; page: Page; onRegion: (region: string) => void
  source: unknown; onChange: (patch: Partial<Widget>) => void; onDelete: () => void; onDuplicate: () => void; layout?: LayoutItem; onLayout: (item: LayoutItem) => void }) {
  const regions = regionsOf(page)
  const spec = specOf(widget.type)!
  const paths = discoverPaths(source)
  const rows = asRows(getPath(source, widget.path))
  const fields = discoverFields(rows)
  const setProp = (key: string, value: unknown) => onChange({ props: { ...widget.props, [key]: value } })
  const ctx = { paths: paths.filter(row => row.kind !== 'object').map(row => row.path), fields, variables: draft.variables.map(row => row.id), pages: draft.pages.map(row => row.id) }
  return <div className="space-y-3" data-testid="widget-inspector">
    <div className="flex items-center"><h2 className="text-sm font-semibold">{spec.label}</h2><span className="ml-2 text-xs text-slate-400">{widget.id}</span><button className="ml-auto text-xs text-blue-700" onClick={onDuplicate}>复制</button><button className="ml-3 text-xs text-rose-700" onClick={onDelete}>删除</button></div>
    <p className="text-xs text-slate-500">{spec.description}</p>
    <Text label="标题" value={widget.title} onChange={value => onChange({ title: value })} />
    {regions.length > 0 && <label className="block text-xs text-slate-500">所在区域<select className="wb-input mt-0.5 w-full" aria-label="所在区域" value={widget.region ?? ''} onChange={event => onRegion(event.target.value)}>
      {regions.map(row => <option key={row.id} value={row.id}>{row.tag} {row.label}</option>)}</select></label>}
    {layout && <fieldset className="grid grid-cols-4 gap-1 rounded border p-2"><legend className="px-1 text-xs text-slate-500">位置与大小（{regions.length ? '区域内 ' : ''}{spanOf(page, widget.region)} 列网格）</legend>
      {(['x', 'y', 'w', 'h'] as const).map(key => <Text key={key} label={{ x: '列', y: '行', w: '宽', h: '高' }[key]} type="number" value={String(layout[key])}
        onChange={value => onLayout({ ...layout, [key]: Math.max(key === 'w' || key === 'h' ? 1 : 0, Number(value) || 0) })} />)}</fieldset>}
    <Choice label="数据源" value={widget.source ?? ''} options={['', ...draft.data_sources.map(row => row.id)]} onChange={value => onChange({ source: value || null })} />
    {spec.usesArray && <Choice label="列表路径" value={widget.path} options={['', ...paths.filter(row => row.kind === 'array').map(row => row.path)]} onChange={value => onChange({ path: value })} />}
    {spec.props.map(field => <FieldInput key={field.key} field={field} value={widget.props[field.key]} ctx={ctx} onChange={value => setProp(field.key, value)} />)}
    {spec.selectable && <fieldset className="space-y-2 rounded border p-2"><legend className="px-1 text-xs text-slate-500">点击一行时</legend>
      <Choice label="写入变量" value={widget.on_select?.set_variable ?? ''} options={['', ...ctx.variables]} onChange={value => onChange({ on_select: { ...widget.on_select, set_variable: value || null } })} />
      <Choice label="取值字段" value={widget.on_select?.field ?? ''} options={['', ...fields]} onChange={value => onChange({ on_select: { ...widget.on_select, field: value || null } })} />
      <Choice label="跳到页面" value={widget.on_select?.navigate ?? ''} options={['', ...ctx.pages]} onChange={value => onChange({ on_select: { ...widget.on_select, navigate: value || null } })} /></fieldset>}
    {!source && widget.source && <p className="text-xs text-amber-700">数据源还没有可用的运行结果，路径和字段暂时无法列出。</p>}
  </div>
}

type Ctx = { paths: string[]; fields: string[]; variables: string[]; pages: string[] }

function FieldInput({ field, value, ctx, onChange, prefix = '' }: { field: FieldSpec; value: unknown; ctx: Ctx; onChange: (value: unknown) => void; prefix?: string }) {
  const label = `${prefix}${field.label}`
  const text = value === undefined || value === null ? '' : String(value)
  switch (field.kind) {
    case 'boolean': return <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label={label} checked={!!value} onChange={event => onChange(event.target.checked)} />{field.label}</label>
    case 'number': return <Text label={label} value={text} type="number" onChange={next => onChange(next === '' ? undefined : Number(next))} />
    case 'select': return <Choice label={label} value={text} options={field.options ?? []} onChange={onChange} />
    case 'scalar_path': return <Choice label={label} value={text} options={['', ...ctx.paths]} onChange={onChange} />
    case 'field': return <Choice label={label} value={text} options={['', ...ctx.fields]} onChange={onChange} />
    case 'variable': return <Choice label={label} value={text} options={['', ...ctx.variables]} onChange={onChange} />
    case 'page': return <Choice label={label} value={text} options={['', ...ctx.pages]} onChange={onChange} />
    case 'color': return <Text label={label} value={text} type="color" onChange={onChange} />
    case 'list': { const items = Array.isArray(value) ? value as Record<string, unknown>[] : []
      return <fieldset className="space-y-2 rounded border p-2"><legend className="px-1 text-xs text-slate-500">{field.label}（{items.length}）</legend>
        {items.map((item, index) => <div key={index} className="space-y-1 rounded bg-slate-50 p-2">
          <div className="flex text-[11px] text-slate-500">第 {index + 1} 项<button className="ml-auto text-rose-700" aria-label={`删除${field.label} ${index + 1}`} onClick={() => onChange(items.filter((_, i) => i !== index))}>删除</button></div>
          {(field.item ?? []).map(sub => <FieldInput key={sub.key} field={sub} value={item[sub.key]} ctx={ctx} prefix={`${field.label} ${index + 1} `} onChange={next => onChange(items.map((row, i) => i === index ? { ...row, [sub.key]: next } : row))} />)}
        </div>)}
        <button className="w-full rounded border px-2 py-1 text-xs" onClick={() => onChange([...items, {}])}>＋ 添加{field.label}</button></fieldset> }
    default: return <Text label={label} value={text} onChange={onChange} />
  }
}

function Text({ label, value, onChange, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return <label className="block text-xs text-slate-500">{label.replace(/^.* \d+ /, '')}<input className="wb-input mt-0.5 w-full" type={type} aria-label={label} value={value} onChange={event => onChange(event.target.value)} /></label>
}

function Choice({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  const all = options.includes(value) ? options : [...options, value]
  return <label className="block text-xs text-slate-500">{label.replace(/^.* \d+ /, '')}<select className="wb-input mt-0.5 w-full" aria-label={label} value={value} onChange={event => onChange(event.target.value)}>
    {all.map(option => <option key={option} value={option}>{option || '—'}</option>)}</select></label>
}

function SettingsPanel({ settings, onChange }: { settings: AppSettings; onChange: (patch: Partial<AppSettings>) => void }) {
  return <section><h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">全局控件默认值</h2>
    <p className="mb-1 text-[11px] text-slate-500">打开应用时的时间范围和刷新方式；看的人可以在应用顶部临时改。</p>
    <select className="wb-input w-full" aria-label="默认时间范围" value={settings.time_range} onChange={event => onChange({ time_range: event.target.value as AppSettings['time_range'] })}>{TIME_RANGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
    <select className="wb-input mt-1 w-full" aria-label="默认刷新方式" value={settings.refresh_minutes} onChange={event => onChange({ refresh_minutes: Number(event.target.value) as AppSettings['refresh_minutes'] })}>{REFRESH_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></section>
}
