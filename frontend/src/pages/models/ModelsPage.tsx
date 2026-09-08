import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { modelApi } from '@/api/ontologies'
import { apiClientV2 } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog'
import type { ModelConfig } from '@/types/ontology'
import { Trash2, TestTube2, Plus, Pencil, X, Loader2, Server, CheckCircle2, CircleAlert, Copy } from 'lucide-react'

const CONFIG_TYPES = [
  { value: 'llm', label: 'LLM配置' },
  { value: 'ocr', label: 'OCR配置' },
  { value: 'other', label: '其他配置' },
]

const PROVIDERS: Record<string, Array<{ value: string; label: string }>> = {
  llm: [
    { value: 'openai', label: 'OpenAI' },
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'compatible', label: 'OpenAI-Compatible' },
  ],
  ocr: [
    { value: 'easyocr', label: 'EasyOCR' },
    { value: 'paddleocr', label: 'PaddleOCR' },
    { value: 'tesseract', label: 'Tesseract' },
    { value: 'external_api', label: 'External OCR API' },
  ],
  other: [
    { value: 'custom', label: 'Custom' },
    { value: 'local_service', label: 'Local Service' },
    { value: 'http_api', label: 'HTTP API' },
  ],
}

const USAGE_TAGS = ['VLM提取', '结构化提取', '宽表分析', 'Ontology Mapping', 'NL-to-Cypher', 'OCR文字提取']

function modelList(text?: string) {
  return text ? text.split('\n').map((s: string) => s.trim()).filter(Boolean) : []
}

function parseOptions(text?: string) {
  if (!text?.trim()) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {}
  }
}

function buildPayload(data: any, usageTags: string[]) {
  const options = {
    ...parseOptions(data.options_json),
    usage_tags: usageTags,
    ...(data.config_type === 'ocr' ? {
      enabled: data.ocr_enabled === 'true',
      lang: data.ocr_lang || 'ch',
      device: data.ocr_device || 'cpu',
    } : {}),
  }
  return {
    name: data.name,
    config_type: data.config_type || 'llm',
    provider: data.provider,
    api_key: data.api_key,
    api_base: data.api_base,
    models: modelList(data.models_str),
    options,
  }
}

function typeLabel(type?: string) {
  return CONFIG_TYPES.find(t => t.value === (type || 'llm'))?.label || 'LLM配置'
}

