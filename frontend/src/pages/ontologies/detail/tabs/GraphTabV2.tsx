import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { apiClientV2 } from '@/api/client'
import { Loader2, RefreshCw, Search } from 'lucide-react'
import OntologySearchBox from '@/components/search/OntologySearchBox'
import cytoscape from 'cytoscape'

type ViewMode = 'schema' | 'instances'
type QueryMode = 'natural' | 'cypher'

interface GraphNode {
  id: string
  labels: string[]
  properties: Record<string, unknown>
  entity_type?: string
  event_seq?: number | null
  event_time?: string | null
  node_kind?: string
}
interface GraphEdge {
  id: string
  source: string
  target: string
  type: string
  label?: string
  properties?: Record<string, unknown>
  valid_from?: string | null
  valid_to?: string | null
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  graph_backend?: string
  available?: boolean
  error?: string
  neo4j_available?: boolean
  fallback?: string
  total_instances?: number
  time_kind?: string
}

interface GraphQuality {
  quality_score: number
  isolated_node_count: number
  duplicate_display_name_count?: number
  orphan_relation_count: number
  node_count?: number
  edge_count?: number
}

interface CoverageData {
  available: boolean
  current: Array<{ equipment_id: string; valid_from?: string | null }>
  history: Array<{ equipment_id: string; valid_from?: string | null; valid_to?: string | null }>
}

interface IntegrationStatus {
  falkordb?: { available: boolean; host?: string; port?: number }
  chroma?: { available: boolean; entity_count: number }
}

const TYPE_COLORS: Record<string, string> = {
  Equipment: '#2563eb', SensorReading: '#059669', AnomalyEvent: '#dc2626',
  ProductionLine: '#7c3aed', Supplier: '#2563eb', Product: '#059669',
  Material: '#d97706', Organization: '#7c3aed', Order: '#dc2626',
  Building: '#1d4ed8', Zone: '#7c3aed', Point: '#0891b2', Observation: '#059669',
}
const FALLBACK_COLORS = ['#2563eb', '#059669', '#dc2626', '#7c3aed', '#d97706', '#0891b2', '#db2777']

function stableColor(value: string) {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length]
}

function nodeColor(node: GraphNode) {
  const type = node.entity_type || node.labels?.[0] || 'Entity'
  return TYPE_COLORS[type] || stableColor(type)
}

