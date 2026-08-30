import { ArrowRight, Database, GitBranch, Table2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

/**
 * A single landing page for the existing structured-data pipeline features.
 * The underlying pages remain available for compatibility; this page keeps
 * the product navigation from exposing three competing "data" entry points.
 */
export default function RegularDataPage() {
  const navigate = useNavigate()
  const links = [
    { title: '数据源与数据集', description: '连接数据库、上传 CSV/XLSX/JSON，并预览字段。', icon: Database, path: '/data/pipelines/datasets', color: 'text-blue-600 bg-blue-50' },
    { title: 'Pipeline 转换', description: '将原始数据清洗、转换为可复用的 Curated Dataset。', icon: GitBranch, path: '/data/pipelines', color: 'text-purple-600 bg-purple-50' },
    { title: 'Curated 数据', description: '查看转换结果、审核状态和映射入口。', icon: Table2, path: '/data/structured', color: 'text-emerald-600 bg-emerald-50' },
  ]
  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h2 className="text-xl font-semibold">常规数据</h2>
        <p className="text-sm text-gray-500 mt-1">管理表格和数据库数据，并通过现有 Pipeline 转换成本体可用的数据集。</p>
      </div>
      <div className="grid md:grid-cols-3 gap-4">
        {links.map(({ title, description, icon: Icon, path, color }) => (
          <button key={path} onClick={() => navigate(path)} className="text-left bg-white border rounded-xl p-5 hover:border-gray-400 hover:shadow-sm transition group">
            <span className={`inline-flex p-2 rounded-lg ${color}`}><Icon size={18} /></span>
            <h3 className="font-medium mt-4">{title}</h3>
            <p className="text-xs text-gray-500 mt-2 leading-5">{description}</p>
            <span className="mt-4 inline-flex items-center gap-1 text-xs text-gray-500 group-hover:text-black">进入 <ArrowRight size={13} /></span>
          </button>
        ))}
      </div>
      <div className="border rounded-xl bg-gray-50 p-4 text-xs text-gray-600 leading-5">
        常规数据页面复用已有 Dataset、Pipeline、Curated 后端。时序数据请进入“数据管理 → 时序数据”，不要在这里重复创建专用 Pipeline。
      </div>
    </div>
  )
}
