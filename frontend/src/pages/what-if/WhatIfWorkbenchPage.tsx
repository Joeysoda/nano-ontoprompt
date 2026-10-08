import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'

type Scenario = { id: string; name: string; description: string; status: string; base_view_id: string; head_revision: number; etag: number; updated_at: string }
type Revision = { revision: number; result_view_id: string; content_digest: string; status: string }
type ScenarioDetail = Scenario & { revisions: Revision[] }
type Stage = { key: string; status: string; duration_ms?: number; summary?: Record<string, unknown>; error?: { message?: string } }
type Run = { id: string; status: string; current_stage: string; progress: number; logic_asset_key: string; output_digest?: string; duration_ms?: number; result_manifest?: { output?: Record<string, unknown>; metrics?: Array<{ key: string; label: string; value: number; unit: string; completeness: string }>; warnings?: Array<{ message: string; severity: string }>; provenance?: Record<string, unknown> }; stages?: Stage[]; error?: { message?: string } }
type DemoCase = { scenario: { id: string; name: string; description: string; status: string; base_view_id: string; head_revision: number }; asset_key: string; parameters: Record<string, unknown> }
type DemoCatalog = { ontology_id: string; ontology_name: string; source: { kind: string; synthetic_extensions: boolean; note: string }; cases: DemoCase[] }

export function WhatIfDemoLandingPage() {
  const navigate = useNavigate()
  const bootstrap = useMutation({
    mutationFn: () => apiClientV2.post<DemoCatalog>('/what-if/demo/bootstrap'),
    onSuccess: (catalog) => {
      const first = catalog.cases[0]
      if (first) navigate(`/ontologies/${catalog.ontology_id}/what-if?scenario=${first.scenario.id}&tab=Summary`, { replace: true })
    },
  })
  const bootstrapDemo = bootstrap.mutate
  useEffect(() => { bootstrapDemo() }, [bootstrapDemo])
  if (bootstrap.isPending || !bootstrap.error) return <div className="flex min-h-full items-center justify-center bg-[var(--workbench-canvas)]"><div className="wb-surface max-w-lg p-8"><p className="wb-eyebrow">Prepared Supplier Study</p><h1 className="wb-page-title mt-2">Opening the ready-to-run study…</h1><p className="wb-page-subtitle mt-3">The frePPLe baseline, object context and scenario cases are being resolved.</p></div></div>
  return <div className="min-h-full bg-[var(--workbench-canvas)] p-8" data-testid="what-if-demo-landing">
    <section className="mx-auto max-w-5xl wb-surface p-8">
      <p className="wb-eyebrow">Ready-to-run demo</p>
      <h1 className="wb-page-title mt-2">What-if Scenario Workbench</h1>
      <p className="wb-page-subtitle mt-2">frePPLe 基线、Object Set、Object Query、FalkorDB 和三个持久 Scenario 已准备好；这里只执行场景推理。</p>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {['供应商中断与替代供应', '需求激增、产能下降与重排', '质量批次召回与恢复生产'].map((name, index) => <div key={name} className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="text-xs text-gray-400">CASE 0{index + 1}</div><h2 className="mt-2 font-semibold">{name}</h2>
          <p className="mt-2 text-sm text-gray-500">固定基线上的 synthetic what-if 输入，明确标注为演示数据。</p>
        </div>)}
      </div>
      <button className="wb-button-primary mt-7" onClick={() => bootstrap.mutate()} disabled={bootstrap.isPending} data-testid="start-what-if-demo">Retry prepared study</button>
      {bootstrap.error && <p role="alert" className="mt-3 text-sm text-red-700">演示数据尚未导入，或 FalkorDB/API 尚未启动。</p>}
    </section>
  </div>
}

const tabs = ['Summary', 'Explore', 'Compare', 'Impact', 'Changes'] as const
type Tab = typeof tabs[number]

