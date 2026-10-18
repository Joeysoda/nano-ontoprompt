import { useNavigate } from 'react-router-dom';
import { Activity, ArrowRight, Images, Radio, Table2 } from 'lucide-react';
import { DEFAULT_DYNAMIC_RUN_PATH } from '@/config/demoRoutes';

type DataClass = 'regular' | 'temporal' | 'multimodal' | undefined;

/** Bring data into this ontology. Every import wizard opens already targeted at it. */
export default function SourcesTab({ ontologyId, dataClass, onOpen }: { ontologyId: string; dataClass: DataClass; onOpen: (tab: string) => void }) {
  const navigate = useNavigate();
  const sources = [
    { key: 'temporal', title: '时序数据', icon: Activity, fits: dataClass === 'temporal',
      description: '按时间顺序的事件或传感器记录（CSV/表格），构建随时间演化的对象、关系和状态。',
      action: () => navigate(`/data/temporal/build?ontology_id=${encodeURIComponent(ontologyId)}`) },
    { key: 'multimodal', title: '多模态数据', icon: Images, fits: dataClass === 'multimodal',
      description: '图片、点云与标注一起导入，生成对应的对象和数据模型。只能追加到多模态本体。',
      action: () => navigate(`/data/multimodal/build?ontology_id=${encodeURIComponent(ontologyId)}`) },
    { key: 'business', title: '业务数据', icon: Table2, fits: dataClass !== 'temporal' && dataClass !== 'multimodal',
      description: '查看已经映射到本体的业务数据集（订单、物料、资源等）。',
      action: () => onOpen('manufacturing') },
    { key: 'dynamic', title: '动态数据演示', icon: Radio, fits: false,
      description: 'FactoryNet 逐事件实时演示。它会使用自己的演示本体，不写入当前本体。',
      action: () => navigate(DEFAULT_DYNAMIC_RUN_PATH) },
  ];
  return <section aria-label="数据源" className="space-y-4">
    <div>
      <h2 className="text-base font-semibold text-slate-900">数据源</h2>
      <p className="text-xs text-slate-500">选择数据类型导入到这个本体；导入完成后可在“资源 → 数据模型”和“应用 → 对象浏览器”里查看。</p>
    </div>
    <ul className="grid gap-3 md:grid-cols-2">
      {sources.map(source => <li key={source.key}>
        <button type="button" onClick={source.action} className={`wb-surface flex h-full w-full items-start gap-3 p-4 text-left hover:border-slate-400 ${source.fits ? 'border-slate-400' : ''}`}>
          <source.icon size={18} className="mt-0.5 shrink-0 text-slate-500" />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 text-sm font-medium text-slate-900">{source.title}
              {source.fits && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">适合当前本体</span>}</span>
            <span className="mt-1 block text-xs leading-5 text-slate-500">{source.description}</span>
          </span>
          <ArrowRight size={15} className="mt-0.5 shrink-0 text-slate-400" />
        </button>
      </li>)}
    </ul>
  </section>;
}
