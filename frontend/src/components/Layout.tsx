import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSearch,
  Images,
  LayoutDashboard,
  LogOut,
  Network,
  Settings,
  Table2,
} from 'lucide-react'
import HelpFAQ from '@/components/HelpFAQ'
import { useAuthStore } from '@/stores/authStore'

interface SubItem { to: string; icon: React.ElementType; label: string; hint: string }
interface NavItem { to: string; icon: React.ElementType; label: string; subItems?: SubItem[] }

const LOCAL_SINGLE_USER = import.meta.env.VITE_AUTH_MODE === 'local_single_user'

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [dataOpen, setDataOpen] = useState(true)

  const navItems: NavItem[] = [
    { to: '/overview', icon: LayoutDashboard, label: '总览' },
    {
      to: '/data', icon: Database, label: '数据构筑',
      subItems: [
        { to: '/data/regular', icon: Table2, label: '常规数据', hint: '表格与数据库' },
        { to: '/data/temporal', icon: Activity, label: '时序数据', hint: '序列与时间轴' },
        { to: '/data/multimodal', icon: Images, label: '多模态数据', hint: '图像、深度与点云' },
      ],
    },
    { to: '/ontologies', icon: Network, label: '本体库' },
    { to: '/models', icon: FileSearch, label: '模型与审查' },
    { to: '/settings', icon: Settings, label: '设置' },
  ]

  const isActive = (to: string) => location.pathname === to || location.pathname.startsWith(`${to}/`)

  return (
    <div className="min-h-screen bg-[var(--workbench-canvas)]">
      <aside className={`fixed inset-y-0 left-0 z-30 flex flex-col bg-[var(--workbench-graphite)] text-white transition-[width] duration-200 ${collapsed ? 'w-[72px]' : 'w-[252px]'}`}>
        <div className={`flex h-[76px] items-center border-b border-white/10 ${collapsed ? 'justify-center px-3' : 'justify-between px-5'}`}>
          <Link to="/overview" className="flex min-w-0 items-center gap-3" aria-label="返回总览">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/20 bg-white/10 text-sm font-semibold tracking-tight">本</span>
            {!collapsed && <span className="truncate text-[15px] font-semibold tracking-[.02em]">本体构筑工作台</span>}
          </Link>
        </div>

        <nav className="workbench-scrollbar flex-1 overflow-y-auto px-3 py-5" aria-label="主导航">
          <p className={`${collapsed ? 'text-center' : 'px-3'} mb-3 text-[10px] font-semibold uppercase tracking-[.18em] text-slate-400/80`}>{collapsed ? '·' : '工作区'}</p>
          <div className="space-y-1">
            {navItems.map(item => {
              const Icon = item.icon
              const groupActive = isActive(item.to) || (item.subItems?.some(sub => isActive(sub.to)) ?? false)
              if (!item.subItems) {
                return (
                  <Link key={item.to} to={item.to} title={collapsed ? item.label : undefined} className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${groupActive ? 'bg-white text-[var(--workbench-graphite)] shadow-sm' : 'text-slate-300 hover:bg-white/10 hover:text-white'}`}>
                    <Icon size={17} className="shrink-0" strokeWidth={1.8} />
                    {!collapsed && <span>{item.label}</span>}
                  </Link>
                )
              }
              return (
                <div key={item.to}>
                  <div className={`flex items-center rounded-lg transition-colors ${groupActive ? 'bg-white/10 text-white' : 'text-slate-300 hover:bg-white/10 hover:text-white'}`}>
                    <Link to={item.to} title={collapsed ? item.label : undefined} className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 text-sm">
                      <Icon size={17} className="shrink-0" strokeWidth={1.8} />
                      {!collapsed && <span>{item.label}</span>}
                    </Link>
                    {!collapsed && <button type="button" onClick={() => setDataOpen(value => !value)} className="mr-2 rounded p-1 text-slate-400 hover:bg-white/10 hover:text-white" aria-label="展开数据构筑菜单"><ChevronDown size={14} className={`transition-transform ${dataOpen ? 'rotate-180' : ''}`} /></button>}
                  </div>
                  {dataOpen && !collapsed && (
                    <div className="ml-4 mt-1 space-y-0.5 border-l border-white/15 pl-2">
                      {item.subItems.map(sub => {
                        const SubIcon = sub.icon
                        const active = isActive(sub.to)
                        return <Link key={sub.to} to={sub.to} className={`flex items-center gap-2 rounded-md px-3 py-2 text-xs transition-colors ${active ? 'bg-white/15 text-white' : 'text-slate-400 hover:bg-white/10 hover:text-slate-200'}`}><SubIcon size={14} className="shrink-0" strokeWidth={1.8} /><span className="min-w-0"><span className="block">{sub.label}</span><span className="mt-0.5 block text-[10px] text-slate-500">{sub.hint}</span></span></Link>
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </nav>

        <div className="border-t border-white/10 p-3">
          {!collapsed && <div className="mb-2 flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-slate-300"><span className="h-2 w-2 rounded-full bg-emerald-400" />{LOCAL_SINGLE_USER ? '本地单用户模式' : '受保护工作区'}</div>}
          <button type="button" onClick={() => setCollapsed(value => !value)} className="flex w-full items-center justify-center rounded-lg py-2 text-slate-400 hover:bg-white/10 hover:text-white" aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>{collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}</button>
          {!LOCAL_SINGLE_USER && <button type="button" onClick={() => { logout(); navigate('/login') }} className={`mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-400 hover:bg-white/10 hover:text-white ${collapsed ? 'justify-center' : ''}`}><LogOut size={15} />{!collapsed && '退出登录'}</button>}
        </div>
      </aside>

      <main className={`min-h-screen transition-[padding] duration-200 ${collapsed ? 'pl-[72px]' : 'pl-[252px]'}`}>
        <div className="min-h-screen px-8 py-7 xl:px-10">{children}</div>
      </main>
      <HelpFAQ />
    </div>
  )
}