export default function WhatIfWorkbenchPage() {
  const { id: ontologyId = '' } = useParams<{ id: string }>()
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [baseViewId, setBaseViewId] = useState('')
  const [description, setDescription] = useState('')
  const tab = (params.get('tab') as Tab | null) ?? 'Summary'
  const selectedId = params.get('scenario') ?? ''
  const scenarios = useQuery({
    queryKey: ['what-if-scenarios', ontologyId],
    queryFn: () => apiClientV2.get<{ scenarios: Scenario[] }>(`/ontologies/${ontologyId}/scenarios`),
    enabled: Boolean(ontologyId),
  })
  const detail = useQuery({
    queryKey: ['what-if-scenario', ontologyId, selectedId],
    queryFn: () => apiClientV2.get<ScenarioDetail>(`/ontologies/${ontologyId}/scenarios/${selectedId}`),
    enabled: Boolean(ontologyId && selectedId),
  })
  const runs = useQuery({
    queryKey: ['what-if-runs', ontologyId, selectedId],
    queryFn: () => apiClientV2.get<{ runs: Run[] }>(`/ontologies/${ontologyId}/scenarios/${selectedId}/runs`),
    enabled: Boolean(ontologyId && selectedId),
    refetchInterval: (query) => query.state.data?.runs.some((item) => ['queued', 'running'].includes(item.status)) ? 1000 : false,
  })
  const catalog = useQuery({
    queryKey: ['what-if-demo-catalog', ontologyId],
    queryFn: () => apiClientV2.post<DemoCatalog>('/what-if/demo/bootstrap'),
    enabled: Boolean(ontologyId && selectedId),
    staleTime: 60_000,
  })
  const create = useMutation({
    mutationFn: () => apiClientV2.post<Scenario>(`/ontologies/${ontologyId}/scenarios`, { name, description, base_view_id: baseViewId }),
    onSuccess: (scenario) => { queryClient.invalidateQueries({ queryKey: ['what-if-scenarios', ontologyId] }); setParams({ scenario: scenario.id, tab: 'Summary' }); setName(''); setDescription('') },
  })
  const run = useMutation({
    mutationFn: (asset: string) => { const item = catalog.data?.cases.find((entry) => entry.scenario.id === selectedId); return apiClientV2.post<Run>(`/ontologies/${ontologyId}/scenarios/${selectedId}/runs`, { revision: detail.data?.head_revision ?? 0, case_key: item?.asset_key.replace(/_v1$/, '') ?? asset, logic_asset_key: item?.asset_key ?? asset, parameters: item?.parameters ?? {} }) },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['what-if-runs', ontologyId, selectedId] }),
  })
  const selected = useMemo(() => scenarios.data?.scenarios.find((item) => item.id === selectedId), [scenarios.data, selectedId])

  function selectScenario(scenario: Scenario) { setParams({ scenario: scenario.id, tab }) }
  function selectTab(next: Tab) { setParams({ ...(selectedId ? { scenario: selectedId } : {}), tab: next }) }

  return <div className="min-h-full bg-[var(--workbench-canvas)]" data-testid="what-if-workbench">
    <header className="flex items-center justify-between border-b bg-white px-6 py-4" data-testid="what-if-header">
      <div><p className="wb-eyebrow">Ontology / What-if</p><h1 className="wb-page-title">Scenario Workbench</h1><p className="wb-page-subtitle">Pinned views, typed changes, deterministic impact evidence</p></div>
      <div className="flex gap-2"><span className="wb-status">{selected?.status ?? 'baseline'}</span><button className="wb-button-secondary" onClick={() => queryClient.invalidateQueries({ queryKey: ['what-if-scenarios', ontologyId] })}>Refresh</button></div>
    </header>
    <div className="grid min-h-[calc(100vh-100px)] grid-cols-[260px_minmax(0,1fr)_300px] gap-4 p-4">
      <aside className="wb-surface p-4" aria-label="Scenario Manager"><div className="mb-3 flex items-center justify-between"><h2 className="wb-section-title text-base">Scenario Manager</h2><span className="wb-tag">{scenarios.data?.scenarios.length ?? 0}</span></div>
        <button className="wb-button-secondary mb-4 w-full" data-testid="new-scenario" onClick={() => setParams({ tab: 'Summary' })}>+ New scenario</button>
        <div className="space-y-2">{scenarios.data?.scenarios.map((scenario) => <button key={scenario.id} className={`w-full rounded-lg border p-3 text-left ${scenario.id === selectedId ? 'border-blue-400 bg-blue-50' : 'border-gray-200 bg-white'}`} onClick={() => selectScenario(scenario)} data-testid={`scenario-${scenario.id}`}><div className="font-medium">{scenario.name}</div><div className="mt-1 text-xs text-gray-500">Revision {scenario.head_revision} · {scenario.status}</div></button>)}</div>
        {!scenarios.data?.scenarios.length && <div className="wb-empty">Create a pinned scenario to begin.</div>}
      </aside>
      <main className="min-w-0 space-y-4">
        {!selectedId && <section className="wb-surface p-6"><p className="wb-eyebrow">Create from baseline</p><h2 className="wb-section-title mt-2">Start an isolated scenario</h2><p className="mt-2 text-sm text-gray-500">The base view must be a ready QueryDataView. Synthetic demo extensions remain explicitly labeled.</p><div className="mt-5 grid gap-3 md:grid-cols-2"><label className="wb-label">Scenario name<input className="wb-input mt-1" value={name} onChange={(event) => setName(event.target.value)} placeholder="wood supplier outage" /></label><label className="wb-label">Ready base view ID<input className="wb-input mt-1" value={baseViewId} onChange={(event) => setBaseViewId(event.target.value)} placeholder="view_..." /></label><label className="wb-label md:col-span-2">Description<textarea className="wb-input mt-1" value={description} onChange={(event) => setDescription(event.target.value)} /></label></div><button className="wb-button-primary mt-5" disabled={!name || !baseViewId || create.isPending} onClick={() => create.mutate()} data-testid="create-scenario">{create.isPending ? 'Creating…' : 'Create scenario'}</button>{create.error && <p role="alert" className="mt-3 text-sm text-red-700">Unable to create scenario. Check the base view and permissions.</p>}</section>}
        {selectedId && detail.data && <><div className="wb-surface flex items-center gap-1 p-2" role="tablist">{tabs.map((item) => <button key={item} role="tab" aria-selected={tab === item} className={`rounded-lg px-4 py-2 text-sm ${tab === item ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-50'}`} onClick={() => selectTab(item)}>{item}</button>)}</div><WorkbenchPanel tab={tab} scenario={detail.data} runs={runs.data?.runs ?? []} onRun={(asset) => run.mutate(asset)} running={run.isPending} /><RunWorkflow runs={runs.data?.runs ?? []} /></>}
      </main>
      <aside className="wb-surface p-4" aria-label="Changes panel"><p className="wb-eyebrow">Changes panel</p><h2 className="wb-section-title mt-1 text-base">Revision evidence</h2>{detail.data ? <div className="mt-4 space-y-3 text-sm"><div><span className="text-gray-500">Head</span><div className="font-medium">Revision {detail.data.head_revision}</div></div><div><span className="text-gray-500">Base view</span><div className="break-all font-mono text-xs">{detail.data.base_view_id}</div></div>{detail.data.revisions.map((revision) => <div key={revision.revision} className="rounded-lg border border-gray-200 p-3"><div className="font-medium">Revision {revision.revision}</div><div className="mt-1 break-all font-mono text-[10px] text-gray-500">{revision.content_digest}</div></div>)}</div> : <div className="wb-empty mt-4">Select a scenario.</div>}</aside>
    </div>
  </div>
}

