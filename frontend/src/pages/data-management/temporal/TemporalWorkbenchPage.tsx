import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Eye, GitCompare, Pause, Play, RefreshCw, TriangleAlert } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import cytoscape from 'cytoscape'

type Node = { id: string; entity_type?: string; labels?: string[]; properties?: Record<string, any>; event_time?: string | null }
type Edge = { id: string; source: string; target: string; type: string; properties?: Record<string, any> }
type Run = { id: string; run_id: string; ontology_id: string; status: string; config?: Record<string, any>; progress?: Record<string, any>; metrics?: Record<string, any>; error?: string | null }
type Snapshot = { available: boolean; graph_backend: string; nodes: Node[]; edges: Edge[]; total_nodes: number; total_edges: number; total_available_nodes?: number; total_available_edges?: number; mode?: string; at?: string }

const fmt = (x: any) => x === null || x === undefined || x === '' ? '—' : String(x).replace('T', ' ')
const statusText: Record<string, string> = { queued: '排队中', running: '构建中', completed: '已完成', failed: '失败' }
const colors: Record<string, string> = { Actor: '#2563eb', InteractionEvent: '#dc2626', EventCategory: '#7c3aed', Country: '#059669', Location: '#d97706' }
const fallbackColors = ['#2563eb', '#059669', '#dc2626', '#7c3aed', '#d97706', '#0891b2', '#db2777']
function stableColor(value: string) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0
  return fallbackColors[Math.abs(hash) % fallbackColors.length]
}
const CanvasOptionsContext = createContext<{ hideLabels: boolean; fitCanvas: boolean; selectedId?: string }>({ hideLabels: false, fitCanvas: true })

function GraphCanvas({ nodes, edges, onSelect, hideLabels = false, fitCanvas = true, selectedId }: { nodes: Node[]; edges: Edge[]; onSelect: (node: Node) => void; hideLabels?: boolean; fitCanvas?: boolean; selectedId?: string }) {
  const canvasOptions = useContext(CanvasOptionsContext)
  const effectiveHideLabels = hideLabels || canvasOptions.hideLabels
  const effectiveFitCanvas = fitCanvas && canvasOptions.fitCanvas
  const effectiveSelectedId = selectedId || canvasOptions.selectedId
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const visibleNodes = nodes.slice(0, 200)
    const ids = new Set(visibleNodes.map(node => node.id))
    const visibleEdges = edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)).slice(0, 300)
    const degree = new Map<string, number>()
    visibleNodes.forEach(node => degree.set(node.id, 0))
    visibleEdges.forEach(edge => {
      degree.set(edge.source, (degree.get(edge.source) || 0) + 1)
      degree.set(edge.target, (degree.get(edge.target) || 0) + 1)
    })
    const elements = [
      ...visibleNodes.map(node => {
        const type = node.entity_type || node.labels?.[0] || 'Entity'
        const label = String(node.properties?.name || node.properties?.event_type || node.properties?.label || node.id).slice(0, 24)
        return { data: { id: node.id, label, color: colors[type] || stableColor(type), size: 52, degree: degree.get(node.id) || 0, raw: node } }
      }),
      ...visibleEdges.map(edge => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.type, raw: edge } })),
    ]
    cyRef.current?.destroy()
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        { selector: 'node', style: { label: effectiveHideLabels ? '' : 'data(label)', 'background-color': 'data(color)', color: '#fff', 'font-size': '9px', 'font-weight': 'bold', 'text-valign': 'center', 'text-halign': 'center', width: 'data(size)', height: 'data(size)', 'text-wrap': 'wrap', 'text-max-width': '46px', 'text-outline-width': 2, 'text-outline-color': 'data(color)' } },
        { selector: 'edge', style: { label: effectiveHideLabels ? '' : 'data(label)', 'font-size': '8px', color: '#374151', 'line-color': '#9ca3af', 'target-arrow-color': '#9ca3af', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'text-background-color': '#fff', 'text-background-opacity': 0.9, 'text-background-padding': '2px' } },
        { selector: '.dimmed', style: { opacity: 0.18 } },
        { selector: '.highlighted', style: { 'line-color': '#1d4ed8', 'target-arrow-color': '#1d4ed8', 'background-color': '#1d4ed8', 'border-width': 3, 'border-color': '#fff' } },
      ],
      layout: { name: 'cose', animate: false, randomize: true, numIter: visibleNodes.length > 120 ? 250 : 500, idealEdgeLength: 110, nodeRepulsion: 6000, componentSpacing: 80 } as any,
    })
    cy.on('tap', 'node', event => {
      const node = event.target
      cy.elements().removeClass('highlighted dimmed')
      node.addClass('highlighted')
      node.neighborhood().addClass('highlighted')
      cy.elements().not('.highlighted').addClass('dimmed')
      onSelect(node.data('raw') as Node)
    })
    cy.on('tap', event => {
      if (event.target === cy) {
        cy.elements().removeClass('highlighted dimmed')
      }
    })
    if (effectiveSelectedId) cy.getElementById(effectiveSelectedId).addClass('highlighted')
    cyRef.current = cy
    if (effectiveFitCanvas) cy.fit(cy.elements(), 28)
    return () => { cy.destroy(); cyRef.current = null }
  }, [nodes, edges, onSelect, effectiveHideLabels, effectiveFitCanvas, effectiveSelectedId])

  return <div ref={containerRef} data-testid="icews-temporal-graph-canvas" role="img" aria-label="ICEWS 时序知识图谱" className="border rounded-lg bg-slate-50 h-[430px]" />
}

