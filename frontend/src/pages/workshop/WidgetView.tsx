import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'
import { CoverageGrid, DataTable, KpiStrip, StackedBarChart } from '@/components/dashboard/widgets'
import { aggregate, asRows, formatCell, getPath, groupSeries, matches, plainId, template, tone, type Format, type Row, type ToneRule, type Where } from '@/components/dashboard/binding'
import DecisionPackagePanel from '@/pages/what-if/DecisionPackagePanel'
import type { DecisionRunContext } from '@/pages/what-if/decisionPackage'
import type { ResolvedSource, Widget } from './spec'

export type Runtime = {
  ontologyId: string
  sources: Record<string, ResolvedSource | undefined>
  vars: Record<string, string | null>
  setVar: (id: string, value: string | null) => void
  navigate: (page: string) => void
  refresh: () => void
  editing?: boolean
}

type P = Record<string, unknown>
const str = (value: unknown) => (value === undefined || value === null ? '' : String(value))
const list = <T,>(value: unknown) => (Array.isArray(value) ? value as T[] : [])

function useData(widget: Widget, rt: Runtime) {
  const source = widget.source ? rt.sources[widget.source] : undefined
  const outputs = source?.outputs ?? null
  const value = outputs ? getPath(outputs, widget.path) : undefined
  const p = widget.props as P
  const rows = asRows(value).filter(row => matches(row, str(p.filter_field), rt.vars[str(p.filter_variable)]))
  return { source, outputs, value, rows, p }
}

function select(widget: Widget, rt: Runtime, row: Row) {
  const event = widget.on_select
  if (!event) return
  if (event.set_variable) rt.setVar(event.set_variable, str(getPath(row, event.field || 'id')))
  if (event.navigate && !rt.editing) rt.navigate(event.navigate)
}

export default function WidgetView({ widget, rt }: { widget: Widget; rt: Runtime }) {
  const { source, outputs, rows, p } = useData(widget, rt)
  if (widget.source && !source) return <Empty text="数据加载中…" />
  if (widget.source && !outputs) return <Empty text={`数据源运行状态：${source?.status ?? '未知'}`} />
  switch (widget.type) {
    case 'kpi_strip': return <KpiStrip items={list<P>(p.items).map(item => {
      const sign = item.tone_path ? Number(getPath(outputs, str(item.tone_path))) : undefined
      return { label: str(item.label), value: formatCell(getPath(outputs, str(item.path)), (item.format as Format) || 'number'),
        note: item.note ? template(str(item.note), outputs) : undefined, tone: sign === undefined ? undefined : sign < 0 ? 'bad' as const : 'good' as const }
    })} />
    case 'table': return <TableWidget widget={widget} rt={rt} rows={rows} p={p} />
    case 'bar_chart': return <BarWidget rows={rows} p={p} title={widget.title} />
    case 'coverage_grid': return <GridWidget rows={rows} p={p} outputs={outputs} title={widget.title} />
    case 'facet_list': return <FacetWidget rows={asRows(getPath(outputs, widget.path))} p={p} rt={rt} outputs={outputs} />
    case 'select': return <label className="block"><select className="wb-input w-full" aria-label={widget.title || '选择'} value={rt.vars[str(p.variable)] ?? ''} onChange={event => rt.setVar(str(p.variable), event.target.value || null)}>
      {p.allow_all !== false && <option value="">全部</option>}{rows.map(row => <option key={str(getPath(row, str(p.value_field)))} value={str(getPath(row, str(p.value_field)))}>{str(getPath(row, str(p.label_field)))}</option>)}</select></label>
    case 'stat_row': return <div className="grid h-full grid-cols-3 items-center gap-2 text-center" data-testid="stat-row">{list<P>(p.items).map(item => <div key={str(item.label)}><p className="text-[11px] text-slate-500">{str(item.label)}</p><strong className="text-xl tabular-nums">{formatCell(aggregate(rows, item as never), (item.format as Format) || 'number')}</strong></div>)}</div>
    case 'banner': { const count = aggregate(rows, { agg: 'count', ...(p as Where) })
      return count ? <p className="rounded border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-900" role="status">🔔 {template(str(p.template), outputs, { count })}</p> : <Empty text="没有需要提醒的项" /> }
    case 'action_list': return <ActionList rows={rows} p={p} rt={rt} />
    case 'action_detail': return source ? <ActionDetail rows={rows} p={p} rt={rt} source={source} /> : <Empty text="请绑定数据源" />
    case 'rerun_button': return source ? <RerunButton p={p} rt={rt} source={source} /> : <Empty text="请绑定数据源" />
    default: return <Empty text={`未知组件 ${widget.type}`} />
  }
}

