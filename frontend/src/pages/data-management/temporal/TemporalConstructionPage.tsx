import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, CheckCircle2, Clock3, Database, GitCompare, Loader2, Play, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { apiClient, apiClientV2 } from '@/api/client'
import { constructionApi } from '@/api/construction'

type CatalogItem = {
  id: string
  name: string
  kind: string
  time_kind: string
  source_url?: string
  manifest?: { rows_after_downsample?: number; raw_streams?: number; [key: string]: unknown }
}
type Run = {
  id: string
  run_id: string
  ontology_id: string
  status: string
  progress?: { stage?: string; completed?: number; total?: number; issues?: number }
  metrics?: { rows_in?: number; rows_normalized?: number; temporal_issues?: number; nodes_upserted?: number; edges_upserted?: number; summary?: { streams?: number; time_from?: string; time_to?: string }; [key: string]: unknown }
  error?: string | null
}
type Snapshot = { available: boolean; graph_backend: string; at?: string | null; nodes: any[]; edges: any[]; total_nodes: number; total_edges: number; total_available_nodes?: number; total_available_edges?: number }
type Timeline = { events?: Array<{ id: string; timestamp?: string; label?: string; entity_type?: string; value?: unknown }>; count?: number }
type Growth = { points?: Array<{ timestamp: string; observations: number; cumulative_nodes: number }> }

const statusLabel: Record<string, string> = { queued: '排队中', running: '构建中', completed: '已完成', failed: '失败' }

function fmt(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value).replace('T', ' ').replace('+00:00', ' UTC')
}

