import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'

type Asset = {
  id: string; asset_key: string; name: string; kind: string; version: string;
  description: string; implementation: string; status: string;
  interface_key: string; interface_version: string; executor_type: string;
  input_schema?: { required?: string[] };
  binding_spec?: { fields?: Record<string, string> };
}
type AssetList = { assets: Asset[] }
const errorText = (error: unknown) => error instanceof Error ? error.message : JSON.stringify(error)

const samples: Record<string, object> = {
  release_gate_v1: { tool_condition:'good', visual_inspection:'pass', machining_finalized:true, clamp_pressure:40 },
  cycle_time_v1: { setup_seconds:120, unit_seconds:18, quantity:10 },
  observation_summary_v1: { values:[12,13,14,15,16], anomaly_threshold:3 },
  tool_wear_risk_v1: { temperature:92, vibration:3.1, pressure:11 },
  resource_assignment_v1: { tasks:[{id:'op-1',duration:3,eligible_resources:['machine-a','machine-b']},{id:'op-2',duration:2,eligible_resources:['machine-a','machine-b']}], resources:[{id:'machine-a',capacity:8},{id:'machine-b',capacity:8}] },
  priority_plan_v1: { tasks:[{id:'cut',duration:2,priority:1,resource:'machine-a'},{id:'finish',duration:1,priority:2,resource:'machine-a',predecessors:['cut']},{id:'inspect',duration:1,priority:3,resource:'qa',predecessors:['finish']}] },
}

export default function LogicAssetsTab({ ontologyId }: { ontologyId: string }) {
  const cache = useQueryClient()
  const queryKey = ['logic-assets', ontologyId]
  const { data, isPending, error: loadError, refetch } = useQuery({
    queryKey,
    queryFn: ({ signal }) => apiClientV2.get<AssetList>(`/ontologies/${ontologyId}/logic-assets`, { signal }),
  })
  const assets = data?.assets ?? []
  const [selectedId, setSelectedId] = useState<string>()
  const selected = assets.find(asset => asset.id === selectedId) ?? assets[0]
  const [editedText, setText] = useState<string>()
  const text = editedText ?? JSON.stringify(samples[selected?.asset_key ?? ''] ?? {}, null, 2)
  const [result, setResult] = useState<Record<string, unknown>>()
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [error, setError] = useState('')
  const select = (asset: Asset) => {
    if (inFlight.current) return
    setSelectedId(asset.id); setText(undefined); setResult(undefined); setError('')
  }
  const seed = async () => {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError('')
    try {
      const response = await apiClientV2.post<AssetList>(`/ontologies/${ontologyId}/logic-assets/seed`)
      cache.setQueryData(queryKey, response)
      setSelectedId(response.assets[0]?.id); setText(undefined); setResult(undefined)
    } catch (error) { setError(errorText(error)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  const run = async () => {
    if (!selected || inFlight.current) return
    inFlight.current = true; setBusy(true); setError(''); setResult(undefined)
    try {
      setResult(await apiClientV2.post<Record<string, unknown>>(`/ontologies/${ontologyId}/logic-assets/${selected.id}/run`, { inputs: JSON.parse(text) }))
    } catch (error) { setError(errorText(error)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  if (isPending) return <p role="status">正在加载逻辑资产…</p>
  if (loadError) return <div role="alert">加载失败：{errorText(loadError)}<button onClick={() => { void refetch() }} className="ml-3 underline">重新加载</button></div>
  return <div className="space-y-5">
    <div className="rounded-xl border bg-white p-5"><div className="flex items-center justify-between"><div><h2 className="text-xl font-semibold">本地逻辑绑定</h2><p className="mt-1 text-sm text-slate-500">统一输入/输出契约，调用业务、数学、统计、机器学习、优化和规划逻辑；每次运行都会保存版本与执行轨迹。</p></div><button onClick={seed} disabled={busy} className="rounded bg-slate-900 px-4 py-2 text-white">初始化六类逻辑</button></div>{error&&<pre className="mt-3 rounded bg-red-50 p-3 text-xs text-red-700 whitespace-pre-wrap">{error}</pre>}</div>
    <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div className="space-y-3">{assets.length===0&&<div className="rounded-xl border bg-white p-6 text-slate-500">尚未注册逻辑资产，请点击“初始化六类逻辑”。</div>}{assets.map(a=><button key={a.id} onClick={()=>select(a)} className={`block w-full rounded-xl border bg-white p-4 text-left ${selected?.id===a.id?'border-violet-500 ring-2 ring-violet-100':''}`}><div className="flex justify-between"><span className="font-semibold">{a.name}</span><span className="text-xs text-slate-500">{a.kind} · v{a.version}</span></div><p className="mt-1 text-sm text-slate-600">{a.description}</p><p className="mt-2 text-xs text-slate-400">实现：{a.implementation} · {a.status}</p></button>)}</div>
      {selected&&<div className="rounded-xl border bg-white p-5"><h3 className="font-semibold">运行 {selected.name}</h3><p className="mt-1 text-sm text-slate-500">接口：{selected.interface_key} · v{selected.interface_version} · 执行器：{selected.executor_type}</p><p className="mt-1 text-sm text-slate-500">输入字段：{(selected.input_schema?.required||[]).join('、')}</p><p className="mt-1 text-xs text-slate-400">绑定：{Object.entries(selected.binding_spec?.fields||{}).map(([k,v])=>`${k} ← ${v}`).join('；') || '直接输入'}</p><textarea value={text} onChange={e=>setText(e.target.value)} className="mt-4 h-64 w-full rounded border bg-slate-950 p-4 font-mono text-sm text-emerald-200" spellCheck={false}/><div className="mt-3 flex gap-2"><button onClick={()=>setText(JSON.stringify(samples[selected.asset_key]||{},null,2))} className="rounded border px-3 py-2">载入示例</button><button onClick={run} disabled={busy} className="rounded bg-violet-600 px-4 py-2 text-white">{busy?'运行中…':'执行本地逻辑'}</button></div>{result&&<div className="mt-5"><h4 className="font-semibold">结构化输出</h4><pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-100 p-4 text-xs">{JSON.stringify(result,null,2)}</pre></div>}</div>}
    </div>
  </div>
}