function WorkbenchPanel({ tab, scenario, runs, onRun, running }: { tab: Tab; scenario: ScenarioDetail; runs: Run[]; onRun: (asset: string) => void; running: boolean }) {
  const cards: Record<Tab, { title: string; body: string; asset?: string }> = {
    Summary: { title: 'Scenario Summary', body: 'Baseline metrics and candidate deltas remain tied to the pinned revision and its provenance.' },
    Explore: { title: 'Object Explorer', body: 'Use the existing Object Set and Object Query definitions with scenario_id and scenario_revision context.' },
    Compare: { title: 'Compare revisions', body: 'Compare baseline, Scenario A and Scenario B with added, removed and retained members.' },
    Impact: { title: 'Impact paths', body: 'Bounded graph traversal makes reachability evidence explicit; it does not claim physical causality.', asset: 'lot_recall_trace_v1' },
    Changes: { title: 'Typed changes', body: 'Changes are immutable, ordered and applied through typed Scenario Actions.', asset: 'supplier_disruption_impact_v1' },
  }
  const card = cards[tab]
  const demoAssets = tab === 'Summary' ? ['supplier_disruption_impact_v1', 'capacity_replan_v1', 'lot_recall_trace_v1'] : card.asset ? [card.asset] : []
  const latest = runs[0]
  return <section className="wb-surface p-6" data-testid={`what-if-${tab.toLowerCase}`}><p className="wb-eyebrow">{tab}</p><h2 className="wb-section-title mt-2">{card.title}</h2><p className="mt-2 max-w-2xl text-sm text-gray-500">{card.body}</p><div className="mt-5 grid gap-3 md:grid-cols-4"><Metric label="Head revision" value={String(scenario.head_revision)} /><Metric label="Pinned base" value={scenario.base_view_id.slice(0, 18)} /><Metric label="Runs" value={String(runs.length)} /><Metric label="Latest status" value={latest?.status ?? 'ready'} /></div><div className="mt-6 grid gap-4 md:grid-cols-3"><Metric label="Time" value="2026-01-01 → 2026-03-31" /><Metric label="Scope" value="Objects on graph" /><Metric label="Model provenance" value={latest?.result_manifest?.provenance?.logic_asset ? String(latest.result_manifest.provenance.logic_asset) : 'Supplier impact v1'} /></div>{latest?.result_manifest?.metrics && <div className="mt-6 grid gap-3 md:grid-cols-4">{latest.result_manifest.metrics.map((metric) => <Metric key={metric.key} label={metric.label} value={`${metric.value} ${metric.unit}`} />)}</div>}{demoAssets.length > 0 && <div className="mt-6 flex flex-wrap gap-2">{demoAssets.map((asset) => <button key={asset} className="wb-button-primary" disabled={running} onClick={() => onRun(asset)} data-testid={`run-${asset}`}>{running ? 'Queued…' : `Run ${asset}`}</button>)}</div>}</section>
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-lg bg-gray-50 p-3"><div className="text-xs text-gray-500">{label}</div><div className="mt-1 font-semibold">{value}</div></div> }

function RunWorkflow({ runs }: { runs: Run[] }) {
  const latest = runs[0]
  return <section className="wb-surface p-4" data-testid="run-workflow"><div className="flex items-center justify-between"><div><p className="wb-eyebrow">Async inference workflow</p><h2 className="wb-section-title text-base">{latest?.logic_asset_key ?? 'No run yet'}</h2></div><span className="wb-status">{latest ? `${latest.status} · ${latest.progress}%` : 'Ready'}</span></div>{latest && <><div className="mt-4 h-2 overflow-hidden rounded-full bg-gray-100"><div className="h-full bg-blue-600 transition-all" style={{ width: `${latest.progress}%` }} /></div><div className="mt-4 grid gap-2 md:grid-cols-7">{(latest.stages ?? []).map((stage) => <div key={stage.key} className={`rounded-lg border p-2 text-xs ${stage.status === 'completed' ? 'border-emerald-200 bg-emerald-50' : stage.status === 'failed' ? 'border-red-200 bg-red-50' : stage.status === 'running' ? 'border-blue-200 bg-blue-50' : 'border-gray-200 bg-gray-50'}`}><div className="font-medium">{stage.key.replaceAll('_', ' ')}</div><div className="mt-1 text-gray-500">{stage.status}{stage.duration_ms ? ` · ${stage.duration_ms}ms` : ''}</div></div>)}</div>{latest.result_manifest?.warnings?.map((warning) => <div key={warning.message} className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{warning.severity}: {warning.message}</div>)}<div className="mt-4 grid gap-3 text-xs text-gray-500 md:grid-cols-3"><div>ChangeSet / revision: {String(latest.result_manifest?.provenance?.scenario_revision ?? '—')}</div><div>Output digest: <span className="font-mono">{latest.output_digest ?? 'pending'}</span></div><div>Duration: {latest.duration_ms ? `${latest.duration_ms} ms` : 'running'}</div></div></>}</section>
}
