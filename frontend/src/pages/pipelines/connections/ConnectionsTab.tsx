import { useState, useEffect, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Database, FileUp, Globe, X, Loader2, RefreshCw, ArrowLeft, CircleCheck, Eye, List, SlidersHorizontal } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

interface Connection {
  id: string
  name: string
  kind: string
  status: string
}

interface ResourceColumn {
  name: string
  type?: string
  nullable?: boolean
}

interface Resource {
  name: string
  columns?: ResourceColumn[]
}

const KIND_META: Record<string, { icon: React.ReactNode; label: string }> = {
  file:     { icon: <FileUp size={14} />,   label: '文件上传' },
  mysql:    { icon: <Database size={14} />, label: 'MySQL' },
  postgres: { icon: <Database size={14} />, label: 'PostgreSQL' },
  mongo:    { icon: <Database size={14} />, label: 'MongoDB' },
  rest:     { icon: <Globe size={14} />,    label: 'REST API' },
}

const STATUS_STYLE: Record<string, string> = {
  active:   'text-green-600 bg-green-50 border-green-200',
  inactive: 'text-gray-400 bg-gray-50 border-gray-200',
  error:    'text-red-500 bg-red-50 border-red-200',
}

const STATUS_LABEL: Record<string, string> = {
  active: '活跃', inactive: '未激活', error: '错误',
}