export default function ModelsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfig | null>(null)
  const [testResult, setTestResult] = useState<Record<string, string>>({})
  const [formTags, setFormTags] = useState<string[]>([])
  const { register, handleSubmit, reset, watch, setValue: setCreateValue } = useForm<any>({
    defaultValues: { config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' },
  })

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['models'], queryFn: () => modelApi.list() as any,
  })
  const localProbe = useQuery({ queryKey: ['local-model-probe'], queryFn: () => modelApi.localProbe() as any, refetchInterval: 15000 })
  const routeStatus = useQuery({ queryKey: ['model-route-status'], queryFn: () => modelApi.routeStatus() as any, refetchInterval: 30000 })
  const invocationQuery = useQuery({ queryKey: ['model-invocations'], queryFn: () => apiClientV2.get<{ items?: any[] }>('/model-invocations?limit=20') as any, refetchInterval: 15000 })
  const [expandedInvocation, setExpandedInvocation] = useState<string | null>(null)
  const visibleModels = useMemo(() => {
    const all = models as ModelConfig[]
    // Legacy migrations left several compatible qwen slots pointing at
    // host.docker.internal.  Keep them in storage for history, but show the
    // explicit local slot as the one users can operate.
    return all.filter(item => {
      const isQwen = (item.models || []).some(name => String(name).toLowerCase() === 'qwen3.5:0.8b')
      const provider = String(item.provider || '').toLowerCase()
      return !isQwen || provider === 'ollama' || provider === 'local'
    })
  }, [models])

  const [createError, setCreateError] = useState('')
  const createMut = useMutation({
    mutationFn: (data: any) => modelApi.create(buildPayload(data, formTags)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setShowCreate(false); reset(); setFormTags([]); setCreateError('') },
    onError: (err: any) => setCreateError(err?.response?.data?.detail || err?.detail || err?.message || '保存失败'),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => modelApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setDeleteTarget(null) },
  })

  const testMut = useMutation({
    mutationFn: (id: string) => modelApi.test(id),
    onSuccess: (res: any, id) => {
      const data = res?.data || res
      setTestResult(prev => ({ ...prev, [id]: data?.ok === false ? `未启用：${data.response || ''}` : '连接成功' }))
    },
    onError: (err: any, id) => setTestResult(prev => ({ ...prev, [id]: `连接失败：${err?.detail || '请检查服务地址'}` })),
  })

  // ── 编辑 ──
  const [editTarget, setEditTarget] = useState<ModelConfig | null>(null)
  const [editTags, setEditTags] = useState<string[]>([])
  const { register: regEdit, handleSubmit: handleEditSubmit, setValue, watch: watchEdit } = useForm<any>()

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => modelApi.update(id, buildPayload(data, editTags)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setEditTarget(null); setEditTags([]) },
  })

  const deleteInvocation = async (id: string) => {
    if (!window.confirm('确认删除这条模型调用日志？删除后不可恢复。')) return
    try {
      await apiClientV2.delete(`/model-invocations/${id}?confirm=true`)
      qc.invalidateQueries({ queryKey: ['model-invocations'] })
    } catch (err: any) {
      window.alert(err?.detail || err?.message || '删除失败')
    }
  }

  const openEdit = (m: ModelConfig) => {
    const options = m.options || {}
    setEditTarget(m); setEditTags((options.usage_tags as string[]) || [])
    setValue('name', m.name); setValue('config_type', m.config_type || 'llm'); setValue('provider', m.provider)
    setValue('api_base', m.api_base || '')
    setValue('models_str', (m.models || []).join('\n'))
    setValue('ocr_enabled', options.enabled ? 'true' : 'false')
    setValue('ocr_lang', String(options.lang || 'ch'))
    setValue('ocr_device', String(options.device || 'cpu'))
    setValue('options_json', JSON.stringify(
      Object.fromEntries(Object.entries(options).filter(([k]) => !['usage_tags', 'lang', 'device'].includes(k))),
      null,
      2,
    ))
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div><p className="wb-eyebrow">模型与审查</p><h1 className="wb-page-title mt-2">模型配置</h1><p className="wb-page-subtitle">云端构建、本地审查和能力检测</p></div>
        <button onClick={() => { setShowCreate(true); reset({ config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' }); setFormTags([]) }}
          className="wb-button-primary">
          <Plus size={14} /> {t('model.create')}
        </button>
      </div>

      <section className="wb-surface p-5">
        <div className="flex items-start justify-between gap-4"><div className="flex items-start gap-3"><span className="wb-icon-box"><Server size={17} /></span><div><h2 className="text-base font-semibold">本地审查槽 · qwen3.5:0.8b</h2><p className="mt-1 text-xs text-gray-500">Ollama · 127.0.0.1:11434 · audit / build / vision</p></div></div><button onClick={() => localProbe.refetch()} className="wb-button-secondary text-xs">重新探测</button></div>
        <div className="mt-4 grid md:grid-cols-3 gap-3"><div className="rounded-lg bg-gray-50 p-3"><p className="text-[11px] text-gray-500">模型槽</p><p className="mt-2 flex items-center gap-1.5 text-sm font-medium">{localProbe.data?.configured ? <CheckCircle2 size={14} className="text-emerald-600" /> : <CircleAlert size={14} className="text-amber-600" />}{localProbe.data?.configured ? '已配置' : '未配置'}</p></div><div className="rounded-lg bg-gray-50 p-3"><p className="text-[11px] text-gray-500">Ollama 服务</p><p className={`mt-2 text-sm font-medium ${localProbe.data?.reachable ? 'text-emerald-700' : 'text-amber-700'}`}>{localProbe.isLoading ? '探测中…' : localProbe.data?.reachable ? '在线' : '未连接'}</p></div><div className="rounded-lg bg-gray-50 p-3"><p className="text-[11px] text-gray-500">模型状态</p><p className={`mt-2 text-sm font-medium ${localProbe.data?.ready ? 'text-emerald-700' : 'text-amber-700'}`}>{localProbe.data?.ready ? '可用于审查' : '待启动或安装'}</p></div></div>
        {!localProbe.data?.ready && <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"><span>{localProbe.data?.error || '请先启动 Ollama，并在终端执行安装命令'}</span><button onClick={() => navigator.clipboard?.writeText(localProbe.data?.install_command || 'ollama pull qwen3.5:0.8b')} className="inline-flex items-center gap-1 rounded border border-amber-300 bg-white px-2 py-1 text-amber-800"><Copy size={12} />复制命令</button></div>}
      </section>

      <section className="wb-surface p-5">
        <div className="flex items-start justify-between gap-4">
          <div><p className="wb-eyebrow">网关路由</p><h2 className="mt-1 text-base font-semibold">模型调用状态</h2><p className="mt-1 text-xs text-gray-500">本地路线可直接探测；云端模型需显式验证后才显示“可用”</p></div>
          <button onClick={() => routeStatus.refetch()} className="wb-button-secondary text-xs">重新探测</button>
        </div>
        <div className="mt-4 grid md:grid-cols-3 gap-3">
          <div className="rounded-lg bg-gray-50 p-3"><p className="text-[11px] text-gray-500">LiteLLM 网关</p><p className={`mt-2 text-sm font-medium ${routeStatus.data?.gateway?.reachable ? 'text-emerald-700' : 'text-amber-700'}`}>{routeStatus.isLoading ? '探测中…' : routeStatus.data?.gateway?.reachable ? '可达' : routeStatus.data?.gateway?.configured ? '未连接' : '未配置'}</p></div>
          {(routeStatus.data?.routes || []).map((route: any) => <div key={route.alias} className="rounded-lg bg-gray-50 p-3"><p className="text-[11px] text-gray-500">{route.alias} · {route.purpose}</p><p className={`mt-2 flex items-center gap-1.5 text-sm font-medium ${route.available ? 'text-emerald-700' : 'text-amber-700'}`}>{route.available ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}{route.available ? '可用' : route.configured ? (route.upstream_authorized === false ? '上游未授权' : route.alias === 'MiniMax-M3' ? '待云端验证' : '待探测') : '未配置'}</p>{route.error && <p className="mt-1 text-[11px] text-amber-700 break-words">{route.error}</p>}</div>)}
        </div>
      </section>

      <section className="wb-surface p-5">
        <div className="flex items-start justify-between gap-4"><div><p className="wb-eyebrow">调用留痕</p><h2 className="mt-1 text-base font-semibold">最近模型调用</h2><p className="mt-1 text-xs text-gray-500">保存可见请求与响应、路由和哈希；不保存隐藏思维或未发送的二进制内容</p></div><button onClick={() => invocationQuery.refetch()} className="wb-button-secondary text-xs">刷新日志</button></div>
        {invocationQuery.isLoading ? <p className="mt-4 text-xs text-gray-400">加载日志…</p> : (invocationQuery.data?.items || []).length === 0 ? <div className="wb-empty mt-4 py-5">暂无模型调用记录</div> : <div className="mt-4 space-y-2">{(invocationQuery.data?.items || []).map((item: any) => <div key={item.id} className="rounded-lg border border-gray-200 bg-white"><button type="button" className="w-full px-3 py-2.5 text-left" onClick={() => setExpandedInvocation(expandedInvocation === item.id ? null : item.id)}><div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${item.status === 'completed' ? 'bg-emerald-500' : item.status === 'failed' ? 'bg-red-500' : 'bg-amber-500'}`} /><span className="text-sm font-medium">{item.model_name || item.route_alias}</span><span className="text-xs text-gray-500">{item.metadata?.purpose || '模型调用'} · {item.status}</span><span className="ml-auto text-[11px] text-gray-400">{item.duration_ms == null ? '—' : `${item.duration_ms} ms`}</span></div><p className="mt-1 text-[11px] text-gray-400">{item.created_at || ''}{item.construction_run_id ? ` · run ${String(item.construction_run_id).slice(0, 8)}` : ''}{item.audit_task_id ? ` · audit ${String(item.audit_task_id).slice(0, 8)}` : ''}</p></button>{expandedInvocation === item.id && <div className="border-t bg-slate-50 p-3 space-y-2"><div><p className="text-[11px] text-gray-500 mb-1">可见请求</p><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border bg-white p-2 text-[11px] text-gray-700">{item.request || '（未记录）'}</pre></div><div><p className="text-[11px] text-gray-500 mb-1">可见响应</p><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border bg-white p-2 text-[11px] text-gray-700">{item.response || item.error || '（未返回）'}</pre></div><div className="flex items-center justify-between"><span className="text-[11px] text-gray-400">request {item.request_hash?.slice(0, 12) || '—'} · response {item.response_hash?.slice(0, 12) || '—'}</span><button type="button" onClick={() => deleteInvocation(item.id)} className="text-[11px] text-red-600 hover:underline">确认删除</button></div></div>}</div>)}</div>}
      </section>

      <div className="grid gap-4">
        {isLoading ? <p className="text-gray-400 text-sm">{t('common.loading')}</p> :
          visibleModels.map(m => (
            <div key={m.id} className="wb-surface p-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold">{m.name}</h3>
                  <p className="text-sm text-gray-500">{typeLabel(m.config_type)} · {m.provider}{m.api_base ? ` · ${m.api_base}` : ''}</p>
                  {m.models?.length > 0 && (
                    <div className="flex gap-1 mt-2 flex-wrap">
                      {m.models.map(mn => <span key={mn} className="bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded">{mn}</span>)}
                    </div>
                  )}
                  {((m.options?.usage_tags as string[]) || []).length > 0 && (
                    <div className="flex gap-1 flex-wrap mt-1">
                      {((m.options?.usage_tags as string[]) || []).map((tag: string) => (
                        <span key={tag} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{tag}</span>
                      ))}
                    </div>
                  )}
                  {testResult[m.id] && <p className={`text-xs mt-1 ${testResult[m.id].startsWith('连接成功') ? 'text-green-600' : 'text-amber-600'}`}>{testResult[m.id]}</p>}
                </div>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => testMut.mutate(m.id)} disabled={testMut.isPending} className="inline-flex items-center gap-1 px-2.5 py-1.5 border rounded text-xs hover:bg-gray-50 disabled:opacity-50"><TestTube2 size={13} />测试</button>
                  <button onClick={() => openEdit(m)} className="inline-flex items-center gap-1 px-2.5 py-1.5 border rounded text-xs hover:bg-gray-50 text-blue-600"><Pencil size={13} />编辑</button>
                  <button onClick={() => setDeleteTarget(m)} className="inline-flex items-center gap-1 px-2.5 py-1.5 border rounded text-xs hover:bg-gray-50 text-red-500"><Trash2 size={13} />删除</button>
                </div>
              </div>
            </div>
          ))
        }
        {!isLoading && visibleModels.length === 0 && (
          <div className="bg-white border rounded-lg p-8 text-center text-gray-400">{t('model.empty')}</div>
        )}
        {!isLoading && visibleModels.length < (models as ModelConfig[]).length && <p className="text-xs text-gray-400">已隐藏 {(models as ModelConfig[]).length - visibleModels.length} 个历史/重复 Ollama 配置，可在数据库中保留并通过接口查询。</p>}
      </div>

      {/* 新建弹窗 */}
      {showCreate && <ModelFormModal title="新建模型" onClose={() => { setShowCreate(false); setCreateError('') }} onSubmit={(d: any) => createMut.mutate(d)}
        isPending={createMut.isPending} formTags={formTags} setFormTags={setFormTags} register={register}
        handleSubmit={handleSubmit} configType={watch('config_type') || 'llm'} setValue={setCreateValue} error={createError} />}

      {/* 编辑弹窗 */}
      {editTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setEditTarget(null)}>
          <div className="bg-white rounded-lg shadow-lg p-6 w-[480px]" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h3 className="font-semibold">编辑模型</h3>
              <button onClick={() => setEditTarget(null)} className="text-gray-400 hover:text-black"><X size={16} /></button>
            </div>
            <form onSubmit={handleEditSubmit(d => updateMut.mutate({ id: editTarget.id, data: d }))} className="space-y-3">
              <div><label className="block text-sm font-medium mb-1">名称 *</label>
                <input {...regEdit('name', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1">配置分类 *</label>
                <select {...regEdit('config_type', { required: true, onChange: e => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })} className="w-full border rounded-lg px-3 py-2 text-sm">
                  {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select></div>
              <div><label className="block text-sm font-medium mb-1">Provider *</label>
                <select {...regEdit('provider', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm">
                  {(PROVIDERS[watchEdit('config_type') || 'llm'] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select></div>
              <div><label className="block text-sm font-medium mb-1">API Base</label>
                <input {...regEdit('api_base')} className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1">模型名（每行一个）</label>
                <textarea {...regEdit('models_str')} rows={3} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
              {(watchEdit('config_type') || 'llm') === 'ocr' && (
                <div className="grid grid-cols-3 gap-3">
                  <div><label className="block text-sm font-medium mb-1">启用运行</label>
                    <select {...regEdit('ocr_enabled')} className="w-full border rounded-lg px-3 py-2 text-sm">
                      <option value="false">关闭</option><option value="true">开启</option>
                    </select></div>
                  <div><label className="block text-sm font-medium mb-1">OCR语言</label>
                    <input {...regEdit('ocr_lang')} placeholder="ch" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
                  <div><label className="block text-sm font-medium mb-1">设备</label>
                    <select {...regEdit('ocr_device')} className="w-full border rounded-lg px-3 py-2 text-sm">
                      <option value="cpu">CPU</option><option value="gpu">GPU</option>
                    </select></div>
                </div>
              )}
              <div><label className="block text-sm font-medium mb-1">高级参数 JSON</label>
                <textarea {...regEdit('options_json')} rows={3} placeholder={'{\"timeout\": 30}'} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
              <div><label className="text-xs text-gray-500 mb-2 block">用途标签</label>
                <div className="flex flex-wrap gap-2">
                  {USAGE_TAGS.map(tag => {
                    const sel = editTags.includes(tag)
                    return <button key={tag} type="button" onClick={() => setEditTags(prev => sel ? prev.filter(t => t !== tag) : [...prev, tag])}
                      className={`text-xs px-3 py-1.5 rounded-full border ${sel ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600'}`}>{tag}</button>
                  })}
                </div></div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setEditTarget(null)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                <button type="submit" disabled={updateMut.isPending} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                  {updateMut.isPending && <Loader2 size={13} className="animate-spin" />}保存
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ConfirmDialog open={!!deleteTarget} title={t('model.confirm_delete')} message={t('model.confirm_delete_msg', { name: deleteTarget?.name })}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)} onCancel={() => setDeleteTarget(null)} />
    </div>
  )
}

/** 新建模型表单弹窗 */
function ModelFormModal({ title, onClose, onSubmit, isPending, formTags, setFormTags, register, handleSubmit, configType, setValue, error }: any) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-lg p-6 w-[480px] max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="font-semibold mb-4">{title}</h3>
        {error && <div className="mb-3 p-2.5 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">{error}</div>}
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
          <div><label className="block text-sm font-medium mb-1">名称 *</label>
            <input {...register('name', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-sm font-medium mb-1">配置分类 *</label>
            <select {...register('config_type', { required: true, onChange: (e: any) => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })} className="w-full border rounded-lg px-3 py-2 text-sm">
              {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select></div>
          <div><label className="block text-sm font-medium mb-1">Provider *</label>
            <select {...register('provider', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm">
              {(PROVIDERS[configType] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select></div>
          <div><label className="block text-sm font-medium mb-1">API Key</label>
            <input {...register('api_key')} type="password" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-sm font-medium mb-1">API Base</label>
            <input {...register('api_base')} placeholder="https://api.openai.com/v1" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-sm font-medium mb-1">模型名（每行一个）</label>
            <textarea {...register('models_str')} rows={3} placeholder="gpt-4o&#10;gpt-4o-mini" className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
          {configType === 'ocr' && (
            <div className="grid grid-cols-3 gap-3">
              <div><label className="block text-sm font-medium mb-1">启用运行</label>
                <select {...register('ocr_enabled')} className="w-full border rounded-lg px-3 py-2 text-sm">
                  <option value="false">关闭</option><option value="true">开启</option>
                </select></div>
              <div><label className="block text-sm font-medium mb-1">OCR语言</label>
                <input {...register('ocr_lang')} placeholder="ch" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1">设备</label>
                <select {...register('ocr_device')} className="w-full border rounded-lg px-3 py-2 text-sm">
                  <option value="cpu">CPU</option><option value="gpu">GPU</option>
                </select></div>
            </div>
          )}
          <div><label className="block text-sm font-medium mb-1">高级参数 JSON</label>
            <textarea {...register('options_json')} rows={3} placeholder={'{\"timeout\": 30}'} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
          <div><label className="text-xs text-gray-500 mb-2 block">用途标签</label>
            <div className="flex flex-wrap gap-2">{[...USAGE_TAGS].map(tag => {
              const sel = formTags.includes(tag)
              return <button key={tag} type="button" onClick={() => setFormTags((prev: string[]) => sel ? prev.filter((t: string) => t !== tag) : [...prev, tag])}
                className={`text-xs px-3 py-1.5 rounded-full border ${sel ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}>{tag}</button>
            })}</div></div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 border rounded-lg text-sm">取消</button>
            <button type="submit" disabled={isPending} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
              {isPending && <Loader2 size={13} className="animate-spin" />}保存
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
