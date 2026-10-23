import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, useParams } from 'react-router-dom';
import { ArrowRight, Network } from 'lucide-react';
import { ontologyApi } from '@/api/ontologies';
import { NAV_ENTRIES, entryHref } from '@/components/navigation';

/** Opened from the sidebar when no current ontology is chosen: pick one, then land on the feature. */
export default function AppLauncherPage() {
  const { app } = useParams<{ app: string }>();
  const ontologies = useQuery({ queryKey: ['ontologies', 'launcher'], queryFn: () => ontologyApi.list({ page_size: 200 }) });
  const entry = NAV_ENTRIES.find(item => item.key === app && !item.global);
  if (!entry) return <Navigate to="/overview" replace />;
  const Icon = entry.icon;
  const items = (ontologies.data?.items ?? []).filter(item => !entry.temporalOnly || item.data_class === 'temporal');
  return <div className="wb-page max-w-[1100px]">
    <div className="wb-page-header">
      <div>
        <p className="wb-eyebrow">先选择本体</p>
        <h1 className="wb-page-title mt-2 flex items-center gap-2"><Icon size={22} className="text-slate-500" />{entry.label}</h1>
        <p className="wb-page-subtitle">{entry.hint ? `${entry.hint}。` : ''}这个功能在某个本体里工作，选一个本体打开（之后也可以在顶部“当前本体”里切换）：</p>
      </div>
    </div>
    {ontologies.isPending && <p role="status" className="mt-6 text-sm text-slate-500">正在加载本体…</p>}
    {ontologies.error && <p role="alert" className="mt-6 text-sm text-red-700">本体目录暂时无法读取，请重试。</p>}
    {ontologies.data && items.length === 0 && <div className="wb-surface mt-6 p-8 text-center text-sm text-slate-500">
      还没有可用的本体。<Link to="/ontologies/new" className="text-blue-700 underline">新建本体</Link>
    </div>}
    <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="选择本体">
      {items.map(item => <li key={item.id}>
        <Link to={entryHref(entry, item.id)} className="wb-surface flex h-full items-start gap-3 p-4 transition-colors hover:border-slate-400">
          <Network size={18} className="mt-0.5 shrink-0 text-slate-400" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-slate-900">{item.name}</span>
            <span className="mt-1 block text-xs text-slate-500">{item.domain || '未分类'} · {item.entity_count} 个类型 · {item.relation_count} 个关系</span>
          </span>
          <ArrowRight size={15} className="mt-0.5 shrink-0 text-slate-400" />
        </Link>
      </li>)}
    </ul>
  </div>;
}
