import { useEffect, useMemo, useState } from 'react'
import { Check, GitCompareArrows, History, Loader2, RotateCcw, TriangleAlert } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

type Revision = {
  id: string
  revision_no: number
  parent_revision_id?: string | null
  source_run_id?: string | null
  graph_namespace?: string | null
  snapshot_hash?: string | null
  summary?: Record<string, number>
  status?: string
  is_current?: boolean
  created_at?: string | null
}
type RevisionResponse = { revisions?: Revision[]; current_revision_id?: string | null }
type DiffSection = { added?: unknown[]; removed?: unknown[]; changed?: unknown[]; added_count?: number; removed_count?: number; changed_count?: number }
type DiffResponse = { left: Revision; right: Revision; diff?: Record<string, DiffSection> }

const sectionLabels: Record<string, string> = { entities: '类与属性', relations: '关系', logic_rules: '规则', actions: '动作' }

function revisionLabel(item?: Revision) {
  return item ? `r${item.revision_no} · ${item.id.slice(0, 8)}` : '—'
}

export default function RevisionsTab({ ontologyId }: { ontologyId: string }) {
  const [items, setItems] = useState<Revision[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [leftId, setLeftId] = useState('')
  const [rightId, setRightId] = useState('')
  const [diff, setDiff] = useState<DiffResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const result = await apiClientV2.get<RevisionResponse>(`/ontologies/${ontologyId}/revisions`)
      const next = result?.revisions || []
      setItems(next)
      setCurrentId(result?.current_revision_id || next.find(item => item.is_current)?.id || null)
      setLeftId(current => current || next[1]?.id || next[0]?.id || '')
      setRightId(current => current || next[0]?.id || '')
    } catch (err: any) {
      setError(err?.detail || err?.message || '版本列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [ontologyId])
  const left = useMemo(() => items.find(item => item.id === leftId), [items, leftId])
  const right = useMemo(() => items.find(item => item.id === rightId), [items, rightId])

  const compare = async () => {
    if (!leftId || !rightId || leftId === rightId) {
      setError('请选择两个不同版本进行比较')
      return
    }
    setBusy(true); setError('')
    try {
      setDiff(await apiClientV2.get<DiffResponse>(`/ontologies/${ontologyId}/revisions/compare`, { params: { left: leftId, right: rightId } }))
    } catch (err: any) {
      setError(err?.detail || err?.message || '版本差异加载失败')
    } finally { setBusy(false) }
  }

  const restore = async (revision: Revision) => {
    if (!window.confirm(`以 ${revisionLabel(revision)} 创建新的当前修订？历史版本不会被覆盖。`)) return
    setBusy(true); setError('')
    try {
      const created = await apiClientV2.post<Revision>(`/ontologies/${ontologyId}/revisions/${revision.id}/restore`)
      await load()
      setRightId(created.id)
    } catch (err: any) {
      setError(err?.detail || err?.message || '恢复版本失败')
    } finally { setBusy(false) }
  }

  if (loading) return <div className="wb-empty"><Loader2 size={17} className="animate-spin" />加载版本</div>
  return <div className="space-y-4">
    {error && <div className="wb-alert wb-alert-danger"><TriangleAlert size={15} />{error}</div>}
    <div className="wb-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div><div className="wb-section-kicker"><History size={13} /> 版本历史</div><h3 className="wb-section-title mt-1">不可变修订</h3></div>
        <span className="wb-status">{items.length} 个版本</span>
      </div>
      {items.length === 0 ? <div className="wb-empty mt-4"><History size={18} />尚未生成修订</div> : <div className="mt-4 grid md:grid-cols-2 xl:grid-cols-3 gap-3">
        {items.map(item => <div key={item.id} className={`rounded-lg border p-4 ${item.id === currentId ? 'border-emerald-300 bg-emerald-50/40' : 'border-gray-200'}`}>
          <div className="flex items-center justify-between gap-2"><strong>r{item.revision_no}</strong>{item.id === currentId ? <span className="wb-status wb-status-success"><Check size={11} />当前</span> : <span className="wb-status">历史</span>}</div>
          <p className="mt-2 text-[11px] font-mono text-gray-500 break-all">{item.id}</p>
          <p className="mt-2 text-xs text-gray-500">{item.created_at ? new Date(item.created_at).toLocaleString() : '—'} · {item.summary?.entity_count ?? 0} 类 · {item.summary?.relation_count ?? 0} 关系</p>
          <button type="button" disabled={busy || item.id === currentId} onClick={() => restore(item)} className="wb-button-secondary mt-3 text-xs disabled:opacity-40"><RotateCcw size={13} />恢复为新修订</button>
        </div>)}
      </div>}
    </div>
    <div className="wb-surface p-5">
      <div className="flex items-center gap-2"><GitCompareArrows size={16} className="text-blue-600" /><h3 className="text-sm font-semibold">版本对比</h3></div>
      <div className="mt-4 grid md:grid-cols-[1fr_1fr_auto] gap-3 items-end">
        <label className="wb-label">左侧版本<select className="wb-input mt-1" value={leftId} onChange={event => setLeftId(event.target.value)}>{items.map(item => <option key={item.id} value={item.id}>{revisionLabel(item)}</option>)}</select></label>
        <label className="wb-label">右侧版本<select className="wb-input mt-1" value={rightId} onChange={event => setRightId(event.target.value)}>{items.map(item => <option key={item.id} value={item.id}>{revisionLabel(item)}</option>)}</select></label>
        <button type="button" className="wb-button-primary" disabled={busy || items.length < 2} onClick={compare}><GitCompareArrows size={14} />{busy ? '处理中' : '比较'}</button>
      </div>
      {left && right && <p className="mt-3 text-xs text-gray-500">{revisionLabel(left)} → {revisionLabel(right)} · 快照哈希仅用于核验来源。</p>}
      {diff && <div className="mt-4 grid md:grid-cols-2 xl:grid-cols-4 gap-3">{Object.entries(diff.diff || {}).map(([key, value]) => <div key={key} className="rounded-lg border border-gray-200 p-3"><p className="text-sm font-medium">{sectionLabels[key] || key}</p><div className="mt-2 grid grid-cols-3 gap-2 text-xs"><span className="text-emerald-700">新增 {value.added_count ?? value.added?.length ?? 0}</span><span className="text-red-700">删除 {value.removed_count ?? value.removed?.length ?? 0}</span><span className="text-amber-700">修改 {value.changed_count ?? value.changed?.length ?? 0}</span></div></div>)}</div>}
    </div>
  </div>
}