function Empty({ text }: { text: string }) {
  return <p className="flex h-full items-center justify-center p-3 text-xs text-slate-400">{text}</p>
}

function TableWidget({ widget, rt, rows, p }: { widget: Widget; rt: Runtime; rows: Row[]; p: P }) {
  const sorted = useMemo(() => {
    const field = str(p.sort_field)
    const out = field ? [...rows].sort((a, b) => { const x = getPath(a, field), y = getPath(b, field); return (typeof x === 'number' && typeof y === 'number' ? x - y : str(x).localeCompare(str(y))) * (p.sort_desc ? -1 : 1) }) : rows
    return p.max_rows ? out.slice(0, Number(p.max_rows)) : out
  }, [rows, p.sort_field, p.sort_desc, p.max_rows])
  const event = widget.on_select
  const selected = event?.set_variable ? rt.vars[event.set_variable] ?? undefined : undefined
  const keyField = event?.field || 'id'
  const columns = list<P>(p.columns).map(col => ({ label: str(col.label), value: (row: Row) => getPath(row, str(col.field)), format: (col.format as Format) || 'text', badge: !!col.badge,
    tone: (row: Row) => tone(row, str(col.field), { rule: (col.tone_rule as ToneRule['rule']) || undefined, value: col.tone_value as number | undefined, field: str(col.tone_field) || undefined }) }))
  return <div className="h-full overflow-auto" data-testid={`table-${widget.id}`}><DataTable label={widget.title || '表格'} rows={sorted} columns={columns} rowKey={row => str(getPath(row, keyField) ?? JSON.stringify(row))} selected={selected} onSelect={event ? row => select(widget, rt, row) : undefined} /></div>
}

function BarWidget({ rows, p, title }: { rows: Row[]; p: P; title: string }) {
  const series = list<P>(p.series)
  const grouped = useMemo(() => groupSeries(rows, str(p.x_field), series as never), [rows, p.x_field, series])
  const chart = useMemo(() => series.map((row, index) => ({ name: str(row.label), color: str(row.color) || ['#1d4ed8', '#b91c1c', '#0f766e', '#a16207'][index % 4], data: grouped.values[index] })), [series, grouped])
  return <StackedBarChart label={title || '柱状图'} categories={grouped.keys} series={chart} height={240} />
}

function GridWidget({ rows, p, outputs, title }: { rows: Row[]; p: P; outputs: unknown; title: string }) {
  const [weeks, setWeeks] = useState(Number(p.weeks) || 12)
  const [only, setOnly] = useState<'all' | 'deficit' | 'surplus'>('all')
  const columns = list<string>(getPath(outputs, str(p.columns_path))).slice(0, weeks)
  const shaped = rows.map(row => { const values = list<number>(getPath(row, str(p.values_field))).slice(0, weeks)
    return { key: str(getPath(row, str(p.label_field))) + str(row.item ?? ''), label: plainId(str(getPath(row, str(p.label_field)))) + (p.emphasis_field && getPath(row, str(p.emphasis_field)) ? ' ⚠' : ''), values, emphasis: !!(p.emphasis_field && getPath(row, str(p.emphasis_field))) } })
    .filter(row => only === 'all' || (only === 'deficit' ? row.values.some(v => v < 0) : row.values.every(v => v >= 0)))
    .sort((a, b) => Math.min(...a.values) - Math.min(...b.values))
  return <div className="flex h-full flex-col gap-2">
    <div className="flex flex-wrap gap-1 text-xs">{[4, 8, 12, 52].map(value => <button key={value} onClick={() => setWeeks(value)} className={`rounded px-2 py-1 ${weeks === value ? 'bg-blue-600 text-white' : 'border'}`}>{value === 52 ? 'All' : `Next ${value}`}</button>)}
      <span className="w-3" />{(['all', 'deficit', 'surplus'] as const).map(value => <button key={value} onClick={() => setOnly(value)} className={`rounded px-2 py-1 ${only === value ? 'bg-blue-600 text-white' : 'border'}`}>{{ all: 'All', deficit: 'Shortages', surplus: 'Surplus' }[value]}</button>)}</div>
    <div className="min-h-0 flex-1 overflow-auto"><CoverageGrid label={title || '时间格子表'} columns={columns} rows={shaped} /></div>
  </div>
}

