import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { GitBranch, Table2, ArrowRight, CheckCircle, AlertTriangle, Clock, FileEdit, Activity, Images, Radio } from 'lucide-react'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import { apiClientV2 } from '@/api/client'

interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
}

const PIPELINE_STATUS_STYLE: Record<string, string> = {
  draft:     'bg-gray-100 text-gray-600',
  editing:   'bg-blue-50 text-blue-600',
  running:   'bg-amber-50 text-amber-600',
  failed:    'bg-red-50 text-red-600',
  published: 'bg-green-50 text-green-600',
}

const PIPELINE_STATUS_LABEL: Record<string, string> = {
  draft: '草稿', editing: '编辑中', running: '运行中',
  failed: '失败', published: '已发布',
}

const CURATED_STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

export default function DataManagementPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [curated, setCurated] = useState<CuratedDataset[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      pipelinesApi.list(),
      apiClientV2.get<CuratedDataset[]>('/curated'),
    ]).then(([pls, cur]) => {
      setPipelines(Array.isArray(pls) ? pls : [])
      setCurated(Array.isArray(cur) ? cur : [])
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const pipelineByStatus = pipelines.reduce<Record<string, number>>((acc, p) => {
    acc[p.status] = (acc[p.status] || 0) + 1
    return acc
  }, {})

  const curatedByStatus = curated.reduce<Record<string, number>>((acc, c) => {
    const k = c.status === 'approved' ? 'approved' : c.status === 'rejected' ? 'rejected' : 'pending_review'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})

  if (loading) return <p className="text-gray-400 text-sm p-6">加载中...</p>

  return (
    <div className="wb-page max-w-[1320px]">
      <div className="wb-page-header"><div><p className="wb-eyebrow">数据构筑</p><h1 className="wb-page-title mt-2">选择数据类型</h1><p className="wb-page-subtitle">常规、时序、多模态，或逐事件动态接收。</p></div></div>
      <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          { title: '常规数据', detail: 'CSV · Excel · JSON · 数据库表', icon: Table2, path: '/data/regular', tone: 'text-blue-700 bg-blue-50' },
          { title: '时序数据', detail: 'FactoryNet · 时间轴 · 序列语义', icon: Activity, path: '/data/temporal', tone: 'text-teal-700 bg-teal-50' },
          { title: '动态数据构建', detail: 'FactoryNet · 逐事件实时接收', icon: Radio, path: '/data/dynamic', tone: 'text-amber-700 bg-amber-50' },
          { title: '多模态数据', detail: 'RGB · 深度 · 掩码 · 点云', icon: Images, path: '/data/multimodal', tone: 'text-violet-700 bg-violet-50' },
        ].map(({ title, detail, icon: Icon, path, tone }) => <button key={title} type="button" onClick={() => navigate(path)} className="wb-choice-card flex items-start gap-3 text-left hover:border-gray-400"><span className={`flex h-10 w-10 items-center justify-center rounded-lg ${tone}`}><Icon size={18} /></span><span><strong className="text-sm">{title}</strong><span className="mt-1 block text-xs text-gray-500">{detail}</span><span className="mt-3 inline-flex items-center gap-1 text-xs text-gray-500">进入向导 <ArrowRight size={12} /></span></span></button>)}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Pipeline overview */}
        <div className="bg-white border rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <GitBranch size={16} className="text-blue-500" />
              <h3 className="font-semibold text-gray-800">{t('data.pipeline_overview')}</h3>
            </div>
            <button
              onClick={() => navigate('/data/pipelines')}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-black"
            >
              {t('data.go_pipelines')} <ArrowRight size={12} />
            </button>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-bold">{pipelines.length}</span>
            <span className="text-sm text-gray-400">{t('data.total_pipelines')}</span>
          </div>

          {pipelines.length === 0 ? (
            <p className="text-sm text-gray-400 py-2">{t('data.no_pipelines')}</p>
          ) : (
            <div className="space-y-2">
              {Object.entries(pipelineByStatus).map(([status, count]) => (
                <div key={status} className="flex items-center justify-between text-sm">
                  <span className={`text-xs px-2 py-0.5 rounded ${PIPELINE_STATUS_STYLE[status] || 'bg-gray-100 text-gray-600'}`}>
                    {PIPELINE_STATUS_LABEL[status] || status}
                  </span>
                  <span className="font-medium text-gray-700">{count}</span>
                </div>
              ))}
            </div>
          )}

          {/* Recent pipelines */}
          {pipelines.length > 0 && (
            <div className="border-t pt-3 space-y-1.5">
              {pipelines.slice(0, 4).map(p => (
                <div
                  key={p.id}
                  onClick={() => navigate(`/data/pipelines/${p.id}`)}
                  className="flex items-center gap-2 text-xs cursor-pointer hover:bg-gray-50 -mx-2 px-2 py-1 rounded"
                >
                  <FileEdit size={11} className="text-gray-400 shrink-0" />
                  <span className="flex-1 truncate text-gray-700">{p.name}</span>
                  <span className="text-gray-400">{p.domain || '通用'}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Curated dataset overview */}
        <div className="bg-white border rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Table2 size={16} className="text-purple-500" />
              <h3 className="font-semibold text-gray-800">{t('data.curated_overview')}</h3>
            </div>
            <button
              onClick={() => navigate('/data/structured')}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-black"
            >
              {t('data.go_structured')} <ArrowRight size={12} />
            </button>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-bold">{curated.length}</span>
            <span className="text-sm text-gray-400">{t('data.total_curated')}</span>
          </div>

          {curated.length === 0 ? (
            <p className="text-sm text-gray-400 py-2">{t('data.no_curated')}</p>
          ) : (
            <div className="space-y-2">
              {[
                ['pending_review', '待审核'],
                ['approved', '已审核'],
                ['rejected', '已拒绝'],
              ].map(([key, label]) =>
                curatedByStatus[key] ? (
                  <div key={key} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-1.5">
                      {CURATED_STATUS_ICON(key)}
                      <span className="text-xs text-gray-600">{label}</span>
                    </div>
                    <span className="font-medium text-gray-700">{curatedByStatus[key]}</span>
                  </div>
                ) : null
              )}
            </div>
          )}

          {/* Recent curated datasets */}
          {curated.length > 0 && (
            <div className="border-t pt-3 space-y-1.5">
              {curated.slice(0, 4).map(c => (
                <div
                  key={c.id}
                  onClick={() => navigate('/data/structured')}
                  className="flex items-center gap-2 text-xs cursor-pointer hover:bg-gray-50 -mx-2 px-2 py-1 rounded"
                >
                  {CURATED_STATUS_ICON(c.status)}
                  <span className="flex-1 truncate text-gray-700">{c.name}</span>
                  {c.row_count != null && (
                    <span className="text-gray-400">{c.row_count} 行</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