const KIND_CONFIG_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: string }[]> = {
  mysql:    [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '3306' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'root' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  postgres: [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '5432' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'postgres' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  mongo:    [
    { key: 'uri', label: 'URI', placeholder: 'mongodb://127.0.0.1:27017' },
    { key: 'database', label: '数据库名', placeholder: 'example' },
    { key: 'collection', label: '集合名', placeholder: 'records' },
  ],
  rest:     [
    { key: 'base_url', label: 'base_url', placeholder: 'https://api.example.com/v1' },
    { key: 'endpoints', label: 'endpoints（逗号分隔）', placeholder: '/records' },
    { key: 'auth', label: 'auth（JSON）', placeholder: '{"type":"bearer","token":""}' },
    { key: 'params', label: 'params（JSON）', placeholder: '{}' },
    { key: 'pagination', label: 'pagination（JSON）', placeholder: '{"type":"page","data_path":"data"}' },
    { key: 'data_path', label: 'data_path', placeholder: 'data' },
  ],
  file: [],
}

function FileUploadZone({ files, onFilesChange }: { files: File[]; onFilesChange: (f: File[]) => void }) {
  const onDrop = useCallback((accepted: File[]) => {
    onFilesChange([...files, ...accepted])
  }, [files, onFilesChange])

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({ onDrop, multiple: true, noClick: true })

  return (
    <div>
      <div
        {...getRootProps()}
        onClick={open}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors
          ${isDragActive ? 'border-black bg-gray-50' : 'border-gray-200 hover:border-gray-400'}`}
      >
        <input {...getInputProps()} />
        <FileUp size={28} className="mx-auto mb-2 text-gray-400" />
        {isDragActive ? (
          <p className="text-sm text-black font-medium">松开以添加文件</p>
        ) : (
          <>
            <p className="text-sm text-gray-600">拖拽文件到此处，或<span className="underline ml-1 cursor-pointer">点击选择</span></p>
            <p className="text-xs text-gray-400 mt-1">支持 CSV、XLSX、JSON、PDF、DOCX 等格式，可多选</p>
          </>
        )}
      </div>
      {files.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
              <FileUp size={12} className="text-gray-400 shrink-0" />
              <span className="flex-1 truncate text-gray-700">{f.name}</span>
              <span className="text-gray-400">{(f.size / 1024).toFixed(1)} KB</span>
              <button
                type="button"
                onClick={() => onFilesChange(files.filter((_, j) => j !== i))}
                className="text-gray-400 hover:text-red-500"
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ConnectionsTab() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const returnTo = searchParams.get('return_to')
  const returnDraftId = searchParams.get('draft_id')
  const [connections, setConnections] = useState<Connection[]>([])
  const [templates, setTemplates] = useState<Array<{ kind: string; name: string; config: Record<string, unknown>; note?: string; import_supported?: boolean }>>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingConfig, setTestingConfig] = useState(false)
  const [formError, setFormError] = useState('')
  const [syncing, setSyncing] = useState<string | null>(null)

  const [formName, setFormName] = useState('')
  const [formKind, setFormKind] = useState('mysql')
  const [formConfig, setFormConfig] = useState<Record<string, string>>({})
  const [formSyncMode, setFormSyncMode] = useState<'snapshot' | 'append'>('snapshot')
  const [formFiles, setFormFiles] = useState<File[]>([])
  const [formNotice, setFormNotice] = useState('')
  const [syncError, setSyncError] = useState('')
  const [resourceConnection, setResourceConnection] = useState<Connection | null>(null)
  const [resources, setResources] = useState<Resource[]>([])
  const [resourceLoading, setResourceLoading] = useState(false)
  const [resourceError, setResourceError] = useState('')
  const [selectedResource, setSelectedResource] = useState('')
  const [selectedColumns, setSelectedColumns] = useState<string[]>([])
  const [resourceFilter, setResourceFilter] = useState({ column: '', op: 'eq', value: '' })
  const [previewRows, setPreviewRows] = useState<Record<string, unknown>[]>([])
  const [previewEstimate, setPreviewEstimate] = useState<number | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [importingResource, setImportingResource] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [importName, setImportName] = useState('')
  const [importClass, setImportClass] = useState<'regular' | 'temporal'>('regular')
  const [importPrivacy, setImportPrivacy] = useState<'standard' | 'private'>('standard')
  const [importMode, setImportMode] = useState<'snapshot' | 'append'>('snapshot')
  const [watermarkColumn, setWatermarkColumn] = useState('')
  const [dedupeKey, setDedupeKey] = useState('')

  const loadConnections = () => {
    setLoading(true)
    apiClientV2.get('/connections')
      .then((res: unknown) => setConnections(Array.isArray(res) ? res : ((res as { data?: Connection[] })?.data ?? [])))
      .catch(() => setConnections([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadConnections() }, [])
  useEffect(() => {
    apiClientV2.get('/connections/templates')
      .then((res: unknown) => {
        const value = (res as { templates?: typeof templates })?.templates
        if (Array.isArray(value)) setTemplates(value)
      })
      .catch(() => setTemplates([]))
  }, [])

  const resetForm = () => {
    setFormName('')
    setFormKind('mysql')
    setFormConfig({})
    setFormSyncMode('snapshot')
    setFormFiles([])
    setFormError('')
    setFormNotice('')
  }

  const applyTemplate = (kind: string) => {
    const template = templates.find(item => item.kind === kind)
    if (!template) return
    setFormKind(kind)
    setFormConfig(Object.fromEntries(Object.entries(template.config).map(([key, value]) => [
      key,
      typeof value === 'string' ? value : JSON.stringify(value),
    ])))
    setFormNotice(template.note || (template.import_supported === false ? '本轮可测试、预览，暂不导入' : '已载入示例字段，可按需修改'))
  }

  const handleSave = async () => {
    if (!formName.trim()) { setFormError('请填写连接名称'); return }
    if (formKind === 'file' && formFiles.length === 0) { setFormError('请至少选择一个文件'); return }
    setSaving(true)
    setFormError('')
    try {
      if (formKind === 'file') {
        const created = await apiClientV2.post<{ id?: string }>('/connections', {
          name: formName, kind: 'file',
          config: { files: formFiles.map(f => f.name), sync_mode: formSyncMode },
        })
        const connectionId = created?.id
        for (const file of formFiles) {
          const fd = new FormData()
          fd.append('file', file)
          await apiClientV2.post('/datasets/upload', fd, {
            params: { connection_id: connectionId, data_class: 'regular' },
            headers: { 'Content-Type': 'multipart/form-data' },
          })
        }
      } else {
        const config: Record<string, unknown> = { ...formConfig, sync_mode: formSyncMode }
        if (formKind === 'rest') {
          for (const key of ['auth', 'params', 'pagination']) {
            // Keep the API contract typed.  Template values are displayed as
            // editable JSON text, but the persisted connection must contain
            // objects (not a second, quoted JSON string).
            if (typeof config[key] === 'string' && config[key]) {
              try { config[key] = JSON.parse(config[key] as string) }
              catch { throw new Error(`${key} 必须是有效 JSON`) }
            }
          }
          config.endpoints = String(config.endpoints || '').split(',').map(item => item.trim()).filter(Boolean)
        }
        await apiClientV2.post('/connections', {
          name: formName, kind: formKind,
          config,
        })
      }
      setShowForm(false)
      resetForm()
      loadConnections()
    } catch (e: unknown) {
      const err = e as { detail?: string; response?: { data?: { detail?: string } }; message?: string }
      setFormError(err?.detail || err?.response?.data?.detail || err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleTestConfig = async () => {
    setTestingConfig(true)
    setFormError('')
    try {
      const config: Record<string, unknown> = { ...formConfig }
      if (formKind === 'rest') {
        for (const key of ['auth', 'params', 'pagination']) {
          if (typeof config[key] === 'string' && config[key]) config[key] = JSON.parse(config[key] as string)
        }
        config.endpoints = String(config.endpoints || '').split(',').map(item => item.trim()).filter(Boolean)
      }
      const result = await apiClientV2.post<{ success?: boolean; detail?: string }>('/connections/test-config', { type: formKind, config })
      setFormNotice(result?.success ? '连接测试成功，可保存' : `连接测试未通过：${result?.detail || '请检查字段或服务状态'}`)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setFormError(err?.detail || err?.message || '连接测试失败')
    } finally { setTestingConfig(false) }
  }

  const handleSync = async (id: string) => {
    setSyncing(id)
    setSyncError('')
    try {
      await apiClientV2.post(`/connections/${id}/sync`, {})
      loadConnections()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setSyncError(err?.detail || err?.message || '同步未启动，请先选择资源范围')
    } finally {
      setSyncing(null)
    }
  }

  const currentResource = resources.find(item => item.name === selectedResource)
  const resourceColumns = currentResource?.columns || []
  const activeFilters = resourceFilter.column && resourceFilter.value !== ''
    ? [{ column: resourceFilter.column, op: resourceFilter.op, value: resourceFilter.value }]
    : []

  const closeResources = () => {
    setResourceConnection(null)
    setResources([])
    setSelectedResource('')
    setSelectedColumns([])
    setPreviewRows([])
    setPreviewEstimate(null)
    setResourceError('')
    setImportStatus('')
  }

  const openResources = async (connection: Connection) => {
    if (resourceConnection?.id === connection.id) { closeResources(); return }
    setResourceConnection(connection)
    setResourceLoading(true)
    setResourceError('')
    setPreviewRows([])
    setPreviewEstimate(null)
    setImportStatus('')
    try {
      const result = await apiClientV2.get<{ resources?: Resource[] }>(`/connections/${connection.id}/resources`)
      const next = Array.isArray(result?.resources) ? result.resources : []
      setResources(next)
      setSelectedResource(next[0]?.name || '')
      setSelectedColumns([])
      setImportName(next[0] ? `${connection.name} / ${next[0].name}` : connection.name)
    } catch (e: unknown) {
      const err = e as { detail?: { message?: string } | string; message?: string }
      setResourceError(typeof err?.detail === 'string' ? err.detail : err?.detail?.message || err?.message || '资源读取失败，请检查连接状态')
    } finally { setResourceLoading(false) }
  }

  const previewResource = async () => {
    if (!resourceConnection || !selectedResource) { setResourceError('请先选择资源'); return }
    setPreviewLoading(true)
    setResourceError('')
    try {
      const result = await apiClientV2.post<{ rows?: Record<string, unknown>[]; total_estimate?: number }>(`/connections/${resourceConnection.id}/preview`, {
        resource: selectedResource,
        columns: selectedColumns,
        filters: activeFilters,
        limit: 25,
      })
      setPreviewRows(result?.rows || [])
      setPreviewEstimate(typeof result?.total_estimate === 'number' ? result.total_estimate : null)
    } catch (e: unknown) {
      const err = e as { detail?: { message?: string } | string; message?: string }
      setResourceError(typeof err?.detail === 'string' ? err.detail : err?.detail?.message || err?.message || '预览失败，请检查资源和筛选条件')
    } finally { setPreviewLoading(false) }
  }

  const importResource = async () => {
    if (!resourceConnection || !selectedResource) { setResourceError('请先选择要导入的表或资源'); return }
    if (!importName.trim()) { setResourceError('请填写数据集名称'); return }
    if (importMode === 'append' && (!watermarkColumn || !dedupeKey)) { setResourceError('增量导入需要配置水位列和去重键'); return }
    setImportingResource(true)
    setResourceError('')
    try {
      const result = await apiClientV2.post<{ id?: string; status?: string }>(`/connections/${resourceConnection.id}/imports`, {
        resource: selectedResource,
        columns: selectedColumns,
        filters: activeFilters,
        limit: 200,
        dataset_name: importName.trim(),
        data_class: importClass,
        privacy_level: importPrivacy,
        mode: importMode,
        watermark_column: importMode === 'append' ? watermarkColumn : null,
        dedupe_key: importMode === 'append' ? dedupeKey : null,
      })
      setImportStatus(`导入任务已创建：${result?.id || 'queued'}（${result?.status || 'queued'}）`)
    } catch (e: unknown) {
      const err = e as { detail?: { message?: string } | string; message?: string }
      setResourceError(typeof err?.detail === 'string' ? err.detail : err?.detail?.message || err?.message || '导入任务未创建')
    } finally { setImportingResource(false) }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('确认删除此连接？')) return
    await apiClientV2.delete(`/connections/${id}`)
    loadConnections()
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">加载中...</div>

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div className="flex items-start gap-3">
          {returnTo && <button type="button" onClick={() => navigate(`${returnTo}${returnDraftId ? `?draft_id=${encodeURIComponent(returnDraftId)}` : ''}`)} className="mt-0.5 flex items-center gap-1 text-xs text-gray-500 hover:text-black"><ArrowLeft size={13} />返回构筑向导</button>}
          <div>
          <h2 className="text-lg font-semibold">数据连接</h2>
          <p className="text-xs text-gray-400 mt-0.5">管理数据源连接，支持数据库、API 和文件上传</p>
          </div>
        </div>
        <button
          onClick={() => { resetForm(); setShowForm(true) }}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm"
        >
          <Plus size={14} /> 新建连接
        </button>
      </div>

      {showForm && (
        <div className="border rounded-xl p-5 bg-white space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-medium text-sm">新建连接</h3>
            <button onClick={() => { setShowForm(false); resetForm() }} className="text-gray-400 hover:text-black">
              <X size={16} />
            </button>
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-1 block">连接名称 *</label>
            <input
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="例：ERP 订单数据库"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
            />
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-2 block">连接类型</label>
            <div className="flex gap-2 flex-wrap">
              {Object.entries(KIND_META).map(([k, m]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => { setFormKind(k); setFormConfig({}); setFormFiles([]) }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors
                    ${formKind === k ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  {m.icon} {m.label}
                </button>
              ))}
            </div>
            {templates.length > 0 && <div className="mt-3 flex flex-wrap items-center gap-2"><span className="text-[11px] text-gray-400">示例模板：</span>{templates.map(item => <button key={item.kind} type="button" onClick={() => applyTemplate(item.kind)} className="text-[11px] px-2 py-1 border rounded-md text-gray-600 hover:bg-gray-50">载入 {item.name}</button>)}</div>}
          </div>

          {formKind === 'file' ? (
            <FileUploadZone files={formFiles} onFilesChange={setFormFiles} />
          ) : (
            <div className="space-y-3">
              {KIND_CONFIG_FIELDS[formKind]?.map(f => (
                <div key={f.key}>
                  <label className="text-xs text-gray-500 mb-1 block">{f.label}</label>
                  <input
                    type={f.type || 'text'}
                    value={formConfig[f.key] || ''}
                    onChange={e => setFormConfig(p => ({ ...p, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
                  />
                </div>
              ))}
            </div>
          )}

          {formNotice && <p className="text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">{formNotice}</p>}

          <div>
            <label className="text-xs text-gray-500 mb-2 block">同步模式</label>
            <div className="flex gap-4">
              {(['snapshot', 'append'] as const).map(m => (
                <label key={m} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="sync_mode"
                    value={m}
                    checked={formSyncMode === m}
                    onChange={() => setFormSyncMode(m)}
                    className="accent-black"
                  />
                  <span>{m === 'snapshot' ? 'SNAPSHOT（全量覆盖）' : 'APPEND（增量追加）'}</span>
                </label>
              ))}
            </div>
          </div>

          {formError && <p className="text-red-500 text-xs">{formError}</p>}

          <div className="flex gap-2 justify-end">
            <button onClick={() => { setShowForm(false); resetForm() }} className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
              取消
            </button>
            {formKind !== 'file' && <button type="button" onClick={handleTestConfig} disabled={testingConfig} className="flex items-center gap-1.5 px-4 py-2 text-sm border rounded-lg hover:bg-gray-50 disabled:opacity-50"><CircleCheck size={13} />{testingConfig ? '测试中...' : '测试连接'}</button>}
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      )}

      {syncError && <div className="border border-amber-200 bg-amber-50 text-amber-800 rounded-lg px-3 py-2 text-xs">{syncError}</div>}

      {connections.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-10 text-center text-gray-400 space-y-2">
          <Database size={28} className="mx-auto opacity-30" />
          <p className="text-sm">暂无数据连接</p>
          <p className="text-xs">点击「新建连接」添加数据源</p>
        </div>
      ) : (
        <div className="border rounded-xl divide-y overflow-hidden">
          {connections.map(c => {
            const meta = KIND_META[c.kind] ?? KIND_META.file
            const statusStyle = STATUS_STYLE[c.status] ?? STATUS_STYLE.inactive
            const statusLabel = STATUS_LABEL[c.status] ?? c.status
            return (
              <div key={c.id}>
                <div className="p-4 flex items-center gap-3">
                  <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center text-gray-500">
                    {meta.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm truncate">{c.name}</p>
                    <p className="text-xs text-gray-400">{meta.label}</p>
                  </div>
                  <span className={`text-xs font-medium px-2 py-0.5 rounded border ${statusStyle}`}>
                    {statusLabel}
                  </span>
                  {(c.kind === 'mysql' || c.kind === 'postgres') && <button onClick={() => openResources(c)} className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-gray-50"><List size={12} />资源</button>}
                  <button
                    onClick={() => handleSync(c.id)}
                    disabled={syncing === c.id}
                    className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                  >
                    <RefreshCw size={11} className={syncing === c.id ? 'animate-spin' : ''} />
                    同步
                  </button>
                  <button
                    onClick={() => handleDelete(c.id)}
                    className="text-gray-400 hover:text-red-500 text-xs px-1 transition-colors"
                  >
                    删除
                  </button>
                </div>
                {resourceConnection?.id === c.id && <div className="border-t bg-slate-50 p-4 space-y-4">
                  {resourceLoading ? <div className="text-xs text-gray-500 flex items-center gap-2"><Loader2 size={13} className="animate-spin" />读取表和字段…</div> : <>
                    {resourceError && <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{resourceError}</div>}
                    <div className="grid lg:grid-cols-[.8fr_1.2fr] gap-4">
                      <div className="space-y-3">
                        <div><label className="text-xs text-gray-500 mb-1 block">资源 / 表</label><select value={selectedResource} onChange={event => { setSelectedResource(event.target.value); setSelectedColumns([]); setPreviewRows([]); setPreviewEstimate(null); setImportName(`${c.name} / ${event.target.value}`) }} className="w-full border rounded-lg px-3 py-2 text-sm bg-white"><option value="">请选择表</option>{resources.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></div>
                        {resourceColumns.length > 0 && <div><div className="flex items-center justify-between mb-1"><label className="text-xs text-gray-500">字段（不选表示全列）</label><button type="button" className="text-[11px] text-blue-600" onClick={() => setSelectedColumns(selectedColumns.length === 0 ? resourceColumns.map(item => item.name).slice(0, -1) : [])}>{selectedColumns.length === 0 ? '取消全选' : '全选'}</button></div><div className="max-h-32 overflow-y-auto rounded-lg border bg-white p-2 space-y-1">{resourceColumns.map(column => <label key={column.name} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={selectedColumns.length === 0 || selectedColumns.includes(column.name)} onChange={() => { const all = resourceColumns.map(item => item.name); const current = selectedColumns.length === 0 ? all : selectedColumns; const next = current.includes(column.name) ? current.filter(item => item !== column.name) : [...current, column.name]; setSelectedColumns(next.length === all.length ? [] : next) }} /><span className="truncate">{column.name}</span><span className="ml-auto text-[10px] text-gray-400">{column.type || 'field'}</span></label>)}</div></div>}
                        <div><div className="flex items-center gap-1 mb-1"><SlidersHorizontal size={12} className="text-gray-500" /><label className="text-xs text-gray-500">筛选（等值 / 范围）</label></div><div className="grid grid-cols-[1.1fr_.7fr_1fr] gap-2"><select value={resourceFilter.column} onChange={event => setResourceFilter(prev => ({ ...prev, column: event.target.value }))} className="border rounded-lg px-2 py-2 text-xs bg-white"><option value="">字段</option>{resourceColumns.map(column => <option key={column.name} value={column.name}>{column.name}</option>)}</select><select value={resourceFilter.op} onChange={event => setResourceFilter(prev => ({ ...prev, op: event.target.value }))} className="border rounded-lg px-2 py-2 text-xs bg-white"><option value="eq">等于</option><option value="gt">大于</option><option value="gte">不小于</option><option value="lt">小于</option><option value="lte">不大于</option></select><input value={resourceFilter.value} onChange={event => setResourceFilter(prev => ({ ...prev, value: event.target.value }))} placeholder="值" className="border rounded-lg px-2 py-2 text-xs" /></div></div>
                        <div className="flex gap-2"><button type="button" onClick={previewResource} disabled={previewLoading || !selectedResource} className="inline-flex items-center gap-1 px-3 py-2 text-xs border rounded-lg bg-white hover:bg-gray-100 disabled:opacity-50"><Eye size={13} />{previewLoading ? '预览中…' : '预览数据'}</button><button type="button" onClick={importResource} disabled={importingResource || !selectedResource} className="inline-flex items-center gap-1 px-3 py-2 text-xs bg-black text-white rounded-lg disabled:opacity-50">{importingResource && <Loader2 size={13} className="animate-spin" />}后台导入</button><button type="button" onClick={closeResources} className="text-xs px-2 py-2 text-gray-500">收起</button></div>
                      </div>
                      <div className="rounded-lg border bg-white p-3 space-y-3">
                        <div className="flex items-center justify-between"><span className="text-xs font-medium">导入设置</span><span className="text-[11px] text-gray-400">{previewEstimate == null ? '先预览以估算行数' : `估算 ${previewEstimate} 行`}</span></div>
                        <div className="grid grid-cols-2 gap-2"><input value={importName} onChange={event => setImportName(event.target.value)} placeholder="数据集名称" className="border rounded-lg px-2 py-2 text-xs" /><select value={importClass} onChange={event => setImportClass(event.target.value as 'regular' | 'temporal')} className="border rounded-lg px-2 py-2 text-xs bg-white"><option value="regular">常规数据</option><option value="temporal">时序数据</option></select><select value={importPrivacy} onChange={event => setImportPrivacy(event.target.value as 'standard' | 'private')} className="border rounded-lg px-2 py-2 text-xs bg-white"><option value="standard">standard</option><option value="private">private</option></select><select value={importMode} onChange={event => setImportMode(event.target.value as 'snapshot' | 'append')} className="border rounded-lg px-2 py-2 text-xs bg-white"><option value="snapshot">快照导入</option><option value="append">增量导入</option></select></div>
                        {importMode === 'append' && <div className="grid grid-cols-2 gap-2"><input value={watermarkColumn} onChange={event => setWatermarkColumn(event.target.value)} placeholder="水位列，如 updated_at" className="border rounded-lg px-2 py-2 text-xs" /><input value={dedupeKey} onChange={event => setDedupeKey(event.target.value)} placeholder="去重键，如 id" className="border rounded-lg px-2 py-2 text-xs" /></div>}
                        {importStatus && <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-lg px-2 py-1.5">{importStatus}</p>}
                        <div className="overflow-auto max-h-48 rounded border"><table className="min-w-full text-[11px]"><thead className="bg-gray-50"><tr>{(previewRows[0] ? Object.keys(previewRows[0]) : []).map(key => <th key={key} className="px-2 py-1.5 text-left font-medium whitespace-nowrap">{key}</th>)}</tr></thead><tbody className="divide-y">{previewRows.slice(0, 8).map((row, index) => <tr key={index}>{Object.values(row).map((value, cell) => <td key={cell} className="px-2 py-1.5 max-w-[160px] truncate">{String(value ?? '')}</td>)}</tr>)}</tbody></table>{previewRows.length === 0 && <p className="px-3 py-5 text-center text-xs text-gray-400">暂无预览数据</p>}</div>
                      </div>
                    </div>
                  </>}
                </div>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