function FacetWidget({ rows, p, rt, outputs }: { rows: Row[]; p: P; rt: Runtime; outputs: unknown }) {
  const field = str(p.field)
  const values = p.options_path ? list<string>(getPath(outputs, str(p.options_path))).map(String) : [...new Set(rows.map(row => str(getPath(row, field))))].sort()
  const current = rt.vars[str(p.variable)]
  return <div className="space-y-1.5" data-testid="facet-list">{values.map(value => { const count = rows.filter(row => str(getPath(row, field)) === value).length
    return <label key={value} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={current === value} onChange={event => rt.setVar(str(p.variable), event.target.checked ? value : null)} /><span className="flex-1">{value}</span><span className="text-xs text-slate-500">{count}</span><span className="h-2 rounded bg-blue-600" style={{ width: `${count * 10}px` }} /></label> })}</div>
}

function ActionList({ rows, p, rt }: { rows: Row[]; p: P; rt: Runtime }) {
  const shown = rows.filter(row => !p.only_applicable || row.applicable !== false).slice(0, Number(p.max_rows) || 50)
  const current = rt.vars[str(p.variable)]
  return <div className="h-full space-y-1.5 overflow-auto" data-testid="action-list">{shown.map(row => <button key={str(row.id)} onClick={() => { rt.setVar(str(p.variable), str(row.id)); if (p.navigate && !rt.editing) rt.navigate(str(p.navigate)) }}
    className={`flex w-full items-center justify-between gap-2 border px-3 py-2 text-left text-sm ${current === str(row.id) ? 'border-blue-500 bg-blue-50' : 'border-slate-200 bg-white'} ${row.applicable === false ? 'opacity-60' : ''}`}>
    <span>{plainId(str(getPath(row, str(p.label_field))))}{p.subtitle ? <span className="block text-xs text-slate-500">{template(str(p.subtitle), row)}</span> : null}</span><span>→</span></button>)}</div>
}

type Explanation = { candidate_id: string; model: string; verified: boolean; issues: Array<{ where: string; problem: string; value?: number }>; evidence: Array<{ id: string; label: string; value: unknown }>; answer: { summary: string; reasoning: Array<{ claim: string; evidence_ids: string[] }>; risks: Array<{ claim: string; evidence_ids: string[] }> } }

