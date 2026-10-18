import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  BellRing,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FileSearch,
  GitCompareArrows,
  LayoutDashboard,
  LogOut,
  Network,
  Settings,
  Sparkles,
  Table2,
} from 'lucide-react'
import HelpFAQ from '@/components/HelpFAQ'
import { useAuthStore } from '@/stores/authStore'

interface SubItem { to: string; icon: React.ElementType; label: string; hint: string }
interface NavItem { to: string; icon: React.ElementType; label: string; subItems?: SubItem[] }
interface NavGroup { label: string; items: NavItem[] }

const LOCAL_SINGLE_USER = import.meta.env.VITE_AUTH_MODE === 'local_single_user'

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  // Submenus start collapsed and open for the section you are in; a manual toggle wins.
  const [toggled, setToggled] = useState<Record<string, boolean>>({})

  // Platform-level navigation, as in Foundry: home, the ontology library and the
  // applications that run on an ontology. Data is brought in inside an ontology
  // (数据 → 数据源), not from a separate global tool.
  const navGroups: NavGroup[] = [
    { label: '工作台', items: [
      { to: '/overview', icon: LayoutDashboard, label: '首页' },
      { to: '/ontologies', icon: Network, label: '本体库' },
    ] },
    { label: '应用', items: [
      { to: '/apps/objects', icon: Table2, label: '对象浏览器' },
      { to: '/apps/alerts', icon: BellRing, label: '告警中心' },
      { to: '/scenario-studies', icon: Sparkles, label: '情景研究' },
      { to: '/scenarios', icon: GitCompareArrows, label: '通用情景' },
    ] },
    { label: '系统', items: [
      { to: '/models', icon: FileSearch, label: '模型与审查' },
      { to: '/settings', icon: Settings, label: '设置' },
    ] },
  ]

  const isActive = (to: string) => {
    const path = to.split('?')[0]
    return location.pathname === path || location.pathname.startsWith(`${path}/`)
  }

  return (
    <div className="min-h-screen bg-[var(--workbench-canvas)]">
      <aside className={`fixed inset-y-0 left-0 z-30 flex flex-col bg-[var(--workbench-graphite)] text-white transition-[width] duration-200 ${collapsed ? 'w-[72px]' : 'w-[252px]'}`}>
        <div className={`flex h-[76px] items-center border-b border-white/10 ${collapsed ? 'justify-center px-3' : 'justify-between px-5'}`}>
          <Link to="/overview" className="flex min-w-0 items-center gap-3" aria-label="返回总览">
            <span className="pku-brand-mark" aria-hidden="true"><img src="/pku-seal.png" alt="" /></span>
            {!collapsed && <span className="truncate text-[15px] font-semibold tracking-[.02em]">本体构筑工作台</span>}
          </Link>
        </div>

        <nav className="workbench-scrollbar flex-1 overflow-y-auto px-3 py-5" aria-label="主导航">
          {navGroups.map(group => <div key={group.label} className="mb-4">
          <p className={`${collapsed ? 'text-center' : 'px-3'} mb-1.5 text-[10px] font-semibold uppercase tracking-[.18em] text-slate-400/80`}>{collapsed ? '·' : group.label}</p>
          <div className="space-y-1">
            {group.items.map(item => {
              const Icon = item.icon
              const groupActive = isActive(item.to) || (item.subItems?.some(sub => isActive(sub.to)) ?? false)
              const open = toggled[item.to] ?? groupActive
              if (!item.subItems) {
                return (
                  <Link key={item.to} to={item.to} title={collapsed ? item.label : undefined} className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${groupActive ? 'bg-[var(--pku-red)] text-white shadow-[0_6px_18px_rgba(139,26,43,.28)]' : 'text-slate-300 hover:bg-white/10 hover:text-white'}`}>
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
                    {!collapsed && <button type="button" onClick={() => setToggled(value => ({ ...value, [item.to]: !open }))} className="mr-2 rounded p-1 text-slate-400 hover:bg-white/10 hover:text-white" aria-expanded={open} aria-label={`展开${item.label}菜单`}><ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} /></button>}
                  </div>
                  {open && !collapsed && (
                    <div className="ml-4 mt-1 space-y-0.5 border-l border-white/15 pl-2">
                      {item.subItems.map(sub => {
                        const SubIcon = sub.icon
                        const active = location.pathname === sub.to.split('?')[0] || location.pathname.startsWith(`${sub.to.split('?')[0]}/`)
                        return <Link key={sub.to} to={sub.to} className={`flex items-center gap-2 rounded-md px-3 py-2 text-xs transition-colors ${active ? 'bg-white/15 text-white' : 'text-slate-400 hover:bg-white/10 hover:text-slate-200'}`}><SubIcon size={14} className="shrink-0" strokeWidth={1.8} /><span className="min-w-0"><span className="block">{sub.label}</span><span className="mt-0.5 block text-[10px] text-slate-500">{sub.hint}</span></span></Link>
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          </div>)}
        </nav>

        <div className="border-t border-white/10 p-3">
          {!collapsed && <div className="mb-2 flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-slate-300"><span className="h-2 w-2 rounded-full bg-emerald-400" />{LOCAL_SINGLE_USER ? '本地单用户模式' : '受保护工作区'}</div>}
          <button type="button" onClick={() => setCollapsed(value => !value)} className="flex w-full items-center justify-center rounded-lg py-2 text-slate-400 hover:bg-white/10 hover:text-white" aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>{collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}</button>
          {!LOCAL_SINGLE_USER && <button type="button" onClick={() => { logout(); navigate('/login') }} className={`mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-400 hover:bg-white/10 hover:text-white ${collapsed ? 'justify-center' : ''}`}><LogOut size={15} />{!collapsed && '退出登录'}</button>}
        </div>
      </aside>

      <main className={`min-h-screen min-w-0 transition-[padding] duration-200 ${collapsed ? 'pl-[72px]' : 'pl-[252px]'}`}>
        <div className="min-h-screen px-8 py-7 xl:px-10">{children}</div>
      </main>
      <HelpFAQ />
    </div>
  )
}