export default function TemporalWorkbenchPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const [run, setRun] = useState<Run | null>(null)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [timeline, setTimeline] = useState<any>(null)
  const [growth, setGrowth] = useState<any>(null)
  const [selected, setSelected] = useState<Node | null>(null)
  const [evidence, setEvidence] = useState<any>(null)
  const [view, setView] = useState<'ontology' | 'actors' | 'evidence'>('ontology')
  const [mode, setMode] = useState<'cumulative' | 'window'>('cumulative')
  const [at, setAt] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [country, setCountry] = useState('')
  const [participant, setParticipant] = useState('')
  const [eventType, setEventType] = useState('')
  const [category, setCategory] = useState('')
  const [intensityMin, setIntensityMin] = useState('')
  const [intensityMax, setIntensityMax] = useState('')
  const [nodeSearch, setNodeSearch] = useState('')
  const [egoDepth, setEgoDepth] = useState(0)
  const [hideLabels, setHideLabels] = useState(false)
  const [fitCanvas, setFitCanvas] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [diff, setDiff] = useState<any>(null)
  const [fromAt, setFromAt] = useState('')
  const [toAt, setToAt] = useState('')
  const [error, setError] = useState('')
  const requestSequence = useRef(0)

  const loadRun = async () => {
    if (!runId) return
    const sequence = ++requestSequence.current
    try {
      const current = await apiClientV2.get<Run>(`/construction-runs/${runId}`)
      setRun(current)
      if (current.status === 'completed') {
        const ontologyId = current.ontology_id
        const params: Record<string, any> = { mode, limit: 200 }
        if (at) params.at = at
        if (dateFrom) params.date_from = dateFrom
        if (dateTo) params.date_to = dateTo
        if (country) params.country = country
        if (participant) params.participant = participant
        if (eventType) params.event_type = eventType
        if (category) params.category = category
        if (intensityMin) params.intensity_min = intensityMin
        if (intensityMax) params.intensity_max = intensityMax
        const [snap, events, curve] = await Promise.all([
          apiClientV2.get<Snapshot>(`/ontologies/${ontologyId}/temporal/snapshot`, { params }),
          apiClientV2.get(`/ontologies/${ontologyId}/temporal/timeline`, { params: { category: category || undefined, limit: 500 } }),
          apiClientV2.get(`/ontologies/${ontologyId}/temporal/growth`, { params: { limit: 100 } }),
        ])
        // A quick timeline drag can leave earlier requests in flight.  Do not
        // let an older response overwrite the latest selected snapshot.
        if (sequence !== requestSequence.current) return
        setSnapshot(snap); setTimeline(events); setGrowth(curve)
        // Start at the latest available day so the initial snapshot and the
        // slider label describe the same state.  The follow-up effect reloads
        // that cumulative snapshot with the explicit `at` value.
        if (!at && events?.dates?.length) setAt(String(events.dates[events.dates.length - 1]))
      }
    } catch (e: any) { setError(e?.detail?.message || e?.detail || e?.message || '运行结果加载失败') }
  }
  useEffect(() => { loadRun() }, [runId, mode, at, dateFrom, dateTo, country, participant, eventType, category, intensityMin, intensityMax])
  useEffect(() => { if (!run || run.status === 'completed' || run.status === 'failed') return; const timer = window.setInterval(loadRun, 1200); return () => window.clearInterval(timer) }, [run?.status, runId])
  useEffect(() => { if (!playing || !growth?.points?.length) return; const timer = window.setInterval(() => { const points = growth.points; const index = points.findIndex((point: any) => point.timestamp === at); const next = points[(index + 1) % points.length]; setAt(next?.timestamp || points[0].timestamp) }, 1000); return () => window.clearInterval(timer) }, [playing, growth, at])

  const visibleNodes = useMemo(() => {
    const allNodes = snapshot?.nodes || []
    let nodes = view === 'actors' ? allNodes.filter(node => node.entity_type === 'Actor') : allNodes
    // Keep the event's directly connected context in the evidence view so
    // clicking an InteractionEvent shows a meaningful source/category/place
    // neighbourhood instead of an isolated dot.
    if (view === 'evidence') {
      const eventIds = new Set(allNodes.filter(node => node.entity_type === 'InteractionEvent').map(node => node.id))
      const related = new Set<string>(eventIds)
      ;(snapshot?.edges || []).forEach(edge => {
        if (eventIds.has(edge.source) || eventIds.has(edge.target)) {
          related.add(edge.source); related.add(edge.target)
        }
      })
      nodes = allNodes.filter(node => related.has(node.id))
    }
    const query = nodeSearch.trim().toLowerCase()
    if (query) nodes = nodes.filter(node => `${node.id} ${JSON.stringify(node.properties || {})}`.toLowerCase().includes(query))
    if (egoDepth > 0 && selected) {
      const allowed = new Set<string>([selected.id])
      let frontier = new Set<string>([selected.id])
      for (let depth = 0; depth < egoDepth; depth += 1) {
        const next = new Set<string>()
        ;(snapshot?.edges || []).forEach(edge => {
          if (frontier.has(edge.source)) next.add(edge.target)
          if (frontier.has(edge.target)) next.add(edge.source)
        })
        next.forEach(id => allowed.add(id)); frontier = next
      }
      nodes = nodes.filter(node => allowed.has(node.id))
    }
    return nodes
  }, [snapshot, view, nodeSearch, egoDepth, selected])
  const visibleEdges = useMemo(() => {
    const edges = snapshot?.edges || []
    if (view !== 'actors') return edges
    // Semantica's actor projection folds each InteractionEvent into a labelled
    // Actor → Actor edge.  The underlying event graph remains untouched; this
    // is only a display projection for investigation.
    const events = new Map((snapshot?.nodes || []).filter(node => node.entity_type === 'InteractionEvent').map(node => [node.id, node]))
    const initiated = new Map<string, Edge>()
    const projected: Edge[] = []
    edges.filter(edge => edge.type === 'INITIATED').forEach(edge => initiated.set(edge.target, edge))
    edges.filter(edge => edge.type === 'TARGETED').forEach(edge => {
      const start = initiated.get(edge.source)
      const event = events.get(edge.source)
      if (!start || !event) return
      projected.push({
        id: `${start.source}:ACTED_ON:${edge.target}:${event.id}`,
        source: start.source,
        target: edge.target,
        type: String(event.properties?.event_type || 'ACTED_ON'),
        properties: { event_id: event.properties?.event_id, event_time: event.event_time, cameo_code: event.properties?.cameo_code, intensity: event.properties?.intensity },
      })
    })
    return projected
  }, [snapshot, view])
  const graphNodes = useMemo(() => {
    let nodes = visibleNodes
    const query = nodeSearch.trim().toLowerCase()
    if (query) nodes = nodes.filter(node => `${node.id} ${JSON.stringify(node.properties || {})}`.toLowerCase().includes(query))
    if (egoDepth > 0 && selected) {
      const allowed = new Set<string>([selected.id])
      let frontier = new Set<string>([selected.id])
      for (let depth = 0; depth < egoDepth; depth += 1) {
        const next = new Set<string>()
        visibleEdges.forEach(edge => {
          if (frontier.has(edge.source)) next.add(edge.target)
          if (frontier.has(edge.target)) next.add(edge.source)
        })
        next.forEach(id => allowed.add(id)); frontier = next
      }
      nodes = nodes.filter(node => allowed.has(node.id))
    }
    return nodes
  }, [visibleNodes, visibleEdges, nodeSearch, egoDepth, selected])
  const graphEdges = useMemo(() => {
    const ids = new Set(graphNodes.map(node => node.id))
    return visibleEdges.filter(edge => ids.has(edge.source) && ids.has(edge.target))
  }, [graphNodes, visibleEdges])
  const dates = (timeline?.dates?.length
    ? timeline.dates
    : Array.from(new Set((timeline?.events || []).map((event: any) => event.timestamp).filter(Boolean)))) as string[]
  const selectNode = async (node: Node) => {
    setSelected(node); setEvidence(null)
    if (!run) return
    try { setEvidence(await apiClientV2.get(`/assertions/${encodeURIComponent(node.id)}/provenance`, { params: { ontology_id: run.ontology_id } })) } catch { /* provenance may not exist for context nodes */ }
  }
  const compare = async () => { if (!run || !fromAt || !toAt) return; try { setDiff(await apiClientV2.get(`/ontologies/${run.ontology_id}/temporal/diff`, { params: { from_at: fromAt, to_at: toAt, limit: 200 } })) } catch (e: any) { setError(e?.detail || e?.message || '差异查询失败') } }

  if (!run || (run.status !== 'completed' && run.status !== 'failed')) return <div className="max-w-5xl space-y-4"><button onClick={() => navigate('/data/temporal')} className="text-xs text-gray-500 flex gap-1 items-center"><ArrowLeft size={13} />返回时序数据</button><div className="bg-white border rounded-xl p-6"><p className="font-medium">{statusText[run?.status || 'queued'] || run?.status || '读取任务'}</p><p className="text-sm text-gray-500 mt-2">{run?.progress?.stage || '正在准备任务...'}　{run?.progress?.completed ?? 0} / {run?.progress?.total ?? 0}</p><div className="h-2 bg-gray-100 rounded mt-4 overflow-hidden"><div className="h-full bg-black transition-all" style={{ width: `${run?.progress?.total ? Math.min(100, (Number(run.progress.completed || 0) / Number(run.progress.total)) * 100) : 8}%` }} /></div></div></div>
  if (run.status === 'failed') return <div className="max-w-5xl space-y-4"><button onClick={() => navigate('/data/temporal')} className="text-xs text-gray-500 flex gap-1 items-center"><ArrowLeft size={13} />返回</button><div className="border border-red-200 bg-red-50 rounded-xl p-5 text-red-700 flex gap-2"><TriangleAlert size={16} />{run.error || '构建失败'}</div></div>

  return <CanvasOptionsContext.Provider value={{ hideLabels, fitCanvas, selectedId: selected?.id }}><div className="max-w-[1400px] space-y-4">
    <div className="flex items-start justify-between gap-3"><div><button onClick={() => navigate('/data/temporal')} className="text-xs text-gray-500 flex gap-1 items-center mb-2"><ArrowLeft size={13} />返回时序数据</button><h2 className="text-xl font-semibold">ICEWS 时序图谱调查工作台</h2><p className="text-sm text-gray-500 mt-1">筛选 → 可视化 → 解释 → 调查（FalkorDB · 画布最多 200 节点）</p></div><button onClick={loadRun} className="p-2 border rounded-lg"><RefreshCw size={15} /></button></div>
    <div className="bg-white border rounded-xl p-4 flex flex-wrap items-end gap-3"><label className="text-xs">节点搜索<input value={nodeSearch} onChange={e => setNodeSearch(e.target.value)} placeholder="ID、名称或事件文本" className="mt-1 border rounded px-2 py-1.5 w-64" /></label><button disabled={!selected} onClick={() => setEgoDepth(1)} className="border rounded px-3 py-1.5 text-xs disabled:opacity-40">一跳 Ego</button><button disabled={!selected} onClick={() => setEgoDepth(2)} className="border rounded px-3 py-1.5 text-xs disabled:opacity-40">两跳 Ego</button><button onClick={() => setEgoDepth(0)} className="border rounded px-3 py-1.5 text-xs">清除 Ego</button><label className="flex items-center gap-2 text-xs pb-1"><input type="checkbox" checked={hideLabels} onChange={e => setHideLabels(e.target.checked)} />隐藏节点标签</label><button onClick={() => setFitCanvas(value => !value)} className="border rounded px-3 py-1.5 text-xs">{fitCanvas ? '放大画布' : '适配画布'}</button><span className="text-[11px] text-gray-400 pb-1">搜索和 Ego 只改变当前画布，不修改 FalkorDB 数据。</span></div>
    {error && <div className="border border-red-200 bg-red-50 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}
    <div className="grid grid-cols-2 md:grid-cols-6 gap-3">{[['事件', run.metrics?.summary?.events ?? run.metrics?.rows_normalized ?? '—'], ['参与者', run.metrics?.summary?.participants ?? '—'], ['类别', run.metrics?.summary?.categories ?? '—'], ['节点', snapshot?.total_available_nodes ?? run.metrics?.nodes_upserted ?? '—'], ['边', snapshot?.total_available_edges ?? run.metrics?.edges_upserted ?? '—'], ['问题', run.metrics?.temporal_issues ?? 0]].map(([label, value]) => <div className="bg-white border rounded-lg p-3" key={label as string}><p className="text-xs text-gray-400">{label}</p><p className="text-xl font-semibold mt-1">{value}</p></div>)}</div>
    <div className="grid xl:grid-cols-[230px_1fr_300px] gap-4 items-start"><aside className="bg-white border rounded-xl p-4 space-y-4"><p className="text-sm font-semibold">筛选与视图</p><label className="block text-xs">日期从<input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">日期到<input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">国家<input value={country} onChange={e => setCountry(e.target.value)} placeholder="Ukraine" className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">参与者<input value={participant} onChange={e => setParticipant(e.target.value)} placeholder="Putin" className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">事件类型<input value={eventType} onChange={e => setEventType(e.target.value)} placeholder="Make statement" className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">CAMEO 类别<input value={category} onChange={e => setCategory(e.target.value)} placeholder="例如 18" className="mt-1 w-full border rounded px-2 py-1.5" /></label><div className="grid grid-cols-2 gap-2"><label className="block text-xs">强度 ≥<input type="number" value={intensityMin} onChange={e => setIntensityMin(e.target.value)} className="mt-1 w-full border rounded px-2 py-1.5" /></label><label className="block text-xs">强度 ≤<input type="number" value={intensityMax} onChange={e => setIntensityMax(e.target.value)} className="mt-1 w-full border rounded px-2 py-1.5" /></label></div><div className="border-t pt-3 space-y-1"><p className="text-xs text-gray-400">时间模式</p><button onClick={() => setMode('cumulative')} className={`w-full text-left text-xs px-2 py-1.5 rounded ${mode === 'cumulative' ? 'bg-black text-white' : 'hover:bg-gray-50'}`}>截至当前（累计）</button><button onClick={() => setMode('window')} className={`w-full text-left text-xs px-2 py-1.5 rounded ${mode === 'window' ? 'bg-black text-white' : 'hover:bg-gray-50'}`}>当日窗口</button></div><div className="border-t pt-3 space-y-1"><p className="text-xs text-gray-400">图谱视图</p>{[['ontology', '本体结构'], ['actors', 'Actor 关系投影'], ['evidence', '事件证据']].map(([key, label]) => <button key={key} onClick={() => setView(key as any)} className={`w-full text-left text-xs px-2 py-1.5 rounded ${view === key ? 'bg-gray-100 font-medium' : 'hover:bg-gray-50'}`}>{label}</button>)}</div></aside>
      <section className="bg-white border rounded-xl p-4 space-y-3"><div className="flex items-center justify-between"><p className="text-sm font-semibold">{view === 'ontology' ? '本体结构与实例' : view === 'actors' ? 'Actor 关系投影视图' : 'InteractionEvent 证据视图'}</p><span className="text-xs text-gray-400">{snapshot?.graph_backend || 'falkordb'} · {graphNodes.length} 个渲染节点</span></div>{snapshot?.available === false ? <div className="p-5 border border-red-200 bg-red-50 text-red-700 rounded">FalkorDB 未连接，不能把 SQLite 回退数据当作时序实例。</div> : <GraphCanvas nodes={graphNodes} edges={graphEdges} onSelect={selectNode} hideLabels={hideLabels} fitCanvas={fitCanvas} selectedId={selected?.id} />}<div className="border rounded-lg bg-gray-50 p-3 space-y-2"><div className="flex items-center justify-between"><p className="text-xs font-medium">时间轴 · {dates.length} 个日期</p><button onClick={() => setPlaying(value => !value)} className="border rounded px-2 py-1 bg-white text-xs flex items-center gap-1">{playing ? <Pause size={12} /> : <Play size={12} />}{playing ? '暂停' : '播放'}</button></div><input type="range" min="0" max={Math.max(0, dates.length - 1)} value={Math.max(0, dates.indexOf(at))} onChange={e => setAt(dates[Number(e.target.value)] || '')} className="w-full" disabled={!dates.length} /><div className="flex justify-between text-[10px] text-gray-500"><span>{fmt(dates[0])}</span><span>{fmt(at || dates[0])}</span><span>{fmt(dates.at(-1))}</span></div></div><div className="overflow-auto max-h-52 border rounded-lg"><table className="w-full text-xs"><thead className="bg-gray-50 sticky top-0"><tr><th className="text-left px-2 py-2">日期</th><th className="text-left px-2 py-2">事件</th><th className="text-left px-2 py-2">类别</th><th className="text-left px-2 py-2">强度</th></tr></thead><tbody>{(timeline?.events || []).slice(0, 80).map((event: any) => <tr key={event.id} className="border-t"><td className="px-2 py-1.5">{fmt(event.timestamp)}</td><td className="px-2 py-1.5 max-w-[300px] truncate">{event.label || event.id}</td><td className="px-2 py-1.5">{event.category || '—'}</td><td className="px-2 py-1.5">{fmt(event.value)}</td></tr>)}</tbody></table></div></section>
      <aside className="bg-white border rounded-xl p-4 space-y-4"><div className="flex items-center gap-2"><Eye size={15} /><p className="text-sm font-semibold">对象详情</p></div>{selected ? <><p className="text-xs break-all font-mono">{selected.id}</p><p className="text-xs"><b>类型</b> {selected.entity_type || selected.labels?.[0]}</p><div className="max-h-48 overflow-auto bg-gray-50 rounded p-2"><pre className="text-[10px] whitespace-pre-wrap">{JSON.stringify(selected.properties || {}, null, 2)}</pre></div><div className="border-t pt-3"><p className="text-xs font-medium mb-2">原始来源证据</p>{evidence?.evidence?.length ? <div className="space-y-2">{evidence.evidence.slice(0, 5).map((item: any) => <div className="text-[10px] border rounded p-2" key={item.id}><p>{item.source_file} · 行 {item.source_row}</p><p className="text-gray-500 mt-1">{item.evidence_text}</p></div>)}</div> : <p className="text-xs text-gray-400">该上下文节点暂无独立 EvidenceRef</p>}</div></> : <p className="text-xs text-gray-400">点击画布节点查看 Event ID、Story ID、Publisher 和原始行证据。</p>}</aside></div>
    <section className="bg-white border rounded-xl p-4 space-y-3"><div className="flex items-center gap-2"><GitCompare size={15} /><p className="text-sm font-semibold">两个日期窗口对比</p></div><div className="flex flex-wrap items-end gap-3"><label className="text-xs">从<input type="date" value={fromAt} onChange={e => setFromAt(e.target.value)} className="mt-1 block border rounded px-2 py-1.5" /></label><label className="text-xs">到<input type="date" value={toAt} onChange={e => setToAt(e.target.value)} className="mt-1 block border rounded px-2 py-1.5" /></label><button onClick={compare} className="bg-black text-white rounded px-3 py-1.5 text-xs">比较窗口</button></div>{diff && <div className="grid md:grid-cols-4 gap-2 text-xs"><div className="border border-green-200 bg-green-50 rounded p-2">窗口出现节点 {diff.added_nodes?.length || 0}</div><div className="border border-red-200 bg-red-50 rounded p-2">窗口未出现节点 {diff.removed_nodes?.length || 0}</div><div className="border border-green-200 bg-green-50 rounded p-2">出现关系 {diff.added_edges?.length || 0}</div><div className="border border-red-200 bg-red-50 rounded p-2">未出现关系 {diff.removed_edges?.length || 0}</div></div>}</section>
    <section className="bg-white border rounded-xl p-4"><p className="text-sm font-semibold mb-3">增长曲线（按日）</p><div className="h-32 flex items-end gap-1 border-b border-l px-2">{(growth?.points || []).map((point: any) => <div key={point.timestamp} title={`${point.timestamp}: ${point.cumulative_nodes}`} className="bg-blue-500/70 min-w-[8px] flex-1" style={{ height: `${Math.max(3, Math.min(100, Number(point.cumulative_nodes || point.observations || 0) / Math.max(1, Number((growth?.points || []).at(-1)?.cumulative_nodes || 1)) * 100))}%` }} />)}</div></section>
  </div></CanvasOptionsContext.Provider>
}
