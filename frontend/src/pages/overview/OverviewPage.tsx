import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  Database,
  FileSpreadsheet,
  Images,
  Network,
  ShieldCheck,
  Table2,
  Timer,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import StatusBadge from '@/components/StatusBadge'

interface RecentOntology {
  id: string
  name: string
  domain: string
  status: string
  entity_count: number
  logic_count: number
  action_count: number
  updated_at: string
}

interface DashboardStats {
  ontology_count: number
  entity_count: number
  logic_count: number
  action_count: number
  dataset_count?: number
  task_count?: number
  review_count?: number
  recent_ontologies: RecentOntology[]
  domain_counts: Record<string, number>
  status_counts: Record<string, number>
  data_class_counts?: Record<string, number>
}

const FALLBACK: DashboardStats = {
  ontology_count: 0,
  entity_count: 0,
  logic_count: 0,
  action_count: 0,
  dataset_count: 0,
  task_count: 0,
  review_count: 0,
  recent_ontologies: [],
  domain_counts: {},
  status_counts: {},
  data_class_counts: { regular: 0, temporal: 0, multimodal: 0 },
}

type Icon = typeof Database

const dataTypes: Array<{
  title: string
  subtitle: string
  detail: string
  icon: Icon
  tone: string
  path: string
}> = [
  { title: '常规数据', subtitle: '表格与数据库', detail: 'CSV · Excel · JSON · 数据库表', icon: Table2, tone: 'blue', path: '/data/regular' },
  { title: '时序数据', subtitle: '序列与时间轴', detail: 'FactoryNet · Ordinal · Instant', icon: Activity, tone: 'teal', path: '/data/temporal' },
  { title: '多模态数据', subtitle: '图像、深度与点云', detail: 'RGB-D · 掩码 · 证据关联', icon: Images, tone: 'violet', path: '/data/multimodal' },
]

const viewTypes: Array<{ title: string; detail: string; icon: Icon; path: string }> = [
  { title: '本体关系', detail: '实体类型、属性与关系', icon: Network, path: '/ontologies' },
  { title: '实体记录', detail: '实体类型与来源记录', icon: Database, path: '/ontologies' },
  { title: '时序记录', detail: '顺序、窗口与时间轴', icon: Timer, path: '/data/temporal' },
  { title: '证据工作区', detail: '媒体、来源与本体联动', icon: Images, path: '/data/multimodal' },
  { title: '质量审查', detail: '规则校验与审查留痕', icon: ShieldCheck, path: '/ontologies' },
]

const cases = [
  { name: 'C-MAPSS FD001', tag: '常规数据', detail: '设备与传感器读数', path: '/data/regular', icon: FileSpreadsheet, tone: 'bg-blue-50 text-blue-700' },
  { name: 'FactoryNet CNC', tag: '时序数据', detail: '机器、工序、传感器与时间轴', path: '/data/temporal', icon: Activity, tone: 'bg-teal-50 text-teal-700' },
  { name: 'I‑BADAS', tag: '多模态数据', detail: '12 组 RGB-D、掩码与点云样例', path: '/data/multimodal', icon: Images, tone: 'bg-violet-50 text-violet-700' },
]

function toneClasses(tone: string) {
  return tone === 'blue'
    ? { icon: 'bg-blue-50 text-blue-700', border: 'hover:border-blue-300' }
    : tone === 'teal'
      ? { icon: 'bg-teal-50 text-teal-700', border: 'hover:border-teal-300' }
      : { icon: 'bg-violet-50 text-violet-700', border: 'hover:border-violet-300' }
}