export default function TemporalConstructionPage() {
  const navigate = useNavigate()
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [timeKind, setTimeKind] = useState<'ordinal' | 'instant' | 'interval'>('instant')
  const [entityColumn, setEntityColumn] = useState('stream_id')
  const [timeColumn, setTimeColumn] = useState('event_time')
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [growth, setGrowth] = useState<Growth | null>(null)
  const [at, setAt] = useState('')
  const [fromAt, setFromAt] = useState('')
  const [toAt, setToAt] = useState('')
  const [diff, setDiff] = useState<any>(null)
  const [playing, setPlaying] = useState(false)
  const [timelineIndex, setTimelineIndex] = useState(0)

  const dataset = catalog[0]
  const events = timeline?.events || []
  const growthPoints = growth?.points || []
  const timelineRange = useMemo(() => {
    const values = events.map(e => e.timestamp).filter(Boolean) as string[]
    return { from: values[0] || '', to: values[values.length - 1] || '' }
  }, [events])

  const loadCatalog = () => {
    setLoading(true); setLoadError('')
    Promise.all([
      apiClientV2.get<CatalogItem[]>('/temporal/catalog'),
      apiClientV2.get('/temporal/catalog/bts_site_b/preview', { params: { limit: 8 } }),
    ]).then(([items, rows]) => { setCatalog(Array.isArray(items) ? items : []); setPreview(rows) })
      .catch((err: any) => setLoadError(err?.detail || err?.message || '时序数据目录加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadCatalog() }, [])

  useEffect(() => {
    if (!run || (run.status !== 'queued' && run.status !== 'running')) return
    const timer = window.setInterval(() => {
      constructionApi.getRun(run.id).then((next: Run) => setRun(next)).catch(() => {})
    }, 1500)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  useEffect(() => {
    if (!playing || growthPoints.length < 2) return
    const timer = window.setInterval(() => {
      setTimelineIndex(index => {
        const next = index >= growthPoints.length - 1 ? 0 : index + 1
        setAt(growthPoints[next].timestamp)
        return next
      })
    }, 1200)
    return () => window.clearInterval(timer)
  }, [playing, growthPoints.length])

  useEffect(() => {
    if (!run || run.status !== 'completed') return
    const summary = run.metrics?.summary || {}
    const latest = summary.time_to || ''
    if (latest) { setAt(String(latest)); setToAt(String(latest)) }
    const first = summary.time_from || timelineRange.from
    if (first) setFromAt(String(first))
    Promise.all([
      apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/snapshot`, { params: { at: latest || undefined, limit: 300 } }),
      apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/timeline`, { params: { limit: 120 } }),
      apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/growth`, { params: { limit: 80 } }),
    ]).then(([snap, line, curve]) => { setSnapshot(snap); setTimeline(line); setGrowth(curve) }).catch(() => {})
  }, [run?.id, run?.status])

  const createRun = async () => {
    setBusy(true); setLoadError(''); setSnapshot(null); setTimeline(null); setGrowth(null); setDiff(null)
    try {
      // Reuse the retained BTS ontology on repeated demonstrations. Creating
      // a new ontology for every click made the runtime fill with duplicates
      // even though FalkorDB writes themselves were idempotent.
      const catalogResult = await apiClient.get<any>('/ontologies?page_size=100')
      let ontology = (catalogResult?.items || []).find((item: any) => String(item.name || '').startsWith('BTS Site B 时序本体'))
      if (!ontology) {
        ontology = await apiClient.post<any>('/ontologies', {
          name: `BTS Site B 时序本体 ${new Date().toLocaleString('zh-CN', { hour12: false }).replace(/[/: ]/g, '-')}`,
          domain: '制造',
          description: 'Building TimeSeries Site B → Brick 时序本体构建演示',
          build_mode: 'temporal_pipeline',
        })
      }
      const created = await apiClientV2.post<Run>(`/ontologies/${ontology.id}/temporal/runs`, {
        source: 'bts_site_b', adapter: 'bts', time_kind: timeKind,
        event_time_column: timeKind === 'instant' ? timeColumn : null,
        sequence_column: timeKind === 'ordinal' ? timeColumn : null,
        entity_id_column: entityColumn, sample_limit: 600,
        model_name: undefined,
      })
      setRun(created)
    } catch (err: any) {
      setLoadError(err?.detail?.message || err?.detail || err?.message || '创建时序构建任务失败')
    } finally { setBusy(false) }
  }

  const loadSnapshot = async () => {
    if (!run || !at) return
    try { setSnapshot(await apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/snapshot`, { params: { at, limit: 300 } })) }
    catch (err: any) { setLoadError(err?.detail || err?.message || '快照加载失败') }
  }

  const jumpTimeline = (index: number) => {
    setTimelineIndex(index)
    const point = growthPoints[index]
    if (point) setAt(point.timestamp)
  }

  const loadDiff = async () => {
    if (!run || !fromAt || !toAt) return
    try { setDiff(await apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/diff`, { params: { from_at: fromAt, to_at: toAt, limit: 300 } })) }
    catch (err: any) { setLoadError(err?.detail || err?.message || '时间差异加载失败') }
  }

  if (loading) return <div className="p-8 text-sm text-gray-400">加载 BTS 时序目录...</div>
  if (loadError && catalog.length === 0) return <div className="max-w-4xl space-y-3"><p className="text-red-600 text-sm">{loadError}</p><button onClick={loadCatalog} className="px-3 py-2 border rounded-lg text-sm">重试</button></div>

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-start justify-between gap-4">
        <div><h2 className="text-xl font-semibold">时序数据本体构建</h2><p className="text-sm text-gray-500 mt-1">将传感器时间序列标准化为 Brick 类别、Observation 节点和可追溯的时序关系。</p></div>
        <button onClick={loadCatalog} className="p-2 border rounded-lg text-gray-500 hover:text-black" title="刷新目录"><RefreshCw size={15} /></button>
      </div>

      <div className="grid md:grid-cols-4 gap-3">
        {[['筛选', '先按数据源和时间窗口减少画布负担', Database], ['可视化', 'Schema、实例和时间轴同步查看', Activity], ['解释', '节点、边和来源证据可定位', ShieldCheck], ['调查', '快照、差异和增长曲线辅助分析', GitCompare]].map(([title, text, Icon]) => { const C = Icon as typeof Activity; return <div key={title as string} className="bg-white border rounded-xl p-4"><C size={17} className="text-gray-500" /><p className="font-medium text-sm mt-2">{title as string}</p><p className="text-xs text-gray-500 mt-1 leading-5">{text as string}</p></div> })}
      </div>

      <section className="bg-white border rounded-xl p-5 space-y-5">
        <div className="flex items-center gap-2"><span className="w-6 h-6 rounded-full bg-black text-white text-xs inline-flex items-center justify-center">1</span><h3 className="font-medium">选择数据</h3></div>
        <div className="border rounded-lg p-4 flex flex-wrap items-center gap-4">
          <div className="flex-1 min-w-[260px]"><p className="font-medium">{dataset?.name || 'BTS Site B'}</p><p className="text-xs text-gray-500 mt-1">真实建筑传感器时序 + Brick Site_B.ttl 元数据；当前为交互演示下采样。</p>{dataset?.source_url && <a href={dataset.source_url} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">查看数据来源</a>}</div>
          <div className="flex gap-4 text-xs text-gray-500"><span>流 {dataset?.manifest?.raw_streams ?? '—'}</span><span>行 {dataset?.manifest?.rows_after_downsample ?? preview?.total_rows ?? '—'}</span><span>时间 instant</span></div>
        </div>
        {preview?.rows?.length > 0 && <div className="overflow-auto border rounded-lg"><table className="w-full text-xs"><thead className="bg-gray-50 text-gray-500"><tr>{preview.columns.map((col: string) => <th key={col} className="text-left px-3 py-2 whitespace-nowrap">{col}</th>)}</tr></thead><tbody>{preview.rows.map((row: any, i: number) => <tr key={i} className="border-t">{preview.columns.map((col: string) => <td key={col} className="px-3 py-2 whitespace-nowrap">{String(row[col] ?? '—')}</td>)}</tr>)}</tbody></table></div>}
      </section>

      <section className="bg-white border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2"><span className="w-6 h-6 rounded-full bg-black text-white text-xs inline-flex items-center justify-center">2</span><h3 className="font-medium">配置时间语义与实体列</h3></div>
        <div className="grid md:grid-cols-3 gap-3 text-sm">
          <label>时间语义<select value={timeKind} onChange={e => setTimeKind(e.target.value as any)} className="mt-1 w-full border rounded-lg px-3 py-2"><option value="instant">instant · 时间点</option><option value="ordinal">ordinal · 顺序/cycle</option><option value="interval">interval · 有效区间</option></select></label>
          <label>实体/流 ID 列<input value={entityColumn} onChange={e => setEntityColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
          <label>{timeKind === 'ordinal' ? '顺序列' : '时间列'}<input value={timeColumn} onChange={e => setTimeColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
        </div>
        <p className="text-xs text-gray-500 bg-gray-50 rounded-lg p-3">非法时间、缺失时间和逆序区间会进入问题清单并跳过，不会自动猜日期。C-MAPSS 的 cycle 使用 ordinal；BTS Site B 使用 instant。</p>
      </section>

      <section className="bg-white border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2"><span className="w-6 h-6 rounded-full bg-black text-white text-xs inline-flex items-center justify-center">3</span><h3 className="font-medium">本体映射与执行</h3></div>
        <div className="flex flex-wrap gap-2 text-xs">{['Building', 'Zone', 'Equipment', 'Point', 'Observation', 'AnomalyEvent'].map(x => <span key={x} className="border rounded-full px-2 py-1 bg-blue-50 text-blue-700">Brick · {x}</span>)}</div>
        <div className="flex items-center gap-3"><button onClick={createRun} disabled={busy || Boolean(run && (run.status === 'queued' || run.status === 'running'))} className="px-4 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-40">{busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}创建时序本体构建任务</button>{run && <span className="text-xs text-gray-500">Run {run.id.slice(0, 8)} · {statusLabel[run.status] || run.status}</span>}</div>
        {run && <div className="border rounded-lg p-4 space-y-3"><div className="flex items-center gap-2 text-sm">{run.status === 'completed' ? <CheckCircle2 size={16} className="text-green-600" /> : run.status === 'failed' ? <TriangleAlert size={16} className="text-red-600" /> : <Clock3 size={16} className="text-amber-600" />}<span>{run.progress?.stage || statusLabel[run.status]}</span><span className="text-gray-400">{run.progress?.completed ?? 0} / {run.progress?.total ?? 0}</span></div>{run.status === 'failed' && <p className="text-sm text-red-600">{run.error || '任务失败'}</p>}{run.metrics && <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs"><span>输入行 <b>{run.metrics.rows_in ?? '—'}</b></span><span>规范化 <b>{run.metrics.rows_normalized ?? '—'}</b></span><span>节点 <b>{run.metrics.nodes_upserted ?? '—'}</b></span><span>边 <b>{run.metrics.edges_upserted ?? '—'}</b></span><span>问题 <b>{run.metrics.temporal_issues ?? 0}</b></span></div>}</div>}
      </section>

      {snapshot && run?.status === 'completed' && <section className="space-y-4">
        <div className="flex items-center justify-between"><div><h3 className="font-medium">4. 结果：筛选—可视化—解释—调查</h3><p className="text-xs text-gray-500 mt-1">FalkorDB 实例图按 300 节点以内渲染，完整数量单独统计。</p></div><button onClick={() => navigate(`/ontologies/${run.ontology_id}?tab=graph`)} className="px-3 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-1">打开 Schema / 实例图 <ArrowRight size={14} /></button></div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">{[['快照节点', snapshot.total_available_nodes ?? snapshot.total_nodes], ['快照边', snapshot.total_available_edges ?? snapshot.total_edges], ['时间事件', timeline?.count ?? '—'], ['累计点', growth?.points?.at(-1)?.cumulative_nodes ?? '—'], ['后端', snapshot.graph_backend]].map(([label, value]) => <div key={label as string} className="bg-white border rounded-xl p-4"><p className="text-xs text-gray-500">{label as string}</p><p className="text-xl font-semibold mt-1">{String(value)}</p>{(label === '快照节点' || label === '快照边') && <p className="text-[10px] text-gray-400 mt-1">画布样本≤300</p>}</div>)}</div>
        <div className="bg-white border rounded-xl p-5 space-y-4"><div className="flex flex-wrap items-end gap-3"><label className="text-xs">快照时间<input type="text" value={at} onChange={e => setAt(e.target.value)} placeholder={timelineRange.to || 'ISO 时间'} className="mt-1 block border rounded-lg px-2 py-1.5 w-64" /></label><button onClick={loadSnapshot} className="px-3 py-1.5 border rounded-lg text-xs">查看当前快照</button><button onClick={() => { const latest = String(run.metrics?.summary?.time_to || ''); setAt(latest); setToAt(latest) }} className="px-3 py-1.5 text-xs text-gray-500">跳到最新</button></div><div className="border rounded-lg bg-gray-50 p-3 space-y-2"><div className="flex items-center justify-between text-xs"><span className="font-medium">时间轴（拖动或播放）</span><button onClick={() => setPlaying(value => !value)} disabled={growthPoints.length < 2} className="border rounded px-2 py-1 bg-white disabled:opacity-40">{playing ? '暂停' : '播放'}</button></div><input type="range" min="0" max={Math.max(0, growthPoints.length - 1)} value={Math.min(timelineIndex, Math.max(0, growthPoints.length - 1))} onChange={e => jumpTimeline(Number(e.target.value))} className="w-full" disabled={growthPoints.length < 2} /><div className="flex justify-between text-[10px] text-gray-500"><span>{fmt(growthPoints[0]?.timestamp || run.metrics?.summary?.time_from)}</span><span>{fmt(growthPoints[timelineIndex]?.timestamp || at)}</span><span>{fmt(growthPoints.at(-1)?.timestamp || run.metrics?.summary?.time_to)}</span></div></div><div className="overflow-auto max-h-64"><table className="w-full text-xs"><thead className="sticky top-0 bg-gray-50"><tr><th className="text-left px-2 py-2">节点</th><th className="text-left px-2 py-2">类型</th><th className="text-left px-2 py-2">event_time</th><th className="text-left px-2 py-2">值</th></tr></thead><tbody>{snapshot.nodes.slice(0, 30).map((node: any) => <tr key={node.id} className="border-t"><td className="px-2 py-1.5 max-w-[240px] truncate">{node.id}</td><td className="px-2 py-1.5">{node.entity_type}</td><td className="px-2 py-1.5">{fmt(node.event_time)}</td><td className="px-2 py-1.5">{fmt(node.properties?.value)}</td></tr>)}</tbody></table></div></div>
        <div className="bg-white border rounded-xl p-5 space-y-3"><div className="flex items-end gap-3 flex-wrap"><label className="text-xs">从<input value={fromAt} onChange={e => setFromAt(e.target.value)} className="mt-1 block border rounded-lg px-2 py-1.5 w-64" /></label><label className="text-xs">到<input value={toAt} onChange={e => setToAt(e.target.value)} className="mt-1 block border rounded-lg px-2 py-1.5 w-64" /></label><button onClick={loadDiff} className="px-3 py-1.5 bg-gray-900 text-white rounded-lg text-xs flex items-center gap-1"><GitCompare size={13} />比较两个时刻</button></div>{diff && <div className="text-xs grid md:grid-cols-4 gap-2"><span className="border rounded p-2">新增节点 {diff.added_nodes?.length || 0}</span><span className="border rounded p-2">移除节点 {diff.removed_nodes?.length || 0}</span><span className="border rounded p-2">新增边 {diff.added_edges?.length || 0}</span><span className="border rounded p-2">移除边 {diff.removed_edges?.length || 0}</span></div>}</div>
        <div className="bg-white border rounded-xl p-5 space-y-4"><p className="text-sm font-medium">时间事件与增长</p>{growthPoints.length > 1 && <svg viewBox="0 0 640 150" className="w-full h-36 border rounded-lg bg-gray-50" role="img" aria-label="累计节点增长曲线"><polyline fill="none" stroke="#111827" strokeWidth="2" points={growthPoints.map((point, index) => { const max = growthPoints.at(-1)?.cumulative_nodes || 1; const x = 8 + (index / (growthPoints.length - 1)) * 624; const y = 140 - (point.cumulative_nodes / max) * 125; return `${x},${y}` }).join(' ')} /><text x="10" y="148" fontSize="9" fill="#6b7280">{fmt(growthPoints[0]?.timestamp)}</text><text x="535" y="148" fontSize="9" fill="#6b7280">{fmt(growthPoints.at(-1)?.timestamp)}</text></svg>}<div className="space-y-1 max-h-48 overflow-auto">{events.slice(0, 40).map(event => <div key={event.id} className="flex items-center gap-3 text-xs"><span className="w-36 text-gray-500 shrink-0">{fmt(event.timestamp)}</span><span className="text-gray-700 truncate">{event.label || event.id}</span><span className="text-gray-400">{event.entity_type}</span><span className="ml-auto">{fmt(event.value)}</span></div>)}</div></div>
      </section>}
    </div>
  )
}