export default function GraphTabV2({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const { i18n } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [view, setView] = useState<ViewMode>('schema')
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [quality, setQuality] = useState<GraphQuality | null>(null)
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [entityType, setEntityType] = useState('')
  const [seqFrom, setSeqFrom] = useState('')
  const [seqTo, setSeqTo] = useState('')
  const [relationState, setRelationState] = useState<'all' | 'current'>('all')
  const [hideIsolated, setHideIsolated] = useState(false)
  const [selected, setSelected] = useState<{ kind: 'node' | 'edge'; value: GraphNode | GraphEdge } | null>(null)
  const [coverageLine, setCoverageLine] = useState('PL001')
  const [coverage, setCoverage] = useState<CoverageData | null>(null)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [queryMode, setQueryMode] = useState<QueryMode>('natural')
  const [query, setQuery] = useState('')
  const [queryResult, setQueryResult] = useState<unknown[]>([])
  const [queryLoading, setQueryLoading] = useState(false)

  const loadGraph = () => {
    setLoading(true)
    setError('')
    const params: Record<string, unknown> = { view, limit: 300, relation_state: relationState }
    if (entityType) params.entity_type = entityType
    if (seqFrom !== '') params.seq_from = Number(seqFrom)
    if (seqTo !== '') params.seq_to = Number(seqTo)
    Promise.all([
      apiClientV2.get(`/ontologies/${ontologyId}/graph`, { params }).catch((err: any) => ({
        nodes: [], edges: [], available: false, graph_backend: view === 'instances' ? 'falkordb' : 'sqlite-schema',
        error: err?.detail || err?.message || '图谱加载失败',
      })),
      apiClientV2.get(`/ontologies/${ontologyId}/graph/quality`, { params: { source: view === 'instances' ? 'instances' : 'schema' } }).catch(() => null),
    ]).then(([graph, q]: any[]) => {
      setGraphData(graph)
      setQuality(q)
      if (graph?.error) setError(graph.error)
    }).finally(() => setLoading(false))
  }

  useEffect(() => {
    loadGraph()
    apiClientV2.get(`/ontologies/${ontologyId}/integrations/status`)
      .then((status: any) => setIntegrations(status))
      .catch(() => setIntegrations(null))
  }, [ontologyId, view, entityType, seqFrom, seqTo, relationState])

  useEffect(() => {
    if (!graphData || !containerRef.current) return
    const allNodes = graphData.nodes || []
    const allEdges = graphData.edges || []
    const degree = new Map<string, number>()
    allNodes.forEach(n => degree.set(n.id, 0))
    allEdges.forEach(e => {
      degree.set(e.source, (degree.get(e.source) || 0) + 1)
      degree.set(e.target, (degree.get(e.target) || 0) + 1)
    })
    const visibleNodes = hideIsolated ? allNodes.filter(n => (degree.get(n.id) || 0) > 0) : allNodes
    const ids = new Set(visibleNodes.map(n => n.id))
    const visibleEdges = allEdges.filter(e => ids.has(e.source) && ids.has(e.target))
    const elements = [
      ...visibleNodes.map(n => {
        const label = String(n.properties?.name || n.properties?.reading_id || n.properties?.equipment_id || n.entity_type || n.id)
        return { data: { id: n.id, label: label.slice(0, 24), color: nodeColor(n), size: 66, degree: degree.get(n.id) || 0, entityId: String(n.properties?.source_id || n.properties?.id || n.id), raw: n } }
      }),
      ...visibleEdges.map(e => ({ data: { id: e.id, source: e.source, target: e.target, label: e.type, raw: e } })),
    ]
    cyRef.current?.destroy()
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        { selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', color: '#fff', 'font-size': '10px', 'font-weight': 'bold', 'text-valign': 'center', 'text-halign': 'center', width: 'data(size)', height: 'data(size)', 'text-wrap': 'wrap', 'text-max-width': '56px', 'text-outline-width': 2, 'text-outline-color': 'data(color)' } },
        { selector: 'edge', style: { label: 'data(label)', 'font-size': '9px', color: '#374151', 'line-color': '#9ca3af', 'target-arrow-color': '#9ca3af', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'text-background-color': '#fff', 'text-background-opacity': 0.9, 'text-background-padding': '2px' } },
        { selector: '.dimmed', style: { opacity: 0.18 } },
        { selector: '.highlighted', style: { 'line-color': '#1d4ed8', 'target-arrow-color': '#1d4ed8', 'background-color': '#1d4ed8', 'border-width': 3, 'border-color': '#fff' } },
      ],
      layout: { name: 'cose', animate: false, randomize: true, numIter: elements.length > 150 ? 400 : 1000, idealEdgeLength: 130, nodeRepulsion: 9000, componentSpacing: 100 } as any,
    })
    cy.on('tap', 'node', evt => {
      const node = evt.target
      cy.elements().removeClass('highlighted dimmed')
      node.addClass('highlighted')
      node.neighborhood().addClass('highlighted')
      cy.elements().not('.highlighted').addClass('dimmed')
      setSelected({ kind: 'node', value: node.data('raw') })
    })
    cy.on('tap', 'edge', evt => setSelected({ kind: 'edge', value: evt.target.data('raw') }))
    cy.on('tap', evt => {
      if (evt.target === cy) {
        cy.elements().removeClass('highlighted dimmed')
        setSelected(null)
      }
    })
    cy.on('dblclick', 'node', evt => {
      const node = evt.target.data('raw') as GraphNode
      const id = String(node.properties?.source_id || node.properties?.id || '')
      if (view === 'schema' && id) navigate(`/ontologies/${ontologyId}/entities/${id}`)
    })
    cyRef.current = cy
    return () => { cy.destroy(); cyRef.current = null }
  }, [graphData, hideIsolated, ontologyId, navigate, view, i18n.language])

  const entityTypes = useMemo(() => {
    const values = new Set<string>(['Equipment', 'SensorReading', 'AnomalyEvent', 'ProductionLine'])
    ;(graphData?.nodes || []).forEach(n => values.add(n.entity_type || n.labels?.[0] || 'Entity'))
    return Array.from(values).sort()
  }, [graphData])

  const handleQuery = async () => {
    if (!query.trim()) return
    setQueryLoading(true)
    try {
      const endpoint = queryMode === 'natural' ? `/ontologies/${ontologyId}/graph/ask` : `/ontologies/${ontologyId}/graph/cypher`
      const payload = queryMode === 'natural' ? { question: query } : { query }
      const result: any = await apiClientV2.post(endpoint, payload)
      setQueryResult(result.results || [])
    } catch (err: any) {
      setQueryResult([{ error: err?.detail || err?.message || '查询失败' }])
    } finally {
      setQueryLoading(false)
    }
  }

  const loadCoverage = async () => {
    setCoverageLoading(true)
    try {
      const data = await apiClientV2.get(`/ontologies/${ontologyId}/graph/temporal/coverage`, { params: { production_line_id: coverageLine } })
      setCoverage(data)
    } catch {
      setCoverage({ available: false, current: [], history: [] })
    } finally {
      setCoverageLoading(false)
    }
  }

  if (loading) return <div className="text-gray-400 text-sm py-8 text-center">加载中...</div>
  const nodes = graphData?.nodes || []
  const edges = graphData?.edges || []
  const degree = new Map<string, number>()
  nodes.forEach(n => degree.set(n.id, 0))
  edges.forEach(e => { degree.set(e.source, (degree.get(e.source) || 0) + 1); degree.set(e.target, (degree.get(e.target) || 0) + 1) })
  const isolatedCount = Array.from(degree.values()).filter(v => v === 0).length
  const labels = new Map<string, string>()
  nodes.forEach(n => (n.labels || [n.entity_type || 'Entity']).forEach(l => labels.set(l, TYPE_COLORS[l] || stableColor(l))))
  const graphOk = view === 'instances' ? Boolean(graphData?.available) : Boolean(graphData && !graphData.error)
  const sourceLabel = view === 'instances'
    ? (graphOk ? 'FalkorDB 实例图' : 'FalkorDB 未连接')
    : (graphData?.graph_backend === 'neo4j-legacy' ? 'Neo4j（兼容模式）' : 'Nano Schema 图')
  const selectedValue = selected?.value as any

  return (
    <div className="space-y-4">
      <div className="bg-white border rounded-xl p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex border rounded-lg overflow-hidden text-xs">
            <button onClick={() => { setView('schema'); setSelected(null) }} className={`px-3 py-1.5 ${view === 'schema' ? 'bg-black text-white' : 'bg-white text-gray-600'}`}>本体结构</button>
            <button onClick={() => { setView('instances'); setSelected(null) }} className={`px-3 py-1.5 ${view === 'instances' ? 'bg-black text-white' : 'bg-white text-gray-600'}`}>数据实例</button>
          </div>
          <button onClick={loadGraph} className="px-2 py-1.5 border rounded-lg text-gray-600 hover:bg-gray-50" title="刷新图谱"><RefreshCw size={14} /></button>
          <span className="text-xs text-gray-500">{view === 'instances' ? 'FalkorDB 时序实例' : 'Nano 自动生成的类型图'}</span>
        </div>
        {view === 'instances' && (
          <div className="flex flex-wrap gap-2 items-center text-xs">
            <label>实体类型
              <select value={entityType} onChange={e => setEntityType(e.target.value)} className="ml-1 border rounded px-2 py-1">
                <option value="">全部</option>{entityTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label>cycle 从 <input value={seqFrom} onChange={e => setSeqFrom(e.target.value)} type="number" min="0" className="ml-1 w-20 border rounded px-2 py-1" /></label>
            <label>到 <input value={seqTo} onChange={e => setSeqTo(e.target.value)} type="number" min="0" className="ml-1 w-20 border rounded px-2 py-1" /></label>
            <label>关系
              <select value={relationState} onChange={e => setRelationState(e.target.value as 'all' | 'current')} className="ml-1 border rounded px-2 py-1">
                <option value="all">全部历史</option><option value="current">当前有效</option>
              </select>
            </label>
          </div>
        )}
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-500 flex-wrap">
        <span className={`px-2 py-1 rounded-full border ${graphOk ? 'border-green-200 bg-green-50 text-green-700' : 'border-red-200 bg-red-50 text-red-700'}`}>{sourceLabel}</span>
        <span>节点 {nodes.length}{view === 'instances' && graphData?.total_instances ? ` / ${graphData.total_instances}` : ''}</span><span>边 {edges.length}</span>
        {quality && <><span>质量 {(quality.quality_score * 100).toFixed(0)}%</span><span>孤立 {quality.isolated_node_count}</span><span>孤儿关系 {quality.orphan_relation_count}</span></>}
        {integrations?.falkordb && <span className={`px-2 py-1 rounded-full border ${integrations.falkordb.available ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-200 bg-gray-50'}`}>FalkorDB {integrations.falkordb.available ? '已连接' : '未连接'}</span>}
        {integrations?.chroma && <span>Chroma {integrations.chroma.available ? integrations.chroma.entity_count : '未连接'}</span>}
        {isolatedCount > 0 && <button onClick={() => setHideIsolated(v => !v)} className="px-2 py-1 rounded border bg-white hover:bg-gray-50">{hideIsolated ? `显示 ${isolatedCount} 个孤立节点` : `隐藏 ${isolatedCount} 个孤立节点`}</button>}
      </div>

      {error && <div className="border border-red-200 bg-red-50 text-red-700 rounded-xl p-4 text-sm">{error}</div>}
      {labels.size > 0 && <div className="flex flex-wrap gap-2">{Array.from(labels.entries()).map(([label, color]) => <span key={label} className="flex items-center gap-1 text-xs bg-white border rounded-full px-2 py-0.5"><span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />{label}</span>)}</div>}

      {nodes.length > 0 ? <div ref={containerRef} data-testid="ontology-graph-canvas" className="border rounded-xl bg-white" style={{ height: 500 }} /> : <div className="border rounded-xl bg-gray-50 h-64 flex items-center justify-center"><p className="text-sm text-gray-400">{graphOk ? '暂无图谱数据' : '请先启动对应图数据库服务'}</p></div>}

      {selected && <div className="bg-white border rounded-xl p-4"><div className="flex items-center justify-between mb-2"><h3 className="text-sm font-semibold">{selected.kind === 'node' ? '节点详情' : '关系详情'}</h3><button onClick={() => setSelected(null)} className="text-xs text-gray-400">关闭</button></div><pre className="text-xs bg-gray-50 rounded p-3 overflow-auto max-h-56">{JSON.stringify(selectedValue, null, 2)}</pre></div>}

      {view === 'instances' && <div className="bg-white border rounded-xl p-4 space-y-3"><h3 className="text-sm font-semibold">生产线覆盖关系时序</h3><div className="flex gap-2"><input value={coverageLine} onChange={e => setCoverageLine(e.target.value)} placeholder="ProductionLine ID，例如 PL001" className="flex-1 border rounded-lg px-3 py-2 text-sm" /><button onClick={loadCoverage} disabled={coverageLoading} className="px-3 py-2 bg-black text-white rounded-lg text-sm">{coverageLoading ? <Loader2 size={14} className="animate-spin" /> : '查询历史'}</button></div>{coverage && (coverage.available ? <div className="grid md:grid-cols-2 gap-3 text-xs"><div className="border rounded-lg p-3"><p className="font-medium mb-2">当前有效</p>{coverage.current.length ? coverage.current.map((x, i) => <div key={i} className="py-1">{x.equipment_id} · {x.valid_from || '未记录'}</div>) : <p className="text-gray-400">无当前关系</p>}</div><div className="border rounded-lg p-3"><p className="font-medium mb-2">历史记录</p>{coverage.history.length ? coverage.history.map((x, i) => <div key={i} className="py-1">{x.equipment_id} · {x.valid_from || '—'} → {x.valid_to || '当前'}</div>) : <p className="text-gray-400">无历史关系</p>}</div></div> : <p className="text-red-600 text-xs">FalkorDB 未连接或该生产线不存在</p>)}</div>}

      {view === 'schema' && graphData?.neo4j_available && <div className="bg-white border rounded-xl p-4 space-y-3"><div className="flex items-center gap-2"><div className="flex border rounded overflow-hidden text-xs"><button onClick={() => { setQueryMode('natural'); setQueryResult([]) }} className={`px-3 py-1.5 ${queryMode === 'natural' ? 'bg-black text-white' : 'bg-white text-gray-500'}`}>自然语言</button><button onClick={() => { setQueryMode('cypher'); setQueryResult([]) }} className={`px-3 py-1.5 ${queryMode === 'cypher' ? 'bg-black text-white' : 'bg-white text-gray-500'}`}>Cypher</button></div><span className="text-xs text-gray-400">兼容模式查询</span></div><div className="flex gap-2"><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleQuery()} placeholder={queryMode === 'natural' ? '输入自然语言问题' : '输入只读 Cypher'} className="flex-1 border rounded-lg px-3 py-2 text-sm" /><button onClick={handleQuery} disabled={queryLoading} className="px-3 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-1">{queryLoading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}查询</button></div>{queryResult.length > 0 && <pre className="text-xs bg-gray-50 border rounded p-3 overflow-auto max-h-40">{JSON.stringify(queryResult, null, 2)}</pre>}</div>}

      {view === 'schema' && <div className="bg-white border rounded-xl p-4"><p className="text-xs font-medium text-gray-600 mb-3">语义搜索</p><OntologySearchBox ontologyId={ontologyId} /></div>}
    </div>
  )
}
