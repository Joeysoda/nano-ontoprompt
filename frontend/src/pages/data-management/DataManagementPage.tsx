import { useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, Images, Radio } from 'lucide-react'
import { DEFAULT_DYNAMIC_RUN_PATH } from '@/config/demoRoutes'

const tools = [
  {
    title: '时序数据构建',
    detail: '从原始时序数据开始，创建并构建本体。',
    action: '开始构建',
    icon: Activity,
    path: '/data/temporal',
    tone: 'text-teal-700 bg-teal-50',
  },
  {
    title: '多模态数据构建',
    detail: '从原始图像、深度、掩码或点云数据开始创建。',
    action: '开始构建',
    icon: Images,
    path: '/data/multimodal',
    tone: 'text-violet-700 bg-violet-50',
  },
  {
    title: '动态数据构建',
    detail: '打开动态数据工作台，查看逐事件实时接收。',
    action: '开始演示',
    icon: Radio,
    path: DEFAULT_DYNAMIC_RUN_PATH,
    tone: 'text-amber-700 bg-amber-50',
  },
]

export default function DataManagementPage() {
  const navigate = useNavigate()

  return (
    <div className="wb-page max-w-[1100px]">
      <div className="wb-page-header">
        <div>
          <p className="wb-eyebrow">数据构筑</p>
          <h1 className="wb-page-title mt-2">选择数据构建方式</h1>
          <p className="wb-page-subtitle">从原始数据开始创建本体，或打开动态数据工作台。</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {tools.map(({ title, detail, action, icon: Icon, path, tone }) => (
          <button
            key={title}
            type="button"
            onClick={() => navigate(path)}
            className="wb-choice-card flex min-h-40 items-start gap-3 text-left hover:border-gray-400"
          >
            <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${tone}`}>
              <Icon size={18} />
            </span>
            <span>
              <strong className="text-sm">{title}</strong>
              <span className="mt-2 block text-xs leading-5 text-gray-500">{detail}</span>
              <span className="mt-4 inline-flex items-center gap-1 text-xs text-gray-500">
                {action} <ArrowRight size={12} />
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
