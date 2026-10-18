import { useQuery } from '@tanstack/react-query';
import { ArrowRight, BellRing, Boxes, Database, ListChecks } from 'lucide-react';
import { apiClientV2 } from '@/api/client';

type Catalog = { types: Array<{ kind: string; api_name: string; properties: Array<{ api_name: string }> }>; links: Array<{ api_name: string }> };

/** Ontology landing page (Ontology Manager "Discover"): what is here and where to go next. */
export default function OverviewTab({ ontologyId, onOpen }: { ontologyId: string; onOpen: (tab: string, extra?: string) => void }) {
  const catalog = useQuery({ queryKey: ['explorer-catalog', ontologyId], queryFn: () => apiClientV2.get<Catalog>(`/ontologies/${ontologyId}/object-query/catalog`) });
  const rules = useQuery({ queryKey: ['rules', ontologyId], queryFn: () => apiClientV2.get<{ rules: unknown[] }>(`/ontologies/${ontologyId}/rules`) });
  const alerts = useQuery({ queryKey: ['alerts', ontologyId, 'open-count'], queryFn: () => apiClientV2.get<{ total: number }>(`/ontologies/${ontologyId}/alerts?status=open&limit=1`) });
  const types = (catalog.data?.types ?? []).filter(type => type.kind === 'object');
  const visibleProps = (type: Catalog['types'][number]) => type.properties.filter(p => !p.api_name.startsWith('_')).length;
  const stats = [
    { label: '对象类型', value: types.length, icon: Boxes, tab: 'entities' },
    { label: '链接类型', value: catalog.data?.links.length ?? 0, icon: Database, tab: 'graph' },
    { label: '运行规则', value: rules.data?.rules.length ?? 0, icon: ListChecks, tab: 'rules' },
    { label: '待处理告警', value: alerts.data?.total ?? 0, icon: BellRing, tab: 'alerts' },
  ];
  return <section aria-label="本体总览" className="space-y-6">
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map(stat => <li key={stat.label}>
        <button type="button" onClick={() => onOpen(stat.tab)} className="wb-surface flex w-full items-center gap-3 p-4 text-left hover:border-slate-400">
          <stat.icon size={18} className="text-slate-400" />
          <span><span className="block text-2xl font-semibold text-slate-900">{stat.value}</span><span className="text-xs text-slate-500">{stat.label}</span></span>
        </button>
      </li>)}
    </ul>

    {types.length === 0 && !catalog.isPending ? <div className="wb-surface border-dashed p-8 text-center">
      <p className="text-sm font-medium text-slate-800">这个本体还没有对象类型和数据</p>
      <p className="mt-1 text-xs text-slate-500">先导入数据，或在“资源”里定义对象类型。</p>
      <button type="button" onClick={() => onOpen('sources')} className="wb-button-primary mt-4">添加数据<ArrowRight size={14} /></button>
    </div> : <div>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">对象类型</h2>
        <button type="button" onClick={() => onOpen('entities')} className="text-xs text-blue-700">查看全部</button>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {types.slice(0, 9).map(type => <li key={type.api_name}>
          <button type="button" onClick={() => onOpen('objects', `objectType=${encodeURIComponent(type.api_name)}`)}
            className="wb-surface flex w-full items-center gap-3 p-4 text-left hover:border-slate-400">
            <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded bg-[#2d8c7d] text-sm font-bold text-white">{type.api_name.slice(0, 1).toUpperCase()}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-900">{type.api_name}</span>
              <span className="text-xs text-slate-500">{visibleProps(type)} 个属性 · 在对象浏览器中打开</span></span>
          </button>
        </li>)}
      </ul>
    </div>}
  </section>;
}