function ActionDetail({ rows, p, rt, source }: { rows: Row[]; p: P; rt: Runtime; source: ResolvedSource }) {
  const candidate = rows.find(row => str(row.id) === rt.vars[str(p.variable)])
  const context: DecisionRunContext | undefined = source.status === 'completed' ? { ontologyId: rt.ontologyId, runId: source.run_id, scenarioId: source.scenario_id, scenarioRevision: source.scenario_revision, nodeKey: source.node, artifact: source.artifacts[0] } : undefined
  const base = `/ontologies/${rt.ontologyId}/scenario-study-runs/${source.run_id}`
  const explanations = useQuery({ queryKey: ['explanations', source.run_id], queryFn: () => apiClientV2.get<{ explanations: Explanation[] }>(`${base}/explanations`), enabled: !!p.explain && !rt.editing })
  const explain = useMutation({ mutationFn: () => apiClientV2.post<Explanation>(`${base}/candidates/${encodeURIComponent(str(candidate!.id))}:explain`, undefined, { timeout: 120000 }), onSuccess: () => explanations.refetch() })
  if (!candidate) return <Empty text="在动作列表中选择一个动作" />
  const explanation = explanations.data?.explanations.find(row => row.candidate_id === candidate.id)
  const actions = list<P>(candidate.actions)
  return <div className="h-full space-y-3 overflow-auto pr-1" data-testid="action-detail">
    <div><p className="text-xs text-slate-500">{str(candidate.kind)}</p><h3 className="text-base font-semibold">{plainId(str(candidate.label))}</h3>
      {candidate.applicable === false && <p className="text-xs text-amber-700">{list<P>(candidate.hard_constraints).map(row => str(row.code)).join('、')}</p>}</div>
    <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4">{list<P>(p.metrics).map(metric => <div key={str(metric.label)} className="rounded border bg-slate-50 p-2"><dt className="text-[11px] text-slate-500">{str(metric.label)}</dt>
      <dd className="text-sm font-semibold">{metric.before_path ? `${formatCell(getPath(source.outputs, str(metric.before_path)), metric.format as Format)} → ` : ''}{formatCell(getPath(candidate, str(metric.after_field)), metric.format as Format)}</dd></div>)}</dl>
    {Array.isArray(candidate.protected_orders) && <p className="text-sm">获益 {list(candidate.protected_orders).length} · 受损 <span className={list(candidate.harmed_orders).length ? 'text-rose-700' : ''}>{list(candidate.harmed_orders).length}</span></p>}
    <ul className="max-h-32 space-y-1 overflow-auto text-xs">{actions.map((row, index) => <li key={index} className="rounded border px-2 py-1"><strong>{str(row.action_type)}</strong> · {Object.entries(row).filter(([key]) => key !== 'action_type').map(([key, value]) => `${key}=${plainId(str(value))}`).join(' · ')}</li>)}</ul>
    {!!p.explain && <div className="rounded border border-violet-200 bg-violet-50/50 p-3" data-testid="aip-explanation">
      <div className="flex items-center gap-2"><span className="rounded bg-violet-600 px-2 py-0.5 text-xs text-white">⚡ AIP Explanation</span>
        {explanation && <span className={`rounded px-2 py-0.5 text-xs ${explanation.verified ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{explanation.verified ? '数字与引用已核验' : `未通过核验（${explanation.issues.length} 处）`}</span>}
        {explanation && <span className="text-xs text-slate-500">{explanation.model}</span>}
        <button className="wb-button-secondary ml-auto" disabled={!context || explain.isPending || rt.editing} onClick={() => explain.mutate()}>{explain.isPending ? '模型推理中…' : explanation ? '重新生成' : '生成解释'}</button></div>
      {explain.error && <p role="alert" className="mt-2 text-xs text-rose-700">{formatApiError(explain.error)}</p>}
      {explanation && <div className="mt-2 space-y-1 text-sm"><p>{explanation.answer.summary}</p><ol className="list-decimal pl-5">{explanation.answer.reasoning.map((row, index) => <li key={index}>{row.claim} <span className="text-xs text-violet-700">[{row.evidence_ids.join(', ')}]</span></li>)}</ol>
        {explanation.answer.risks.length > 0 && <ul className="list-disc pl-5 text-amber-800">{explanation.answer.risks.map((row, index) => <li key={index}>{row.claim} <span className="text-xs text-violet-700">[{row.evidence_ids.join(', ')}]</span></li>)}</ul>}
        <details className="text-xs"><summary className="cursor-pointer text-slate-500">证据（{explanation.evidence.length} 条）</summary><ul>{explanation.evidence.map(row => <li key={row.id}><strong>{row.id}</strong> {row.label}：{typeof row.value === 'object' ? JSON.stringify(row.value) : str(row.value)}</li>)}</ul></details></div>}
    </div>}
    {!rt.editing && <div className="border-t pt-2"><DecisionPackagePanel context={context} candidate={{ id: str(candidate.id), applicable: candidate.applicable !== false }} /></div>}
  </div>
}

type Step = { label: string; state: 'pending' | 'running' | 'done' | 'failed'; detail?: string }

/** Re-run the source's Study on freshly pinned live data, then compare configured values. */
function RerunButton({ p, rt, source }: { p: P; rt: Runtime; source: ResolvedSource }) {
  const [open, setOpen] = useState(false)
  const compare = list<P>(p.steps)
  const [steps, setSteps] = useState<Step[]>([])
  const [error, setError] = useState('')
  const api = `/ontologies/${rt.ontologyId}`
  const mark = (index: number, state: Step['state'], detail?: string) => setSteps(current => current.map((row, i) => i === index ? { ...row, state, detail } : row))
  const run = useMutation({
    mutationFn: async () => {
      setSteps([{ label: '固定最新本体数据', state: 'pending' }, { label: '重跑模型', state: 'pending' }, ...compare.map(row => ({ label: str(row.label), state: 'pending' as const }))])
      mark(0, 'running')
      const view = await apiClientV2.post<{ view_id: string }>(`${api}/object-query/data-views`, { source_ontology_id: rt.ontologyId, source_manifest_digest: `replan-${Date.now()}` })
      const scenario = await apiClientV2.post<{ id: string }>(`${api}/scenarios`, { name: `重跑 ${new Date().toISOString().slice(11, 19)}`, base_view_id: view.view_id })
      mark(0, 'done', `数据视图 ${view.view_id.slice(0, 8)}`)
      mark(1, 'running')
      const study = await apiClientV2.get<{ versions: Array<{ version: number }> }>(`${api}/scenario-study-definitions/${source.study_id}`)
      const version = Math.max(...study.versions.map(row => row.version))
      const created = await apiClientV2.post<{ id: string; etag: number }>(`${api}/scenario-study-definitions/${source.study_id}/cases`, { study_version: version, scenario_id: scenario.id, name: `重跑 ${Date.now()}`, case_kind: 'candidate' })
      await apiClientV2.post(`${api}/scenario-study-definitions/${source.study_id}/cases/${created.id}:compile`)
      const queued = await apiClientV2.post<{ id: string }>(`${api}/scenario-study-definitions/${source.study_id}/cases/${created.id}/runs`, { expected_etag: created.etag, client_request_id: crypto.randomUUID() })
      let done: { status: string; result_manifest?: { nodes?: Record<string, { outputs: unknown }> } } | null = null
      for (let i = 0; i < 160; i += 1) {
        done = await apiClientV2.get(`${api}/scenario-study-runs/${queued.id}`)
        if (['completed', 'failed', 'cancelled'].includes(done!.status)) break
        await new Promise(resolve => setTimeout(resolve, 1500))
      }
      if (!done || done.status !== 'completed') throw new Error(`重跑失败：${done?.status}`)
      mark(1, 'done', `Run ${queued.id.slice(0, 8)}`)
      const next = done.result_manifest?.nodes?.[source.node]?.outputs
      compare.forEach((row, index) => mark(index + 2, 'done', `${formatCell(getPath(source.outputs, str(row.path)), row.format as Format)} → ${formatCell(getPath(next, str(row.path)), row.format as Format)}`))
    },
    onError: err => { setError(formatApiError(err)); setSteps(current => current.map(row => row.state === 'running' ? { ...row, state: 'failed' } : row)) },
  })
  return <>
    <button className="wb-button-primary w-full" disabled={rt.editing} onClick={() => { setOpen(true); setError(''); setSteps([]) }}>⚡ {str(p.label) || '重跑并更新'}</button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40" role="dialog" aria-label="Orchestrating Actions" data-testid="orchestration">
      <div className="w-[760px] max-w-[95vw] rounded bg-white p-5 shadow-xl">
        <h2 className="text-base font-semibold">⟳ Orchestrating Actions</h2><p className="text-xs text-slate-500">每一步都是真实的平台调用</p>
        <ol className="mt-4 grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.max(steps.length, 1)}, minmax(0, 1fr))` }}>{steps.map(row => <li key={row.label} className="text-center text-xs"><span className={`mx-auto mb-1 flex h-6 w-6 items-center justify-center rounded-full ${row.state === 'done' ? 'bg-emerald-600 text-white' : row.state === 'running' ? 'bg-blue-600 text-white' : row.state === 'failed' ? 'bg-rose-600 text-white' : 'bg-slate-200'}`}>{row.state === 'done' ? '✓' : row.state === 'failed' ? '!' : '·'}</span><strong className="block">{row.label}</strong><span className="text-slate-500">{row.detail}</span></li>)}</ol>
        {error && <p role="alert" className="mt-3 text-xs text-rose-700">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">{!run.isPending && !run.isSuccess && <button className="wb-button-primary" onClick={() => run.mutate()}>开始</button>}
          {run.isSuccess && <><span className="mr-auto self-center text-sm text-emerald-700">✓ Complete</span><button className="wb-button-primary" onClick={() => { setOpen(false); rt.refresh() }}>查看更新后的数据</button></>}
          <button className="wb-button-secondary" onClick={() => setOpen(false)}>关闭</button></div>
      </div></div>}
  </>
}
