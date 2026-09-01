import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, CheckCircle2, Download, Eye, RefreshCw, TriangleAlert } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

type Source = {
  id: string; name: string; installed: boolean; dataset_id?: string | null
  records?: number | null; participants?: number | null; categories?: number | null
  date_from?: string | null; date_to?: string | null; source_url?: string
  time_kind?: string; time_precision?: string; supports?: string[]; manifest?: Record<string, unknown>
}
type Run = { id: string; ontology_id: string; status: string; config?: Record<string, any>; metrics?: Record<string, any>; created_at?: string; error?: string }

const statusText: Record<string, string> = { queued: '排队中', running: '构建中', completed: '已完成', failed: '失败', cancelled: '已取消' }

export default function TemporalSourcesPage() {
  const navigate = useNavigate()
  const [sources, setSources] = useState<Source[]>([])
  const [source, setSource] = useState<Source | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [sources, history] = await Promise.all([
        apiClientV2.get<Source[]>('/temporal/sources'),
        apiClientV2.get<Run[]>('/temporal/runs', { params: { limit: 20 } }),
      ])
      const list = Array.isArray(sources) ? sources : []
      setSources(list)
      const first = list.find(item => item.id === 'icews_2023_demo') || list[0] || null
      setSource(first)
      setRuns(Array.isArray(history) ? history : [])
      if (first?.installed) setPreview(await apiClientV2.get(`/temporal/sources/${first.id}/preview`, { params: { offset: 0, limit: 8 } }))
      else setPreview(null)
    } catch (e: any) {
      setError(e?.detail?.message || e?.detail || e?.message || '时序数据源加载失败')
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const install = async () => {
    setBusy(true); setError('')
    try { await apiClientV2.post('/temporal/sources/icews_2023_demo/install'); await load() }
    catch (e: any) { setError(e?.detail?.message || e?.detail || e?.message || 'ICEWS 安装失败') }
    finally { setBusy(false) }
  }

  if (loading) return <div className="p-6 text-sm text-gray-400">正在读取时序数据源...</div>
  return <div className="max-w-6xl space-y-6">
    <div className="flex items-start justify-between gap-3">
      <div><h2 className="text-xl font-semibold">时序数据</h2><p className="text-sm text-gray-500 mt-1">选择数据源 → 筛选事件 → 确认 Instant → 构建并调查时序图谱。</p></div>
      <button onClick={load} className="p-2 border rounded-lg text-gray-500 hover:text-black" title="刷新"><RefreshCw size={15} /></button>
    </div>
    {error && <div className="border border-red-200 bg-red-50 text-red-700 rounded-lg px-4 py-3 text-sm flex gap-2"><TriangleAlert size={16} />{error}</div>}
    <section className="bg-white border rounded-xl p-5 space-y-5">
      <div className="flex items-center justify-between"><div><p className="text-xs uppercase tracking-wide text-gray-400">步骤 1 · 选择数据</p><h3 className="font-semibold mt-1">官方 ICEWS 2023 三日事件切片</h3></div><span className={`text-xs px-2 py-1 rounded-full ${source?.installed ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>{source?.installed ? '已安装' : '未安装'}</span></div>
      <div className="grid md:grid-cols-5 gap-3 text-sm">
        <div className="border rounded-lg p-3"><p className="text-xs text-gray-400">事件</p><p className="text-lg font-semibold">{source?.records ?? '—'}</p></div>
        <div className="border rounded-lg p-3"><p className="text-xs text-gray-400">参与者</p><p className="text-lg font-semibold">{source?.participants ?? '—'}</p></div>
        <div className="border rounded-lg p-3"><p className="text-xs text-gray-400">类别</p><p className="text-lg font-semibold">{source?.categories ?? '—'}</p></div>
        <div className="border rounded-lg p-3"><p className="text-xs text-gray-400">日期范围</p><p className="text-xs font-medium mt-1">{source?.date_from || '—'}<br />{source?.date_to || ''}</p></div>
        <div className="border rounded-lg p-3"><p className="text-xs text-gray-400">时间语义</p><p className="text-sm font-medium mt-1">Instant · day</p></div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {!source?.installed && <button disabled={busy} onClick={install} className="px-4 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-40"><Download size={14} />{busy ? '下载并校验中...' : '安装官方 ICEWS 样例'}</button>}
        {source?.installed && <button onClick={() => navigate('/data/temporal/new')} className="px-4 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2">开始构建向导 <ArrowRight size={14} /></button>}
        {source?.source_url && <a href={source.source_url} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">查看 DOI / 使用条款</a>}
      </div>
      {preview?.rows?.length > 0 && <div><div className="flex items-center gap-2 text-sm font-medium mb-2"><Eye size={14} />真实数据预览（前 8 行）</div><div className="overflow-auto border rounded-lg"><table className="w-full text-xs"><thead className="bg-gray-50"><tr>{preview.columns.filter((c: string) => !c.startsWith('_')).slice(0, 10).map((c: string) => <th className="text-left px-3 py-2 whitespace-nowrap" key={c}>{c}</th>)}</tr></thead><tbody>{preview.rows.map((row: any, i: number) => <tr className="border-t" key={i}>{preview.columns.filter((c: string) => !c.startsWith('_')).slice(0, 10).map((c: string) => <td className="px-3 py-2 whitespace-nowrap max-w-[220px] truncate" key={c}>{String(row[c] ?? '—')}</td>)}</tr>)}</tbody></table></div></div>}
    </section>
    {sources.filter(item => item.id !== 'icews_2023_demo').length > 0 && <section className="bg-white border rounded-xl p-5 space-y-3"><div><h3 className="font-semibold">已有 Dataset（可在向导中选择）</h3><p className="text-xs text-gray-500 mt-1">常规数据不会自动写入时序图；选择后仍需在向导中确认时间列和实体列。</p></div><div className="grid md:grid-cols-2 gap-3">{sources.filter(item => item.id !== 'icews_2023_demo').map(item => <button key={item.id} onClick={() => navigate('/data/temporal/new')} className="text-left border rounded-lg p-3 hover:border-gray-400"><p className="text-sm font-medium">{item.name}</p><p className="text-xs text-gray-500 mt-1">{item.records ?? '—'} 条记录 · {(item.supports || []).join(' / ')}</p></button>)}</div></section>}
    <section className="bg-white border rounded-xl p-5 space-y-3">
      <div className="flex items-center gap-2"><Activity size={16} /><h3 className="font-semibold">历史构建任务</h3></div>
      {runs.length === 0 ? <p className="text-sm text-gray-400">还没有运行。完成上面的向导后，任务会出现在这里。</p> : <div className="divide-y border rounded-lg">{runs.map(run => <button key={run.id} onClick={() => navigate(`/data/temporal/runs/${run.id}`)} className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-gray-50"><span className={`w-2 h-2 rounded-full ${run.status === 'completed' ? 'bg-green-500' : run.status === 'failed' ? 'bg-red-500' : 'bg-amber-500'}`} /><span className="flex-1"><span className="block text-sm font-medium">{run.config?.source_id === 'icews_2023_demo' ? 'ICEWS 2023 事件图谱' : '时序构建任务'}</span><span className="text-xs text-gray-400">{run.created_at ? new Date(run.created_at).toLocaleString() : run.id}</span></span><span className="text-xs text-gray-500">{statusText[run.status] || run.status}</span><ArrowRight size={14} className="text-gray-400" /></button>)}</div>}
    </section>
  </div>
}
