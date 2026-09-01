import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, FileImage, FileText, Loader2, Play, RefreshCw, UploadCloud, TriangleAlert } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { apiClient, apiClientV2 } from '@/api/client'
import { constructionApi } from '@/api/construction'

type Source = { id: string; name: string; source?: string; media_count: number; media: Array<{ id: string; media_type: string; storage_uri: string; ocr_status: string }> }
type Ontology = { id: string; name: string; domain?: string }
type Run = { id: string; status: string; model_name?: string; progress?: Record<string, number>; metrics?: Record<string, unknown>; error?: string }

const statusLabel: Record<string, string> = { queued: '排队中', running: '提取中', completed: '已完成', failed: '失败' }

export default function MultimodalDataPage() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [ontologies, setOntologies] = useState<Ontology[]>([])
  const [sourceId, setSourceId] = useState('')
  const [ontologyId, setOntologyId] = useState('')
  const [modelStatus, setModelStatus] = useState<any>(null)
  const [file, setFile] = useState<File | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [fragments, setFragments] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const source = useMemo(() => sources.find(x => x.id === sourceId), [sources, sourceId])

  const load = async () => {
    setError('')
    try {
      const [sourceResult, ontologyResult, status] = await Promise.all([
        apiClientV2.get<{ sources: Source[] }>('/multimodal/sources'),
        apiClient.get<{ items: Ontology[] }>('/ontologies?page_size=100'),
        apiClientV2.get('/multimodal/status'),
      ])
      const nextSources = sourceResult?.sources || []
      setSources(nextSources)
      setSourceId(current => current || nextSources[0]?.id || '')
      setOntologies(ontologyResult?.items || [])
      setOntologyId(current => current || ontologyResult?.items?.[0]?.id || '')
      setModelStatus(status)
    } catch (err: any) {
      setError(err?.detail || err?.message || '多模态数据加载失败')
    }
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    const timer = window.setInterval(() => constructionApi.getRun(run.id).then(setRun).catch(() => {}), 1500)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  const upload = async () => {
    if (!file) return
    setBusy(true); setError('')
    try {
      const form = new FormData(); form.append('file', file)
      const result = await apiClientV2.post<any>('/datasets/upload', form)
      await load()
      setSourceId(result.id)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (err: any) {
      setError(err?.detail || err?.message || '媒体上传失败')
    } finally { setBusy(false) }
  }

  const createRun = async () => {
    if (!sourceId || sourceId === 'mvtec-ad2-builtin') { setError('MVTec 官方样例尚未安装，请先运行 import_mvtec_ad2.py'); return }
    if (!ontologyId) { setError('请选择目标本体'); return }
    setBusy(true); setError(''); setFragments([])
    try {
      const created = await apiClientV2.post<Run>('/multimodal/runs', { dataset_id: sourceId, ontology_id: ontologyId, model_id: modelStatus?.model_id, sample_limit: 32 })
      setRun(created)
    } catch (err: any) { setError(err?.detail?.message || err?.detail || err?.message || '创建多模态构建任务失败') }
    finally { setBusy(false) }
  }

  const loadFragments = async (mediaId: string) => {
    try {
      const result = await apiClientV2.get<any>(`/multimodal/fragments/${mediaId}`)
      setFragments(result?.fragments || [])
    } catch (err: any) { setError(err?.detail || err?.message || '证据加载失败') }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-start justify-between gap-4">
        <div><h2 className="text-xl font-semibold">多模态数据构建</h2><p className="text-sm text-gray-500 mt-1">图片、文档和视频先登记来源，再由 MiniMax M3 提取可追溯证据；没有证据不会猜测业务关系。</p></div>
        <button onClick={load} className="p-2 border rounded-lg text-gray-500 hover:text-black" title="刷新"><RefreshCw size={15} /></button>
      </div>
      <div className="grid md:grid-cols-4 gap-3">
        {[['选择媒体', UploadCloud, 'MVTec 样例或上传原文件'], ['提取内容', FileText, 'MiniMax M3 返回文本/标签'], ['关联证据', FileImage, '保存媒体定位和模型信息'], ['查看图谱', ArrowRight, '只写入可追溯的实例边']].map(([title, Icon, text]) => { const C = Icon as typeof FileText; return <div key={title as string} className="bg-white border rounded-xl p-4"><C size={17} className="text-gray-500" /><p className="font-medium text-sm mt-3">{title as string}</p><p className="text-xs text-gray-500 mt-1">{text as string}</p></div> })}
      </div>
      {error && <div className="border border-red-200 bg-red-50 text-red-700 rounded-lg p-3 text-sm">{error}</div>}

      <section className="bg-white border rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between"><h3 className="font-medium">1. 选择或上传媒体</h3><span className={`text-xs rounded-full border px-2 py-1 ${modelStatus?.available ? 'text-green-700 bg-green-50' : 'text-amber-700 bg-amber-50'}`}>MiniMax M3：{modelStatus?.available ? '可用' : '未配置'}</span></div>
        <label className="text-sm block">数据源<select value={sourceId} onChange={e => setSourceId(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2"><option value="">请选择数据源</option>{sources.map(item => <option key={item.id} value={item.id}>{item.name} · {item.media_count} 个媒体</option>)}</select></label>
        {source?.media?.length ? <div className="border rounded-lg divide-y">{source.media.slice(0, 32).map(item => <div key={item.id} className="flex items-center justify-between px-3 py-2 text-xs"><span className="truncate">{item.storage_uri.split('/').pop()}</span><span className="text-gray-400">{item.media_type} · {item.ocr_status}<button onClick={() => loadFragments(item.id)} className="ml-3 text-blue-600 hover:underline">查看证据</button></span></div>)}</div> : <p className="text-xs text-gray-500 bg-gray-50 rounded-lg p-3">尚未安装 MVTec 样例时，点击“上传文件”导入 PNG/JPG/PDF/DOCX/MP4/MOV。</p>}
        <input ref={inputRef} type="file" accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.mp4,.mov" className="hidden" onChange={e => setFile(e.target.files?.[0] || null)} />
        <div className="flex items-center gap-3"><button onClick={() => inputRef.current?.click()} className="px-3 py-2 border rounded-lg text-sm inline-flex items-center gap-2"><UploadCloud size={15} />选择文件</button>{file && <span className="text-xs text-gray-600 truncate">{file.name}</span>}<button onClick={upload} disabled={!file || busy} className="px-3 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40">上传并登记</button></div>
      </section>

      <section className="bg-white border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2"><span className="w-6 h-6 rounded-full bg-black text-white text-xs inline-flex items-center justify-center">2</span><h3 className="font-medium">选择目标本体并运行 MiniMax M3</h3></div>
        <label className="text-sm block">目标本体<select value={ontologyId} onChange={e => setOntologyId(e.target.value)} className="mt-1 w-full border rounded-lg px-3 py-2"><option value="">请选择本体</option>{ontologies.map(item => <option key={item.id} value={item.id}>{item.name} · {item.domain || '通用'}</option>)}</select></label>
        <div className="flex items-center gap-3"><button onClick={createRun} disabled={busy || !sourceId || !ontologyId || Boolean(run && ['queued', 'running'].includes(run.status))} className="px-4 py-2 bg-black text-white rounded-lg text-sm inline-flex items-center gap-2 disabled:opacity-40">{busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}创建多模态构建任务</button>{run && <span className="text-xs text-gray-500">Run {run.id.slice(0, 8)} · {statusLabel[run.status] || run.status}</span>}</div>
        {run && <div className="border rounded-lg p-4 text-sm">{run.status === 'completed' ? <CheckCircle2 size={16} className="inline text-green-600 mr-2" /> : run.status === 'failed' ? <TriangleAlert size={16} className="inline text-red-600 mr-2" /> : <Loader2 size={16} className="inline animate-spin mr-2" />}<span>{statusLabel[run.status] || run.status}</span><span className="text-gray-400 ml-3">{run.progress?.completed ?? 0} / {run.progress?.total ?? 0}</span>{run.error && <p className="text-red-600 text-xs mt-2">{run.error}</p>}{run.metrics && <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-3 text-xs"><span>媒体 {String(run.metrics.media_processed ?? '—')}</span><span>成功 {String(run.metrics.successful ?? '—')}</span><span>失败 {String(run.metrics.failed ?? '—')}</span><span>节点 {String(run.metrics.nodes_written ?? '—')}</span><span>边 {String(run.metrics.edges_written ?? '—')}</span></div>}{run.status === 'completed' && <button onClick={() => navigate(`/ontologies/${ontologyId}?tab=graph`)} className="mt-3 px-3 py-1.5 bg-gray-900 text-white rounded text-xs">打开实例图</button>}</div>}
      </section>

      {fragments.length > 0 && <section className="bg-white border rounded-xl p-5 space-y-3"><h3 className="font-medium">3. 来源证据（MiniMax M3）</h3>{fragments.map(fragment => <div key={fragment.id} className="border rounded-lg p-3"><div className="flex justify-between text-xs text-gray-500"><span>{fragment.extractor} · {fragment.status}</span><span>{fragment.locator?.filename || '媒体定位'}</span></div><pre className="text-xs whitespace-pre-wrap mt-2 max-h-64 overflow-auto">{fragment.content || fragment.error || '无提取内容'}</pre></div>)}</section>}
      <div className="border rounded-xl bg-gray-50 p-4 text-xs text-gray-600 leading-5"><strong>安全边界：</strong>MiniMax M3 只生成带媒体定位的描述；官方标签、文件元数据和明确设备 ID 才能创建业务关系。视频当前只登记原文件，不宣称视频语义理解；模型自报置信度也不等于校准准确率。</div>
    </div>
  )
}
