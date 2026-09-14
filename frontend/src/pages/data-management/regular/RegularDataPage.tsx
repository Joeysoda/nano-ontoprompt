import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Database, FileSpreadsheet, Loader2, Play, RefreshCw, ScanSearch, ShieldCheck, Table2, TriangleAlert, UploadCloud, X } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiClient, apiClientV2 } from '@/api/client'

type Dataset = { id: string; name: string; kind: string; data_class?: string; privacy_level?: 'standard' | 'private'; latest_version_id?: string; rowcount?: number | null }
type Ontology = { id: string; name: string; domain?: string; data_class?: string; status?: string }
type Column = { name: string; type?: string; sample_values?: unknown[] }
type Run = { id: string; ontology_id?: string; dataset_id?: string; mode?: string; config?: { privacy_level?: 'standard' | 'private'; mode?: 'create' | 'append'; selection?: { fields?: string[] } }; status: string; progress?: { completed?: number; total?: number; stage?: string; pct?: number }; metrics?: Record<string, unknown>; error?: string; revision_id?: string }
type Suggestion = { kind?: string; source?: string; target?: string; target_relation?: string; confidence?: number; extractor?: string; [key: string]: unknown }

const STEPS = ['数据集', '内容选择', '处理配置', '本体映射', '确认构建']
const statusLabel: Record<string, string> = { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', waiting_for_model: '等待模型', cancelled: '已取消' }

function errorText(error: any) { return error?.response?.data?.detail?.message || error?.response?.data?.detail || error?.detail || error?.message || '请求失败' }

export default function RegularDataPage() {
  const navigate = useNavigate(); const [searchParams, setSearchParams] = useSearchParams(); const restoredRunId = searchParams.get('run'); const uploadRef = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState(0); const [datasets, setDatasets] = useState<Dataset[]>([]); const [ontologies, setOntologies] = useState<Ontology[]>([])
  const [datasetId, setDatasetId] = useState(''); const [ontologyId, setOntologyId] = useState(''); const [targetMode, setTargetMode] = useState<'create' | 'append'>('append'); const [newOntologyName, setNewOntologyName] = useState('C-MAPSS FD001 本体'); const [columns, setColumns] = useState<Column[]>([]); const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [fields, setFields] = useState<string[]>([]); const [rowLimit, setRowLimit] = useState(5000); const [dedupe, setDedupe] = useState(true); const [privacy, setPrivacy] = useState<'standard' | 'private'>('standard'); const [mappingKey, setMappingKey] = useState('')
  const [draftId, setDraftId] = useState(''); const [mappingTaskId, setMappingTaskId] = useState(''); const [mappingTask, setMappingTask] = useState<{ stage?: string; progress?: { pct?: number }; trace?: Array<{ stage?: string; detail?: string; status?: string }> } | null>(null); const [suggestions, setSuggestions] = useState<Suggestion[]>([]); const [confirmed, setConfirmed] = useState<number[]>([]); const [mappingReady, setMappingReady] = useState(false); const [run, setRun] = useState<Run | null>(null)
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [uploading, setUploading] = useState(false)
  const dataset = useMemo(() => datasets.find(item => item.id === datasetId), [datasets, datasetId])

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [ds, os] = await Promise.all([apiClientV2.get<Dataset[]>('/datasets'), apiClient.get<{ items: Ontology[] }>('/ontologies?page_size=100')])
      const list = Array.isArray(ds) ? ds.filter(item => item.data_class !== 'temporal' && item.data_class !== 'multimodal') : []
      // Empty historical drafts are not valid append targets.  A user can
      // only append to a published ontology of the same data class; otherwise
      // the first step starts in the clearer "新建本体" mode.
      const ontologyItems = (os?.items || []).filter(item => item.data_class === 'regular' && item.status === 'created')
      setDatasets(list); setOntologies(ontologyItems); setDatasetId(current => current || list[0]?.id || ''); setOntologyId(current => ontologyItems.some(item => item.id === current) ? current : '')
      setTargetMode(current => current === 'append' && !ontologyItems.length ? 'create' : current)
    } catch (err: any) { setError(errorText(err)) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!restoredRunId || run?.id === restoredRunId) return
    let cancelled = false
    apiClientV2.get<Run>(`/construction-runs/${restoredRunId}`).then(restored => {
      if (cancelled) return
      if (restored.mode !== 'regular') { setError('该链接指向的不是常规数据构建任务'); return }
      setRun(restored); if (restored.dataset_id) setDatasetId(restored.dataset_id)
      if (restored.config?.selection?.fields) setFields(restored.config.selection.fields)
      if (restored.config?.privacy_level) setPrivacy(restored.config.privacy_level)
      if (restored.config?.mode === 'append') { setTargetMode('append'); if (restored.ontology_id) setOntologyId(restored.ontology_id) }
      else { setTargetMode('create'); if (restored.ontology_id) apiClient.get<{ name?: string }>(`/ontologies/${restored.ontology_id}`).then(ontology => { if (!cancelled && ontology?.name) setNewOntologyName(ontology.name) }).catch(() => {}) }
      setStep(4)
    }).catch(reason => { if (!cancelled) setError(`无法恢复构建任务：${errorText(reason)}`) })
    return () => { cancelled = true }
  }, [restoredRunId, run?.id])

  useEffect(() => {
    if (!datasetId) { setColumns([]); setRows([]); return }
    let cancelled = false
    const fetchPreview = async () => {
      try {
        const [schema, versions] = await Promise.all([apiClientV2.get<{ columns: Column[] }>(`/datasets/${datasetId}/schema`), apiClientV2.get<any[]>(`/datasets/${datasetId}/versions`)]); const nextColumns = schema?.columns || []; if (cancelled) return
        setColumns(nextColumns); setFields(current => current.length ? current.filter(field => nextColumns.some(column => column.name === field)) : nextColumns.map(column => column.name))
        const version = Array.isArray(versions) ? versions[versions.length - 1] : null
        if (version) { const preview = await apiClientV2.get<{ rows?: Record<string, unknown>[] }>(`/datasets/${datasetId}/versions/${version.version_no}/preview?limit=20`); if (!cancelled) setRows(preview?.rows || (Array.isArray(preview) ? preview as unknown as Record<string, unknown>[] : [])) }
      } catch (err: any) { if (!cancelled) setError(errorText(err)) }
    }
    fetchPreview(); return () => { cancelled = true }
  }, [datasetId])

  useEffect(() => {
    if (!run || !['queued', 'running', 'waiting_for_model'].includes(run.status)) return
    const timer = window.setInterval(() => apiClientV2.get<Run>(`/construction-runs/${run.id}`).then(setRun).catch(() => {}), 1600)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  useEffect(() => {
    if (!mappingTaskId || mappingReady) return
    const timer = window.setInterval(() => apiClientV2.get<any>(`/mapping-tasks/${mappingTaskId}`).then(task => {
      setMappingTask(task)
      if (task.status === 'completed') {
        const next = Array.isArray(task.result?.suggestions) ? task.result.suggestions : []
        setSuggestions(next); setConfirmed(next.map((_: Suggestion, index: number) => index)); setMappingReady(next.length > 0); setMappingTaskId('')
      } else if (task.status === 'waiting_for_model' || task.status === 'failed' || task.status === 'cancelled') {
        setError(task.error || '映射任务未完成'); setMappingTaskId('')
      }
    }).catch(() => {}), 1200)
    return () => window.clearInterval(timer)
  }, [mappingTaskId, mappingReady])

  const upload = async (file: File) => {
    setUploading(true); setError('')
    try { const form = new FormData(); form.append('file', file); await apiClientV2.post('/datasets/upload', form); await load() } catch (err: any) { setError(errorText(err)) } finally { setUploading(false); if (uploadRef.current) uploadRef.current.value = '' }
  }
  const resetTargetDraft = () => { setDraftId(''); setMappingTaskId(''); setMappingTask(null); setMappingReady(false); setSuggestions([]); setConfirmed([]) }
  const switchTargetMode = (mode: 'create' | 'append') => {
    setTargetMode(mode); resetTargetDraft()
    if (mode === 'create') setOntologyId('')
    else setOntologyId(current => current || ontologies[0]?.id || '')
  }
  const ensureDraft = async () => {
    if (!datasetId) throw new Error('请先选择数据集')
    if (targetMode === 'append' && !ontologyId) throw new Error('请选择要追加的目标本体')
    if (targetMode === 'create' && !newOntologyName.trim()) throw new Error('请输入新本体名称')
    const selection = { fields, columns: fields, row_limit: Math.max(1, Math.min(rowLimit, 10000)), dedupe, mapping_key: mappingKey }
    const processing = { privacy_level: privacy, normalization: true, dedupe }
    const target = { target_mode: targetMode, ontology_id: targetMode === 'append' ? ontologyId : null, new_ontology_name: targetMode === 'create' ? newOntologyName.trim() : null, new_ontology_domain: '制造' }
    if (draftId) { const patched = await apiClientV2.patch<any>(`/construction/drafts/${draftId}`, { privacy_level: privacy, selection, processing_config: processing, ...target }); if (patched?.privacy_level && patched.privacy_level !== privacy) setPrivacy(patched.privacy_level); return draftId }
    const created = await apiClientV2.post<{ id: string; privacy_level?: 'standard' | 'private' }>('/construction/drafts', { dataset_id: datasetId, data_class: 'regular', privacy_level: privacy, selection, processing_config: processing, ...target }); if (created?.privacy_level && created.privacy_level !== privacy) setPrivacy(created.privacy_level); setDraftId(created.id); return created.id
  }
  const generateMapping = async () => {
    setBusy(true); setError('')
    try { const id = await ensureDraft(); const result = await apiClientV2.post<any>(`/construction/drafts/${id}/generate-mapping`); const next = Array.isArray(result?.mapping?.suggestions) ? result.mapping.suggestions : []; if (next.length) { setSuggestions(next); setConfirmed(next.map((_: Suggestion, index: number) => index)); setMappingReady(true) } else if (result?.mapping_task_id) { setMappingTaskId(result.mapping_task_id); setMappingTask({ stage: result?.mapping?.stage || '排队', progress: { pct: 0 }, trace: [] }); setMappingReady(false) } else if (result?.mapping?.status === 'waiting_for_model') setError(result.mapping.error || '标准数据等待 MiniMax M3') } catch (err: any) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const build = async () => {
    if (!mappingReady || confirmed.length === 0) { setError('请先确认本体映射'); return }
    setBusy(true); setError('')
    try { const id = await ensureDraft(); await apiClientV2.patch(`/construction/drafts/${id}`, { mapping: { suggestions, confirmed: confirmed.map(index => suggestions[index]) } }); const created = await apiClientV2.post<Run>(`/construction/drafts/${id}/build`, { mapping_confirmed: true, mode: targetMode }); setRun(created); setStep(4); setSearchParams({ run: created.id }, { replace: true }) } catch (err: any) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const next = async () => {
    if (step === 0 && !datasetId) { setError('请选择已有数据集或导入文件'); return }
    if (step === 0 && targetMode === 'append' && !ontologyId) { setError('请选择同为常规数据的目标本体'); return }
    if (step === 0 && targetMode === 'create' && !newOntologyName.trim()) { setError('请输入新本体名称'); return }
    if (step === 1 && fields.length === 0) { setError('至少选择一个字段'); return }
    if (step === 3 && (!mappingReady || confirmed.length === 0)) { setError('请先生成并确认映射建议'); return }
    setError('')
    if (step === 0 || step === 2 || step === 1) { setBusy(true); try { const id = await ensureDraft(); if (step === 2) { const preflight = await apiClientV2.post<any>(`/construction/drafts/${id}/m3-preflight`); if (preflight?.preflight?.allowed || privacy === 'private') { const result = await apiClientV2.post<any>(`/construction/drafts/${id}/generate-mapping`); const nextSuggestions = Array.isArray(result?.mapping?.suggestions) ? result.mapping.suggestions : []; if (nextSuggestions.length) { setSuggestions(nextSuggestions); setConfirmed(nextSuggestions.map((_: Suggestion, index: number) => index)); setMappingReady(true) } else if (result?.mapping_task_id) { setMappingTaskId(result.mapping_task_id); setMappingTask({ stage: result?.mapping?.stage || '排队', progress: { pct: 0 }, trace: [] }); setMappingReady(false) } else if (result?.mapping?.status === 'waiting_for_model') { setError(result.mapping.error || 'MiniMax M3 正在等待恢复'); return } } } } catch (err: any) { setError(errorText(err)); return } finally { setBusy(false) } }
    setStep(value => Math.min(4, value + 1))
  }
  const toggleField = (name: string) => setFields(current => current.includes(name) ? current.filter(item => item !== name) : [...current, name])

  if (loading) return <div className="wb-empty"><Loader2 size={18} className="animate-spin" />加载数据集</div>
  return <div className="wb-page max-w-[1320px]">
    <header className="wb-page-header"><div><div className="wb-eyebrow"><Table2 size={13} /> 数据构筑 / 常规数据</div><h1 className="wb-page-title">常规数据构建</h1><p className="wb-page-subtitle">CSV、Excel、JSON 与数据库表</p></div><div className="flex items-center gap-2"><button className="wb-button-secondary" onClick={load}><RefreshCw size={14} />刷新</button><button className="wb-button-secondary" onClick={() => uploadRef.current?.click()} disabled={uploading}><UploadCloud size={14} />导入文件</button><input ref={uploadRef} type="file" accept=".csv,.tsv,.xlsx,.xls,.json,.jsonl,.parquet" className="hidden" onChange={event => { const file = event.target.files?.[0]; if (file) upload(file) }} /></div></header>
    {error && <div className="wb-alert wb-alert-danger"><TriangleAlert size={16} /><span>{error}</span><button className="ml-auto" onClick={() => setError('')}><X size={15} /></button></div>}
    <div className="wb-stepper wb-surface">{STEPS.map((label, index) => <button key={label} className={`wb-step ${index === step ? 'wb-step-active' : index < step ? 'wb-step-done' : ''}`} onClick={() => index <= step && setStep(index)}><span className="wb-step-index">{index < step ? <Check size={13} /> : index + 1}</span>{label}</button>)}</div>

    {step === 0 && <section className="wb-surface p-5"><div className="flex items-start justify-between gap-4"><div><div className="wb-section-kicker">构建目标</div><h2 className="wb-section-title mt-1">新建或追加本体</h2></div><span className="wb-status">最终确认时创建修订</span></div><div className="mt-4 grid md:grid-cols-2 gap-3"><button type="button" className={`wb-radio-card ${targetMode === 'create' ? 'wb-choice-selected' : ''}`} onClick={() => switchTargetMode('create')}><Database size={16} /><div><strong>新建本体</strong><p>填写名称后选择数据来源</p></div></button><button type="button" className={`wb-radio-card ${targetMode === 'append' ? 'wb-choice-selected' : ''}`} onClick={() => switchTargetMode('append')}><Table2 size={16} /><div><strong>追加本体</strong><p>写入同类本体的新修订</p></div></button></div><div className="mt-3 max-w-xl">{targetMode === 'create' ? <label className="wb-label">本体名称<input className="wb-input mt-1" value={newOntologyName} onChange={event => { setNewOntologyName(event.target.value); resetTargetDraft() }} /></label> : <label className="wb-label">目标本体<select className="wb-input mt-1" value={ontologyId} onChange={event => { setOntologyId(event.target.value); resetTargetDraft() }}><option value="">请选择常规数据本体</option>{ontologies.map(item => <option key={item.id} value={item.id}>{item.name} · {item.domain || '通用'}</option>)}</select></label>}</div></section>}

    {step === 0 && <div className="grid xl:grid-cols-[1.25fr_.75fr] gap-4"><section className="wb-surface p-5 space-y-4"><div className="flex items-start justify-between"><div><div className="wb-section-kicker">01 / 数据集</div><h2 className="wb-section-title">选择或导入数据</h2></div><span className="wb-status">{datasets.length} 个可用数据集</span></div>{datasets.length ? <div className="grid md:grid-cols-2 gap-3">{datasets.map(item => <button type="button" key={item.id} onClick={() => { setDatasetId(item.id); resetTargetDraft() }} className={`wb-choice-card text-left ${datasetId === item.id ? 'wb-choice-selected' : ''}`}><div className="flex items-center justify-between"><span className="wb-icon-box"><FileSpreadsheet size={17} /></span><span className="wb-tag">{item.kind === 'structured' ? '结构化' : '半结构化'}</span></div><h3 className="mt-4 text-sm font-semibold">{item.name}</h3><p className="mt-1 text-xs text-gray-500">{item.rowcount == null ? '行数待探测' : `${item.rowcount} 行`} · {item.privacy_level || 'standard'}</p><p className="mt-3 text-[11px] text-gray-400 font-mono">{item.id.slice(0, 12)}</p></button>)}</div> : <div className="wb-empty"><Database size={19} />暂无常规数据集，请先导入文件</div>}</section><section className="wb-surface p-5"><div className="wb-section-kicker">输入范围</div><h2 className="wb-section-title mt-1">支持的来源</h2><div className="mt-4 space-y-3 text-sm text-gray-600"><div className="flex items-center gap-3"><Table2 size={16} className="text-blue-600" />CSV / TSV / Excel 多工作表</div><div className="flex items-center gap-3"><Database size={16} className="text-teal-600" />JSON 表格 / 数据库表</div><div className="flex items-center gap-3"><ShieldCheck size={16} className="text-emerald-600" />导入后再选择隐私级别</div></div><button className="wb-button-primary mt-5 w-full" onClick={() => navigate(`/data/pipelines/connections?return_to=${encodeURIComponent('/data/regular')}${draftId ? `&draft_id=${encodeURIComponent(draftId)}` : ''}`)}><ArrowRight size={14} />打开数据连接</button></section></div>}

    {step === 1 && <section className="wb-surface p-5 space-y-4"><div className="flex items-start justify-between"><div><div className="wb-section-kicker">02 / 内容选择</div><h2 className="wb-section-title">选择字段与样本范围</h2></div><span className="wb-status">已选 {fields.length} 列</span></div>{dataset ? <><div className="flex flex-wrap gap-2">{columns.map(column => <button type="button" key={column.name} onClick={() => toggleField(column.name)} className={`wb-filter-chip ${fields.includes(column.name) ? 'wb-filter-chip-active' : ''}`}><Check size={11} className={fields.includes(column.name) ? '' : 'invisible'} />{column.name}<span className="ml-1 text-[10px] opacity-60">{column.type || 'field'}</span></button>)}</div>{columns.length === 0 && <div className="wb-empty"><ScanSearch size={19} />尚未识别字段</div>}<div className="overflow-x-auto rounded-lg border border-gray-200"><table className="w-full min-w-[640px] text-xs"><thead className="bg-gray-50"><tr>{(rows[0] ? Object.keys(rows[0]) : fields).map(key => <th key={key} className={`px-3 py-2 text-left font-medium ${fields.includes(key) ? 'text-gray-800' : 'text-gray-400'}`}>{key}</th>)}</tr></thead><tbody className="divide-y">{rows.slice(0, 8).map((row, index) => <tr key={index}>{Object.entries(row).map(([key, value]) => <td key={key} className={`max-w-[180px] truncate px-3 py-2 ${fields.includes(key) ? 'text-gray-700' : 'text-gray-400'}`}>{String(value ?? '')}</td>)}</tr>)}</tbody></table>{rows.length === 0 && <div className="wb-empty rounded-none border-0">暂无预览行</div>}</div></> : <div className="wb-empty"><Database size={19} />请先选择数据集</div>}</section>}

    {step === 2 && <section className="grid xl:grid-cols-[1fr_.8fr] gap-4"><div className="wb-surface p-5 space-y-5"><div><div className="wb-section-kicker">03 / 处理配置</div><h2 className="wb-section-title">清洗、规范化与隐私</h2></div><div className="grid sm:grid-cols-2 gap-3"><button className={`wb-radio-card ${privacy === 'standard' ? 'wb-choice-selected' : ''}`} onClick={() => setPrivacy('standard')}><ShieldCheck size={16} /><div><strong>标准 standard</strong><p>确认范围后可发送 MiniMax M3</p></div></button><button className={`wb-radio-card ${privacy === 'private' ? 'wb-choice-selected' : ''}`} onClick={() => setPrivacy('private')}><ShieldCheck size={16} /><div><strong>私密 private</strong><p>不调用云模型，使用规则与人工</p></div></button></div><label className="wb-label">处理上限<input className="wb-input mt-1" type="number" min={1} max={10000} value={rowLimit} onChange={event => setRowLimit(Number(event.target.value) || 1)} /></label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={dedupe} onChange={event => setDedupe(event.target.checked)} />按关联键去重</label><label className="wb-label">关联键（可选）<select className="wb-input mt-1" value={mappingKey} onChange={event => setMappingKey(event.target.value)}><option value="">自动识别</option>{fields.map(field => <option key={field} value={field}>{field}</option>)}</select></label></div><div className="wb-surface p-5"><div className="wb-section-kicker">处理预览</div><h2 className="wb-section-title mt-1">{dataset?.name || '—'}</h2><div className="mt-4 wb-summary-grid"><div><span>字段</span><strong>{fields.length}</strong></div><div><span>预览行</span><strong>{rows.length}</strong></div><div><span>处理上限</span><strong>{rowLimit}</strong></div><div><span>隐私</span><strong>{privacy}</strong></div></div><p className="mt-4 text-xs text-gray-500">确定性画像、类型规范化与去重先执行；模型只接收确认后的结构化摘要。</p></div></section>}

    {step === 3 && <section className="wb-surface p-5 space-y-5"><div className="flex items-start justify-between gap-4"><div><div className="wb-section-kicker">04 / 本体映射</div><h2 className="wb-section-title">确认类、属性与关系</h2></div><button className="wb-button-primary" onClick={generateMapping} disabled={busy}><Play size={14} />{mappingReady ? '重新生成' : '重新请求映射'}</button></div>{mappingReady ? <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">{suggestions.map((item, index) => <label key={`${item.source}-${index}`} className={`wb-mapping-card ${confirmed.includes(index) ? 'wb-choice-selected' : ''}`}><input type="checkbox" checked={confirmed.includes(index)} onChange={() => setConfirmed(current => current.includes(index) ? current.filter(value => value !== index) : [...current, index])} /><div><strong>{String(item.target || item.kind || '映射')}</strong><p>{String(item.source || '已选字段')} {item.target_relation ? `→ ${item.target_relation}` : ''}</p><span>{item.extractor || '确定性处理'} · 置信度 {item.confidence == null ? '—' : Number(item.confidence).toFixed(2)}</span></div></label>)}</div> : <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-5 py-6"><div className="flex items-center gap-2 text-sm font-medium text-slate-700"><Loader2 size={18} className="animate-spin text-slate-500" />{mappingTaskId ? (mappingTask?.stage || '映射任务排队中') : '等待提交映射任务'}</div><div className="mt-3 h-1.5 overflow-hidden rounded bg-slate-200"><div className="h-full bg-slate-700 transition-all" style={{ width: `${mappingTask?.progress?.pct ?? (mappingTaskId ? 8 : 0)}%` }} /></div><div className="mt-3 space-y-1 text-xs text-slate-500">{(mappingTask?.trace || []).slice(-4).map((item, index) => <p key={`${item.stage}-${index}`}>{item.stage || '处理中'}：{item.detail || item.status || '已提交'}</p>)}</div></div>}</section>}

    {step === 4 && <section className="grid xl:grid-cols-[1fr_.8fr] gap-4"><div className="wb-surface p-5 space-y-4"><div><div className="wb-section-kicker">05 / 确认构建</div><h2 className="wb-section-title">启动后台构建</h2></div><div className="wb-summary-grid"><div><span>数据集</span><strong>{dataset?.name || '—'}</strong></div><div><span>字段</span><strong>{fields.length} 列</strong></div><div><span>隐私</span><strong>{privacy}</strong></div><div><span>目标本体</span><strong>{targetMode === 'create' ? newOntologyName : ontologies.find(item => item.id === ontologyId)?.name || '—'}</strong></div></div><div className="flex items-center justify-between border-t border-gray-100 pt-4"><span className="text-xs text-gray-500">通过结构校验后发布本体；M3 失败会保持“等待模型”。</span><button className="wb-button-primary" onClick={build} disabled={busy || Boolean(run && ['queued', 'running', 'waiting_for_model'].includes(run.status))}><Play size={14} />{busy ? '提交中' : '确认并构建'}</button></div>{run && <div className="wb-task-card"><div className="flex items-center gap-2">{run.status === 'completed' ? <CheckCircle2 size={16} className="text-emerald-600" /> : run.status === 'failed' ? <TriangleAlert size={16} className="text-red-600" /> : <Loader2 size={16} className="animate-spin text-gray-500" />}<strong>{statusLabel[run.status] || run.status}</strong><span className="text-xs text-gray-400">{run.progress?.stage || '等待处理'} · {run.progress?.completed ?? 0} / {run.progress?.total ?? 0}</span></div>{run.status !== 'failed' && run.status !== 'completed' && <div className="mt-3 h-1.5 overflow-hidden rounded bg-slate-200"><div className="h-full bg-slate-700 transition-all" style={{ width: `${run.progress?.pct ?? 8}%` }} /></div>}{run.error && <p className="mt-2 text-xs text-red-700">{run.error}</p>}{run.metrics && <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-gray-500"><span>记录 {String(run.metrics.rows_processed ?? '—')}</span><span>实体类型 {String(run.metrics.entity_type_count ?? '—')}</span><span>关系 {String(run.metrics.relation_count ?? '—')}</span><span>逻辑规则 {String(run.metrics.logic_rule_count ?? '—')}</span></div>}{run.status === 'completed' && <button className="wb-button-secondary mt-3 text-xs" onClick={() => navigate(`/ontologies/${run.ontology_id || ontologyId}?tab=data_model`)}>打开本体 <ArrowRight size={14} /></button>}</div>}</div><div className="wb-surface p-5"><div className="wb-section-kicker">结果视图</div><h2 className="wb-section-title mt-1">构建后可查看</h2><div className="mt-4 space-y-2">{[['本体关系', '实体类型、属性与关系'], ['实体记录', '真实记录与来源证据'], ['逻辑规则与质量审查', '规则和审查结果']].map(([title, note]) => <div key={title} className="flex items-center gap-3 rounded border border-gray-100 p-3"><CheckCircle2 size={16} className="text-gray-400" /><div><p className="text-sm font-medium">{title}</p><p className="text-xs text-gray-400">{note}</p></div></div>)}</div></div></section>}

    {step === 4 && <div className="wb-surface px-5 py-3 text-xs text-gray-500">构建方式：<strong className="text-gray-800">{targetMode === 'create' ? `新建 · ${newOntologyName}` : `追加 · ${ontologies.find(item => item.id === ontologyId)?.name || '—'}`}</strong>。确认后会创建新的不可变修订。</div>}
    <div className="wb-wizard-actions">{step > 0 ? <button className="wb-button-secondary" onClick={() => setStep(value => Math.max(0, value - 1))}><ArrowLeft size={14} />上一步</button> : <span />}{step < 4 && <button className="wb-button-primary" onClick={next} disabled={busy}>{step === 0 ? (datasetId ? '使用已有数据并继续' : '选择数据后继续') : '下一步'}<ArrowRight size={14} /></button>}</div>
  </div>
}
