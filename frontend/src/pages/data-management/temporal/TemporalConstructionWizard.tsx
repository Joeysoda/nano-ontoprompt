import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, FileUp, Loader2, Play, RefreshCw, Table2, TriangleAlert } from 'lucide-react'
import { apiClient, apiClientV2 } from '@/api/client'

type Source = { id: string; name: string; installed: boolean; kind?: string; source?: string; dataset_id?: string; records?: number; participants?: number; categories?: number; date_from?: string; date_to?: string; columns?: string[]; supports?: string[]; manifest?: Record<string, any> }
type Ontology = { id: string; name: string; domain?: string }
const steps = ['选择数据', '筛选数据', '确认时间语义', '配置本体映射', '确认并执行']
const mapping = [
  ['Event ID', 'InteractionEvent.id', '事件唯一标识'], ['Event Date', 'event_time', 'Instant · day'],
  ['Source Name / Country', 'Actor', '源参与者'], ['Target Name / Country', 'Actor', '目标参与者'],
  ['Event Text', 'InteractionEvent.event_type', '事件文本'], ['CAMEO Code', 'EventCategory', '事件类别'],
  ['City / Province / Lat / Lon', 'Location', '事件地点'], ['Story ID / Publisher', 'EvidenceRef', '来源证据'],
]

function errorText(e: any) { return e?.detail?.message || e?.detail || e?.message || '请求失败' }

