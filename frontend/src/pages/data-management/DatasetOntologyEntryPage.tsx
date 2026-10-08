import { useEffect, useMemo } from 'react'
import { AlertTriangle, ArrowRight, BookOpen, Database, Loader2, Plus } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ontologyApi } from '@/api/ontologies'
import type { OntologyListItem } from '@/types/ontology'

type DataClass = 'regular' | 'temporal' | 'multimodal' | 'dynamic'

const CONFIG: Record<DataClass, {
  title: string
  description: string
  keywords: string[]
  buildPath: string
  tab: 'graph' | 'dynamic'
  icon: typeof Database
}> = {
  regular: {
    title: '常规数据',
    description: '打开后直接查看当前数据来源对应的本体。',
    keywords: ['c-mapss', 'cmapss', 'cmapps', 'frepple', '供应链'],
    buildPath: '/data/regular/build',
    tab: 'graph',
    icon: Database,
  },
  temporal: {
    title: '时序数据',
    description: '打开后直接查看 FactoryNet CNC 本体及数据模型。',
    keywords: ['factorynet', 'factory net', 'cnc'],
    buildPath: '/data/temporal/build',
    tab: 'graph',
    icon: BookOpen,
  },
  dynamic: {
    title: '动态数据构建',
    description: '打开 FactoryNet CNC 本体，并可从“动态演化”继续进行逐事件演示。',
    keywords: ['factorynet', 'factory net', 'cnc'],
    buildPath: '/data/dynamic/build',
    tab: 'graph',
    icon: BookOpen,
  },
  multimodal: {
    title: '多模态数据',
    description: '打开后直接查看 I-BADAS 多模态本体及样例证据。',
    keywords: ['i-badas', 'ibadas'],
    buildPath: '/data/multimodal/build',
    tab: 'graph',
    icon: BookOpen,
  },
}

function scoreOntology(item: OntologyListItem, config: typeof CONFIG[DataClass]) {
  const text = `${item.name} ${item.domain || ''}`.toLowerCase()
  let score = 0
  config.keywords.forEach((keyword, index) => {
    if (text.includes(keyword)) score += 220 - index * 15
  })
  if (String(item.status) === 'published') score += 25
  if (item.status === 'draft') score -= 160
  if ((item.entity_count || 0) + (item.relation_count || 0) === 0) score -= 100
  if (item.name.includes('私密')) score -= 20
  return score
}

function chooseOntology(items: OntologyListItem[], dataClass: DataClass, requestedId?: string | null) {
  const config = CONFIG[dataClass]
  const usable = items.filter(item => item.data_class === (dataClass === 'dynamic' ? 'temporal' : dataClass) && String(item.status) !== 'draft' && ((item.entity_count || 0) + (item.relation_count || 0) > 0))
  if (requestedId) {
    const requested = usable.find(item => item.id === requestedId)
    if (requested) return requested
  }
  return [...usable].sort((a, b) => {
    const scoreDelta = scoreOntology(b, config) - scoreOntology(a, config)
    if (scoreDelta) return scoreDelta
    return new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime()
  })[0]
}

export default function DatasetOntologyEntryPage({ dataClass }: { dataClass: DataClass }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const config = CONFIG[dataClass]
  const Icon = config.icon
  const requestedId = searchParams.get('ontology_id')
  const { data, isLoading, error } = useQuery({
    queryKey: ['data-entry-ontologies', dataClass],
    queryFn: () => ontologyApi.list({ page_size: 1000 }),
    staleTime: 15_000,
  })
  const items = useMemo(() => data?.items || [], [data?.items])
  const selected = useMemo(() => chooseOntology(items, dataClass, requestedId), [items, dataClass, requestedId])
  const matching = useMemo(
    () => items
      .filter(item => item.data_class === (dataClass === 'dynamic' ? 'temporal' : dataClass) && String(item.status) !== 'draft' && ((item.entity_count || 0) + (item.relation_count || 0) > 0))
      .sort((a, b) => scoreOntology(b, config) - scoreOntology(a, config)),
    [items, dataClass, config],
  )

  useEffect(() => {
    if (!selected) return
    const target = `/ontologies/${selected.id}?tab=${config.tab}&from=data-${dataClass}`
    navigate(target, { replace: true })
  }, [config.tab, dataClass, navigate, selected])

  if (isLoading) {
    return <div className="wb-page flex min-h-[420px] items-center justify-center"><div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 size={16} className="animate-spin" />正在打开本体</div></div>
  }

  if (error) {
    return <div className="wb-page max-w-[900px]"><div className="wb-surface flex items-start gap-3 border-red-200 bg-red-50 p-5 text-sm text-red-700"><AlertTriangle size={18} className="mt-0.5 shrink-0" /><div><p className="font-medium">本体目录暂时无法读取</p><p className="mt-1 text-xs">请确认后端已启动，然后重试。</p><button type="button" className="mt-3 wb-button-secondary" onClick={() => window.location.reload()}>重新加载</button></div></div></div>
  }

  return (
    <div className="wb-page max-w-[1100px]">
      <div className="wb-page-header">
        <div>
          <p className="wb-eyebrow">数据工具</p>
          <h1 className="wb-page-title mt-2 flex items-center gap-2"><Icon size={23} className="text-slate-500" />{config.title}</h1>
          <p className="wb-page-subtitle">{config.description}</p>
        </div>
        <button type="button" className="wb-button-secondary" onClick={() => navigate(config.buildPath)}><Plus size={14} />进入构建</button>
      </div>
      <div className="wb-surface mt-5 border-dashed p-8 text-center">
        <BookOpen size={26} className="mx-auto text-slate-400" />
        <h2 className="mt-3 text-base font-semibold text-slate-800">没有可直接查看的本体</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">当前数据分类还没有完成构建的本体。可以先导入数据并完成构建，完成后这里会直接打开查看页面。</p>
        <button type="button" className="wb-button-primary mt-5" onClick={() => navigate(config.buildPath)}>进入构建向导<ArrowRight size={14} /></button>
      </div>
      {matching.length > 0 && <div className="mt-5 wb-surface p-5"><div className="mb-3 flex items-center justify-between"><div><p className="wb-section-kicker">已有内容</p><h2 className="wb-section-title mt-1">选择一个本体打开</h2></div><span className="text-xs text-slate-400">{matching.length} 个</span></div><div className="space-y-2">{matching.slice(0, 8).map(item => <button key={item.id} type="button" onClick={() => navigate(`/ontologies/${item.id}?tab=${config.tab}&from=data-${dataClass}`)} className="flex w-full items-center gap-3 rounded-lg border border-slate-200 px-3 py-3 text-left hover:border-slate-400"><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-800">{item.name}</span><span className="mt-1 block text-xs text-slate-500">{item.entity_count || 0} 个实体类型 · {item.relation_count || 0} 条关系</span></span><ArrowRight size={15} className="text-slate-400" /></button>)}</div></div>}
    </div>
  )
}
