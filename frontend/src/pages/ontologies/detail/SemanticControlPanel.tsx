import { useMemo, useState } from 'react'
import { AlertTriangle, Database, Loader2, ShieldCheck } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { ontologyApi } from '@/api/ontologies'
import type { MigrationInstruction, SchemaMigrationPlan, SemanticResource } from '@/types/semanticCore'

const inputClass = 'w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-xs outline-none focus:border-slate-700'

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error !== null) {
    const value = error as { detail?: { message?: string } | string; message?: string }
    if (typeof value.detail === 'object' && value.detail?.message) return value.detail.message
    if (typeof value.detail === 'string') return value.detail
    if (value.message) return value.message
  }
  return fallback
}

export default function SemanticControlPanel({ ontologyId }: { ontologyId: string }) {
  const [selectedId, setSelectedId] = useState('')
  const [newApiName, setNewApiName] = useState('')
  const [message, setMessage] = useState('')
  const [plan, setPlan] = useState<SchemaMigrationPlan | null>(null)
  const schemaQuery = useQuery({
    queryKey: ['semantic-schema', ontologyId],
    queryFn: () => ontologyApi.semanticSchema(ontologyId),
  })
  const planeQuery = useQuery({
    queryKey: ['data-plane-status', ontologyId],
    queryFn: () => ontologyApi.dataPlaneStatus(ontologyId),
  })
  const resources = useMemo(() => (schemaQuery.data?.all_resources || []).slice(0, 80), [schemaQuery.data?.all_resources])
  const selected = resources.find((item) => item.resource_id === selectedId)

  async function preflight() {
    if (!selected || !newApiName.trim()) {
      setMessage('先选择规范资源并填写新的 API 名称')
      return
    }
    setMessage('')
    const instruction: MigrationInstruction = { kind: 'rename_api_name', resource_id: selected.resource_id, payload: { new_api_name: newApiName.trim() } }
    try {
      const result = await ontologyApi.migrationDryRun(ontologyId, { base_revision_id: schemaQuery.data?.revision_id, instructions: [instruction] })
      setPlan(result)
      setMessage(result.impact?.can_apply === false ? '预检发现阻断项，请先处理影响' : '预检通过，可生成影子修订')
    } catch (error: unknown) {
      setPlan(null)
      setMessage(errorMessage(error, '迁移预检失败'))
    }
  }

  async function apply() {
    if (!plan) return
    try {
      const result = await ontologyApi.applyMigration(ontologyId, { plan_id: plan.id })
      setPlan(result)
      setMessage('迁移已切换为当前修订')
      await schemaQuery.refetch()
      await planeQuery.refetch()
    } catch (error: unknown) {
      setMessage(errorMessage(error, '迁移应用失败'))
    }
  }

  if (schemaQuery.isLoading || planeQuery.isLoading) return <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-500"><Loader2 size={14} className="mr-2 inline animate-spin" />读取语义定义与数据平面</div>
  if (schemaQuery.isError) return <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-700">规范语义定义暂不可用，请检查当前修订。</div>

  return <section className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]" data-testid="semantic-control-panel">
    <div className="rounded-xl border border-slate-300 bg-slate-50 p-4">
      <div className="mb-3 flex items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-slate-900">语义定义</h2><p className="mt-1 text-[11px] text-slate-500">规范资源按修订保存；API 标识变更必须经过迁移预检。</p></div><span className="rounded-full bg-white px-2 py-1 text-[11px] text-slate-600">{schemaQuery.data?.schema_version}</span></div>
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">{['object_type', 'property', 'link_type', 'logic_rule'].map((kind) => <div key={kind} className="rounded-lg border border-slate-200 bg-white p-2"><p className="text-[10px] uppercase text-slate-400">{kind}</p><p className="mt-1 text-lg font-semibold text-slate-800">{schemaQuery.data?.counts?.[kind] || 0}</p></div>)}</div>
      <div className="grid gap-2 sm:grid-cols-2">
        {resources.map((resource: SemanticResource) => <button key={resource.resource_id} onClick={() => { setSelectedId(resource.resource_id); setNewApiName(resource.api_name) }} className={`rounded-lg border px-3 py-2 text-left text-xs ${selectedId === resource.resource_id ? 'border-slate-700 bg-white' : 'border-slate-200 bg-white/70 hover:border-slate-400'}`}><span className="font-medium text-slate-800">{resource.display_name || resource.name_cn || resource.api_name}</span><span className="mt-1 block font-mono text-[10px] text-slate-400">{resource.kind} · {resource.api_name}</span></button>)}
      </div>
      {resources.length === 0 && <p className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-xs text-slate-500">当前修订还没有规范资源。</p>}
    </div>
    <aside className="rounded-xl border border-slate-300 bg-white p-4">
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><Database size={14} />数据平面</div>
      <p className="mt-2 text-[11px] text-slate-500">权威来源：{planeQuery.data?.authoritative || 'PostgreSQL'}</p>
      <div className="mt-3 space-y-2">{(planeQuery.data?.projections || []).map((item) => <div key={item.adapter} className="flex items-center justify-between rounded border border-slate-100 px-2 py-1.5 text-[11px]"><span>{item.adapter}</span><span className={item.status === 'ready' || item.status === 'available' ? 'text-emerald-700' : 'text-amber-700'}>{item.status}</span></div>)}</div>
      <div className="mt-4 border-t border-slate-100 pt-4"><div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-800"><ShieldCheck size={14} />迁移预检</div>{selected ? <><p className="mb-2 text-[11px] text-slate-500">当前：<span className="font-mono">{selected.api_name}</span></p><input className={inputClass} value={newApiName} onChange={(event) => setNewApiName(event.target.value)} aria-label="新的 API 名称" /><button onClick={() => void preflight()} className="mt-2 w-full rounded-md bg-slate-800 px-3 py-2 text-xs text-white">检查迁移影响</button>{plan && <button onClick={() => void apply()} disabled={plan.status !== 'validated'} className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-700 disabled:opacity-40">应用影子修订</button>}</> : <p className="text-[11px] text-slate-500">选择一个规范资源开始。</p>}</div>
      {message && <div className="mt-3 flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2 text-[11px] text-slate-700"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{message}</div>}
    </aside>
  </section>
}