export default function TemporalConstructionWizard() {
  const navigate = useNavigate()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState(0)
  const [sources, setSources] = useState<Source[]>([])
  const [source, setSource] = useState<Source | null>(null)
  const [sourceId, setSourceId] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [ontologies, setOntologies] = useState<Ontology[]>([])
  const [ontologyId, setOntologyId] = useState('')
  const [newOntology, setNewOntology] = useState(true)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [timeKind, setTimeKind] = useState<'instant' | 'ordinal' | 'interval'>('instant')
  const [timeColumn, setTimeColumn] = useState('event_time')
  const [sequenceColumn, setSequenceColumn] = useState('event_seq')
  const [validFromColumn, setValidFromColumn] = useState('valid_from')
  const [validToColumn, setValidToColumn] = useState('valid_to')
  const [entityColumn, setEntityColumn] = useState('entity_id')
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [sourceList, ontologyResponse] = await Promise.all([
        apiClientV2.get<Source[]>('/temporal/sources'),
        apiClient.get<any>('/ontologies', { params: { page: 1, page_size: 100 } }),
      ])
      const listSources = Array.isArray(sourceList) ? sourceList : []
      setSources(listSources)
      const found = listSources.find(x => x.id === sourceId) || null
      setSource(found)
      const list = Array.isArray(ontologyResponse) ? ontologyResponse : ontologyResponse?.items || []
      setOntologies(list)
      if (found?.installed) setPreview(await apiClientV2.get(`/temporal/sources/${found.id}/preview`, { params: { offset: 0, limit: 25, ...filters } }))
      else setPreview(null)
    } catch (e: any) { setError(errorText(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const chooseSource = async (item: Source) => {
    if (!item.installed) return
    setSourceId(item.id); setSource(item); setError('')
    if (item.id === 'icews_2023_demo') {
      setTimeKind('instant'); setTimeColumn('Event Date'); setSequenceColumn(''); setValidFromColumn(''); setValidToColumn(''); setEntityColumn('Event ID')
    } else {
      setTimeColumn(item.columns?.find(column => /time|date|timestamp/i.test(column)) || 'event_time')
      setSequenceColumn(item.columns?.find(column => /cycle|seq|sequence/i.test(column)) || 'event_seq')
      setEntityColumn(item.columns?.find(column => /id|unit|engine|component/i.test(column)) || 'entity_id')
    }
    try { setPreview(await apiClientV2.get(`/temporal/sources/${item.id}/preview`, { params: { offset: 0, limit: 25, ...filters } })) }
    catch (e: any) { setError(errorText(e)) }
  }

  const uploadTemporalFile = async (file: File) => {
    setUploading(true); setError('')
    try {
      const form = new FormData(); form.append('file', file)
      const result = await apiClientV2.post<any>('/datasets/upload', form)
      const uploadedId = result?.id || result?.data?.id
      if (!uploadedId) throw new Error('上传未返回 Dataset ID')
      const nextSources = await apiClientV2.get<Source[]>('/temporal/sources')
      setSources(Array.isArray(nextSources) ? nextSources : [])
      const uploaded = (Array.isArray(nextSources) ? nextSources : []).find(item => item.dataset_id === uploadedId || item.id === `dataset:${uploadedId}`)
      if (uploaded) await chooseSource(uploaded)
    } catch (e: any) { setError(errorText(e)) }
    finally { setUploading(false); if (uploadRef.current) uploadRef.current.value = '' }
  }

  const installIcews = async () => {
    setBusy(true); setError('')
    try {
      await apiClientV2.post('/temporal/sources/icews_2023_demo/install')
      const nextSources = await apiClientV2.get<Source[]>('/temporal/sources')
      setSources(Array.isArray(nextSources) ? nextSources : [])
      const installed = (Array.isArray(nextSources) ? nextSources : []).find(item => item.id === 'icews_2023_demo')
      if (installed) await chooseSource(installed)
    } catch (e: any) { setError(errorText(e)) }
    finally { setBusy(false) }
  }

  const refreshPreview = async (nextFilters = filters) => {
    if (!source?.installed) return
    try { setPreview(await apiClientV2.get(`/temporal/sources/${source.id}/preview`, { params: { offset: 0, limit: 25, ...nextFilters } })) }
    catch (e: any) { setError(errorText(e)) }
  }
  const setFilter = (key: string, value: string) => { const next = { ...filters, [key]: value }; setFilters(next); if (step === 1) refreshPreview(next) }
  const shortcut = (scenario: string) => { const next = { ...filters, scenario }; setFilters(next); refreshPreview(next) }

  const ensureOntology = async () => {
    if (!newOntology && ontologyId) return ontologyId
    const ontologyName = 'ICEWS 2023 时序事件本体'
    let created: any
    try {
      created = await apiClient.post<any>('/ontologies', {
        name: ontologyName, domain: '事件分析',
        description: 'ICEWS 官方三日事件切片的 Instant 时序事件本体', build_mode: 'temporal_pipeline',
      })
    } catch (e: any) {
      // Creating the wizard twice should not leave duplicate ontologies. The
      // API reports a conflict for the same name; reuse that existing target.
      if (e?.response?.status !== 409) throw e
      const response = await apiClient.get<any>('/ontologies', { params: { page: 1, page_size: 100 } })
      const list = Array.isArray(response) ? response : response?.items || []
      const existing = list.find((item: Ontology) => item.name === ontologyName)
      if (!existing?.id) throw e
      return existing.id
    }
    const id = created.id || created.data?.id
    if (!id) throw new Error('本体创建未返回 ID')
    return id
  }
  const execute = async () => {
    setBusy(true); setError('')
    try {
      if (!source?.installed || !source.dataset_id) throw new Error('请先选择已安装的数据集')
      const id = await ensureOntology()
      const isIcews = source.id === 'icews_2023_demo'
      const run = await apiClientV2.post<any>(`/ontologies/${id}/temporal/runs`, {
        source_id: source.id, dataset_id: source.dataset_id, adapter: isIcews ? 'icews' : 'generic',
        time_kind: isIcews ? 'instant' : timeKind, time_precision: isIcews ? 'day' : 'source-defined',
        event_time_column: isIcews ? 'Event Date' : (timeKind === 'instant' ? timeColumn : null),
        sequence_column: isIcews ? null : (timeKind === 'ordinal' ? sequenceColumn : null),
        valid_from_column: isIcews ? null : (timeKind === 'interval' ? validFromColumn : null),
        valid_to_column: isIcews ? null : (timeKind === 'interval' ? validToColumn : null),
        entity_id_column: isIcews ? 'Event ID' : entityColumn,
        filters: isIcews ? filters : { max_records: filters.max_records || '' },
        field_mapping: isIcews ? Object.fromEntries(mapping.map(([a, b]) => [a, b])) : { entity: entityColumn, time: timeColumn, sequence: sequenceColumn, valid_from: validFromColumn, valid_to: validToColumn },
        sample_limit: Number(filters.max_records || (source.records || 10000)),
      })
      navigate(`/data/temporal/runs/${run.run_id || run.id}`)
    } catch (e: any) { setError(errorText(e)) }
    finally { setBusy(false) }
  }

  const canNext = useMemo(() => {
    if (step === 0) return Boolean(source?.installed)
    if (step === 1) return Boolean(preview)
    if (step === 3) return Boolean(source?.installed)
    return true
  }, [step, source, preview])
  const isIcewsSource = source?.id === 'icews_2023_demo'

  if (loading) return <div className="p-6 text-sm text-gray-400">加载时序构建向导...</div>
  return <div className="max-w-6xl space-y-5">
    <div className="flex items-start justify-between"><div><button onClick={() => navigate('/data/temporal')} className="text-xs text-gray-500 hover:text-black flex items-center gap-1 mb-3"><ArrowLeft size={13} />返回时序数据</button><h2 className="text-xl font-semibold">时序本体构建</h2><p className="text-sm text-gray-500 mt-1">像 Semantica 一样先筛选，再构建、可视化和调查；不会自动替你选择数据。</p></div><button onClick={load} className="p-2 border rounded-lg text-gray-500"><RefreshCw size={15} /></button></div>
    {error && <div className="border border-red-200 bg-red-50 text-red-700 rounded-lg px-4 py-3 text-sm flex gap-2"><TriangleAlert size={16} />{error}</div>}
    <div className="flex items-center gap-1 overflow-auto pb-1">{steps.map((label, i) => <div key={label} className="flex items-center min-w-max"><div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs ${i < step ? 'bg-green-600 text-white' : i === step ? 'bg-black text-white' : 'bg-gray-100 text-gray-500'}`}>{i < step ? <Check size={14} /> : i + 1}</div><span className={`text-xs ml-2 ${i === step ? 'font-medium text-black' : 'text-gray-400'}`}>{label}</span>{i < steps.length - 1 && <div className="w-8 h-px bg-gray-200 mx-3" />}</div>)}</div>
    {step === 0 && <section className="bg-white border rounded-xl p-5 space-y-5"><div><p className="text-xs text-gray-400">步骤 1 / 5</p><h3 className="font-semibold mt-1">选择数据</h3><p className="text-sm text-gray-500 mt-1">从官方 ICEWS、已有 Dataset 或上传 TSV/CSV/JSON 中主动选择；未选择前不会进入下一步。</p></div><div className="grid lg:grid-cols-2 gap-3">{sources.map(item => <button key={item.id} disabled={item.id !== 'icews_2023_demo' && !item.installed} onClick={() => item.id === 'icews_2023_demo' && !item.installed ? installIcews() : chooseSource(item)} className={`text-left border rounded-lg p-4 transition ${source?.id === item.id ? 'border-black ring-1 ring-black' : 'hover:border-gray-400'} ${!item.installed ? 'opacity-60' : ''}`}><div className="flex items-start justify-between gap-3"><div><p className="font-medium">{item.name}</p><p className="text-xs text-gray-500 mt-1">{item.id === 'icews_2023_demo' ? 'Harvard Dataverse · File ID 7070776 · Instant/day' : `已有 Dataset · ${(item.supports || []).join(' / ')}`}</p></div><span className={`text-xs rounded-full px-2 py-1 ${item.installed ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>{item.installed ? (source?.id === item.id ? '已选择' : '可选择') : (item.id === 'icews_2023_demo' ? '点击安装' : '未安装')}</span></div><div className="grid grid-cols-3 gap-2 text-xs mt-3"><span className="bg-gray-50 rounded p-2">记录 {item.records ?? '—'}</span><span className="bg-gray-50 rounded p-2">字段 {item.columns?.length ?? '—'}</span><span className="bg-gray-50 rounded p-2">时间 {item.id === 'icews_2023_demo' ? 'day' : '自定义'}</span></div></button>)}</div><div className="flex flex-wrap items-center gap-3 border-t pt-4"><input ref={uploadRef} type="file" accept=".tsv,.csv,.json" className="hidden" onChange={e => { const file = e.target.files?.[0]; if (file) uploadTemporalFile(file) }} /><button disabled={uploading} onClick={() => uploadRef.current?.click()} className="px-4 py-2 border rounded-lg text-sm flex items-center gap-2 disabled:opacity-40"><FileUp size={14} />{uploading ? '上传中...' : '上传 TSV / CSV / JSON'}</button><span className="text-xs text-gray-500">上传后自动注册 Dataset，并回到本向导继续配置。</span></div>{source && <div className="border rounded-lg bg-gray-50 p-3 text-xs text-gray-600">已选择：<b>{source.name}</b> · {source.records ?? '—'} 条记录 · {source.dataset_id}</div>}</section>}
    {step === 1 && <section className="bg-white border rounded-xl p-5 space-y-5"><div><p className="text-xs text-gray-400">步骤 2 / 5</p><h3 className="font-semibold mt-1">筛选数据</h3><p className="text-sm text-gray-500 mt-1">筛选在构建前执行，预计记录数会实时更新。</p></div>{isIcewsSource && <div className="flex flex-wrap gap-2"><button onClick={() => { setFilters({}); refreshPreview({}) }} className="px-3 py-1.5 border rounded-full text-xs">全部事件（3,155）</button><button onClick={() => shortcut('ru_ua')} className="px-3 py-1.5 border rounded-full text-xs">俄乌事件（527）</button><button onClick={() => shortcut('kr')} className="px-3 py-1.5 border rounded-full text-xs">朝韩事件（176）</button><button onClick={() => shortcut('negative')} className="px-3 py-1.5 border rounded-full text-xs">负向强度（1,074）</button></div>}<div className="grid md:grid-cols-4 gap-3 text-sm"><label>开始日期<input type="date" value={filters.date_from || ''} onChange={e => setFilter('date_from', e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>结束日期<input type="date" value={filters.date_to || ''} onChange={e => setFilter('date_to', e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>{isIcewsSource && <><label>国家（源/目标）<input value={filters.country || ''} onChange={e => setFilter('country', e.target.value)} placeholder="例如 Ukraine" className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>事件类型<input value={filters.event_type || ''} onChange={e => setFilter('event_type', e.target.value)} placeholder="例如 consult" className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>CAMEO Code<input value={filters.cameo_code || ''} onChange={e => setFilter('cameo_code', e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>最低强度<input type="number" value={filters.intensity_min || ''} onChange={e => setFilter('intensity_min', e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>最高强度<input type="number" value={filters.intensity_max || ''} onChange={e => setFilter('intensity_max', e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label></>}<label>最大记录数<input type="number" min="1" max={source?.records || 100000} value={filters.max_records || ''} onChange={e => setFilter('max_records', e.target.value)} placeholder={String(source?.records || 10000)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label></div><div className="flex items-center justify-between border rounded-lg bg-gray-50 px-4 py-3"><span className="text-sm">当前筛选预计 <b>{preview?.total_rows ?? '—'}</b> 条记录{isIcewsSource && <>，{preview?.summary?.participants ?? '—'} 名参与者</>}</span><button onClick={() => refreshPreview()} className="text-xs text-gray-600 flex items-center gap-1"><RefreshCw size={13} />刷新预览</button></div><div className="overflow-auto border rounded-lg max-h-72"><table className="w-full text-xs"><thead className="bg-gray-50 sticky top-0"><tr>{(preview?.columns || []).filter((c: string) => !c.startsWith('_')).slice(0, 12).map((c: string) => <th className="text-left px-3 py-2 whitespace-nowrap" key={c}>{c}</th>)}</tr></thead><tbody>{(preview?.rows || []).map((row: any, i: number) => <tr className="border-t" key={i}>{(preview?.columns || []).filter((c: string) => !c.startsWith('_')).slice(0, 12).map((c: string) => <td className="px-3 py-2 whitespace-nowrap max-w-[180px] truncate" key={c}>{String(row[c] ?? '—')}</td>)}</tr>)}</tbody></table></div></section>}
    {step === 2 && <section className="bg-white border rounded-xl p-5 space-y-5"><div><p className="text-xs text-gray-400">步骤 3 / 5</p><h3 className="font-semibold mt-1">确认时间语义</h3><p className="text-sm text-gray-500 mt-1">先明确列和语义，再允许构建任务读取数据；非法时间会进入问题清单。</p></div>{isIcewsSource ? <><div className="grid md:grid-cols-3 gap-3"><div className="border-2 border-black rounded-lg p-4"><p className="font-medium">Instant · 日期时间点</p><p className="text-xs text-gray-500 mt-2">ICEWS Event Date 只精确到天，保留原始日期，不伪造时分秒或时区。</p><span className="inline-block mt-3 text-xs bg-black text-white rounded px-2 py-1">已选择</span></div><div className="border rounded-lg p-4 opacity-45"><p className="font-medium">Interval · 有效区间</p><p className="text-xs text-gray-500 mt-2">不适用于只有单日 Event Date 的 ICEWS 切片。</p><span className="inline-block mt-3 text-xs border rounded px-2 py-1">禁用</span></div><div className="border rounded-lg p-4 opacity-45"><p className="font-medium">Ordinal · 顺序时间</p><p className="text-xs text-gray-500 mt-2">不把事件日期改写为序号；后续数据源可单独支持。</p><span className="inline-block mt-3 text-xs border rounded px-2 py-1">禁用</span></div></div><div className="bg-blue-50 text-blue-800 rounded-lg p-4 text-sm">时间列：<b>Event Date</b>　时间精度：<b>day</b>　时区：<b>未提供</b></div></> : <><div className="grid md:grid-cols-3 gap-3"><label className={`border rounded-lg p-4 ${timeKind === 'instant' ? 'border-black ring-1 ring-black' : ''}`}><input type="radio" className="mr-2" checked={timeKind === 'instant'} onChange={() => setTimeKind('instant')} />Instant · 时间点</label><label className={`border rounded-lg p-4 ${timeKind === 'ordinal' ? 'border-black ring-1 ring-black' : ''}`}><input type="radio" className="mr-2" checked={timeKind === 'ordinal'} onChange={() => setTimeKind('ordinal')} />Ordinal · 顺序值</label><label className={`border rounded-lg p-4 ${timeKind === 'interval' ? 'border-black ring-1 ring-black' : ''}`}><input type="radio" className="mr-2" checked={timeKind === 'interval'} onChange={() => setTimeKind('interval')} />Interval · 有效区间</label></div><div className="grid md:grid-cols-2 gap-3 text-sm"><label>实体 ID 列<input value={entityColumn} onChange={e => setEntityColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>{timeKind === 'instant' && <label>时间列<input value={timeColumn} onChange={e => setTimeColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>}{timeKind === 'ordinal' && <label>顺序列<input value={sequenceColumn} onChange={e => setSequenceColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>}{timeKind === 'interval' && <><label>开始列<input value={validFromColumn} onChange={e => setValidFromColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>结束列<input value={validToColumn} onChange={e => setValidToColumn(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2" /></label></>}</div><div className="bg-gray-50 text-gray-700 rounded-lg p-4 text-sm">当前数据集：<b>{source?.name}</b> · 时间配置由你确认，系统不会从错误值猜测日期。</div></>}</section>}
    {step === 3 && <section className="bg-white border rounded-xl p-5 space-y-5"><div><p className="text-xs text-gray-400">步骤 4 / 5</p><h3 className="font-semibold mt-1">配置本体映射</h3><p className="text-sm text-gray-500 mt-1">确定性映射决定最终写入；本版本不添加模型猜测边。</p></div>{isIcewsSource ? <><div className="grid lg:grid-cols-[1fr_280px] gap-5"><div className="border rounded-lg overflow-auto"><table className="w-full text-xs"><thead className="bg-gray-50"><tr><th className="text-left px-3 py-2">ICEWS 字段</th><th className="text-left px-3 py-2">本体结构</th><th className="text-left px-3 py-2">说明</th></tr></thead><tbody>{mapping.map(row => <tr className="border-t" key={row[0]}><td className="px-3 py-2 font-mono">{row[0]}</td><td className="px-3 py-2 font-medium">{row[1]}</td><td className="px-3 py-2 text-gray-500">{row[2]}</td></tr>)}</tbody></table></div><div className="border rounded-lg p-4 space-y-3"><div className="flex items-center gap-2 text-sm font-medium"><Table2 size={15} />一条真实事件预览</div>{preview?.rows?.[0] ? <><p className="text-xs text-gray-500">{preview.rows[0]['Event Date']} · {preview.rows[0]['Source Name']} → {preview.rows[0]['Target Name']}</p><div className="text-xs space-y-1"><p><b>InteractionEvent</b> {preview.rows[0]['Event ID']}</p><p><b>EventCategory</b> {preview.rows[0]['CAMEO Code']}</p><p><b>Location</b> {preview.rows[0]['City'] || '未提供城市'}</p><p><b>EvidenceRef</b> Story {preview.rows[0]['Story ID']}</p></div></> : <p className="text-xs text-gray-400">暂无预览</p>}</div></div><div className="border rounded-lg p-4"><p className="text-sm font-medium mb-2">预计 Schema</p><div className="flex flex-wrap gap-2">{['Actor', 'InteractionEvent', 'EventCategory', 'Country', 'Location'].map(x => <span className="px-2 py-1 rounded-full bg-blue-50 text-blue-700 text-xs" key={x}>{x}</span>)}</div><p className="text-xs text-gray-500 mt-3">关系：INITIATED · TARGETED · CLASSIFIED_AS · ASSOCIATED_WITH · OCCURRED_IN</p></div></> : <div className="grid lg:grid-cols-[1fr_280px] gap-5"><div className="border rounded-lg overflow-auto"><table className="w-full text-xs"><thead className="bg-gray-50"><tr><th className="text-left px-3 py-2">数据列</th><th className="text-left px-3 py-2">映射用途</th><th className="text-left px-3 py-2">状态</th></tr></thead><tbody>{(preview?.columns || []).filter((column: string) => !column.startsWith('_')).map((column: string) => <tr className="border-t" key={column}><td className="px-3 py-2 font-mono">{column}</td><td className="px-3 py-2">{column === entityColumn ? 'Equipment ID' : column === timeColumn ? '时间属性' : 'Observation 属性'}</td><td className="px-3 py-2 text-green-700">确定性规则</td></tr>)}</tbody></table></div><div className="border rounded-lg p-4 space-y-3"><div className="flex items-center gap-2 text-sm font-medium"><Table2 size={15} />第一条记录预览</div>{preview?.rows?.[0] ? <pre className="text-xs whitespace-pre-wrap max-h-56 overflow-auto">{JSON.stringify(preview.rows[0], null, 2)}</pre> : <p className="text-xs text-gray-400">暂无预览</p>}</div></div>}</section>}
    {step === 4 && <section className="bg-white border rounded-xl p-5 space-y-5"><div><p className="text-xs text-gray-400">步骤 5 / 5</p><h3 className="font-semibold mt-1">确认并执行</h3></div><div className="grid md:grid-cols-2 gap-4"><div className="border rounded-lg p-4 space-y-2 text-sm"><p className="font-medium">本次构建配置</p><p>数据源：{source?.name || '—'}</p><p>筛选后记录：<b>{preview?.total_rows ?? '—'}</b></p><p>时间：{isIcewsSource ? 'Instant · day（Event Date）' : `${timeKind} · ${timeKind === 'instant' ? timeColumn : timeKind === 'ordinal' ? sequenceColumn : `${validFromColumn} → ${validToColumn}`}`}</p><p>写入：FalkorDB 独立本体图</p></div><div className="border rounded-lg p-4 space-y-3 text-sm"><p className="font-medium">目标本体</p><label className="flex items-center gap-2"><input type="radio" checked={newOntology} onChange={() => setNewOntology(true)} />创建或复用“ICEWS 2023 时序事件本体”</label><label className="flex items-center gap-2"><input type="radio" checked={!newOntology} onChange={() => setNewOntology(false)} />选择已有本体</label>{!newOntology && <select value={ontologyId} onChange={e => setOntologyId(e.target.value)} className="w-full border rounded-lg px-3 py-2"><option value="">请选择</option>{ontologies.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select>}</div></div><div className="bg-gray-50 rounded-lg p-4 text-xs text-gray-600 space-y-1"><p>执行阶段：读取数据 → 校验时间与关键列 → 稳定化实体 → 写入 FalkorDB → 保存原始行证据。</p><p>相同筛选和映射配置会复用成功 Run；重复导入不会增加节点和边。</p></div><button disabled={busy || (!newOntology && !ontologyId)} onClick={execute} className="px-5 py-2.5 bg-black text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-40">{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}创建时序构建任务</button></section>}
    <div className="flex items-center justify-between"><button onClick={() => step > 0 ? setStep(step - 1) : navigate('/data/temporal')} className="px-4 py-2 border rounded-lg text-sm">{step === 0 ? '取消' : '上一步'}</button>{step < 4 && <button disabled={!canNext} onClick={() => setStep(step + 1)} className="px-4 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2 disabled:opacity-35">下一步 <ArrowRight size={14} /></button>}</div>
  </div>
}