export default function OverviewPage() {
  const navigate = useNavigate()
  const query = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: () => apiClientV2.get('/dashboard') as any,
  })
  const data = { ...FALLBACK, ...(query.data ?? {}) }
  const classCounts = data.data_class_counts ?? FALLBACK.data_class_counts
  const updated = data.recent_ontologies ?? []

  return (
    <div className="mx-auto max-w-[1540px] space-y-7">
      <header className="flex items-end justify-between gap-6">
        <div>
          <p className="wb-eyebrow">工作区总览</p>
          <h1 className="wb-page-title mt-2">本体构筑工作台</h1>
          <p className="wb-page-subtitle">选择数据类型，开始一次构建。</p>
        </div>
        <button type="button" onClick={() => navigate('/data')} className="wb-button-primary"><ArrowRight size={16} />开始构建</button>
      </header>

      <section className="grid grid-cols-3 gap-4" aria-label="数据分类">
        {dataTypes.map(({ title, subtitle, detail, icon: IconComponent, tone, path }) => {
          const colors = toneClasses(tone)
          return <button key={title} type="button" onClick={() => navigate(path)} className={`group wb-surface flex min-h-[130px] items-start gap-4 p-5 text-left transition hover:-translate-y-0.5 hover:shadow-[0_10px_30px_rgba(15,23,42,.07)] ${colors.border}`}>
            <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${colors.icon}`}><IconComponent size={21} strokeWidth={1.7} /></span>
            <span className="min-w-0 flex-1"><span className="block text-base font-semibold tracking-tight">{title}</span><span className="mt-1 block text-xs text-slate-500">{subtitle}</span><span className="mt-3 block truncate text-[11px] text-slate-400">{detail}</span><span className="mt-2 block text-[11px] font-medium text-slate-500">已登记 {classCounts?.[tone === 'blue' ? 'regular' : tone === 'teal' ? 'temporal' : 'multimodal'] ?? 0} 个</span></span>
            <ArrowRight size={16} className="mt-1 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-700" />
          </button>
        })}
      </section>

      <section className="grid grid-cols-[1.15fr_.85fr] gap-5">
        <div className="wb-surface p-5">
          <div className="flex items-start justify-between"><div><p className="wb-eyebrow">案例入口</p><h2 className="mt-1 text-base font-semibold">可直接打开的构建案例</h2></div><button type="button" onClick={() => navigate('/data')} className="text-xs text-slate-500 hover:text-slate-900">查看全部 <ArrowRight size={13} className="ml-1 inline" /></button></div>
          <div className="mt-5 grid grid-cols-3 gap-3">
            {cases.map(({ name, tag, detail, path, icon: IconComponent, tone }) => <button key={name} type="button" onClick={() => navigate(path)} className="group rounded-xl border border-slate-200 p-4 text-left transition hover:border-slate-400 hover:bg-slate-50"><div className="flex items-center justify-between"><span className={`flex h-8 w-8 items-center justify-center rounded-lg ${tone}`}><IconComponent size={16} strokeWidth={1.8} /></span><ArrowRight size={14} className="text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-700" /></div><p className="mt-4 text-sm font-semibold">{name}</p><p className="mt-1 text-[11px] text-slate-400">{tag}</p><p className="mt-3 text-xs leading-5 text-slate-500">{detail}</p></button>)}
          </div>
        </div>

        <div className="wb-surface p-5">
          <div className="flex items-start justify-between"><div><p className="wb-eyebrow">可视化能力</p><h2 className="mt-1 text-base font-semibold">构建后可以查看</h2></div><Network size={17} className="text-slate-300" /></div>
          <div className="mt-4 divide-y divide-slate-100">
            {viewTypes.map(({ title, detail, icon: IconComponent, path }) => <button key={title} type="button" onClick={() => navigate(path)} className="group flex w-full items-center gap-3 py-2.5 text-left"><span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-600"><IconComponent size={14} strokeWidth={1.8} /></span><span className="min-w-0 flex-1"><span className="block text-xs font-medium text-slate-700">{title}</span><span className="mt-0.5 block text-[11px] text-slate-400">{detail}</span></span><ArrowRight size={13} className="text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-700" /></button>)}
          </div>
        </div>
      </section>

      <section className="grid grid-cols-[1.15fr_.85fr] gap-5">
        <div className="wb-surface p-5">
          <div className="flex items-center justify-between"><div><p className="wb-eyebrow">最近本体</p><h2 className="mt-1 text-base font-semibold">最近更新</h2></div><button type="button" onClick={() => navigate('/ontologies')} className="text-xs text-slate-500 hover:text-slate-900">进入本体库 <ArrowRight size={13} className="ml-1 inline" /></button></div>
          {updated.length === 0 ? <div className="mt-5 flex items-center gap-3 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-500"><Database size={17} className="text-slate-400" />当前没有已保存的本体。</div> : <div className="mt-4 divide-y divide-slate-100">{updated.slice(0, 5).map(item => <button key={item.id} type="button" onClick={() => navigate(`/ontologies/${item.id}`)} className="flex w-full items-center gap-3 py-3 text-left hover:bg-slate-50"><span className="flex-1 truncate text-sm font-medium">{item.name}</span><span className="text-xs text-slate-400">{item.domain || '通用'}</span><StatusBadge status={item.status} /><ArrowRight size={13} className="text-slate-300" /></button>)}</div>}
        </div>
        <div className="wb-surface p-5"><div className="flex items-center justify-between"><div><p className="wb-eyebrow">运行状态</p><h2 className="mt-1 text-base font-semibold">当前任务</h2></div><span className="inline-flex items-center gap-1.5 text-xs text-emerald-700"><span className="h-2 w-2 rounded-full bg-emerald-500" />服务在线</span></div><div className="mt-5 grid grid-cols-3 gap-3"><div className="rounded-lg bg-slate-50 p-3"><p className="text-[11px] text-slate-500">数据集</p><p className="mt-2 text-lg font-semibold">{data.dataset_count ?? 0}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-[11px] text-slate-500">任务</p><p className="mt-2 text-lg font-semibold">{data.task_count ?? 0}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-[11px] text-slate-500">待审查</p><p className="mt-2 text-lg font-semibold">{data.review_count ?? 0}</p></div></div><button type="button" onClick={() => navigate('/models')} className="mt-4 flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5 text-xs text-slate-600 hover:border-slate-400 hover:text-slate-900">查看模型与审查 <ArrowRight size={13} /></button></div>
      </section>
    </div>
  )
}
