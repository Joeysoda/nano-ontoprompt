import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, LogOut, Network } from 'lucide-react'
import HelpFAQ from '@/components/HelpFAQ'
import { ontologyApi } from '@/api/ontologies'
import { apiClientV2 } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import {
  NAV_GROUPS,
  entryHref,
  isEntryActive,
  ontologyIdFromUrl,
  readStoredOntology,
  urlForOntologySwitch,
  writeStoredOntology,
} from '@/components/navigation'

const LOCAL_SINGLE_USER = import.meta.env.VITE_AUTH_MODE === 'local_single_user'

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)

  // Current ontology: the one the URL is about, else the last one chosen.
  const fromUrl = ontologyIdFromUrl(location.pathname, location.search)
  const [stored, setStored] = useState(readStoredOntology)
  if (fromUrl && fromUrl !== stored) setStored(fromUrl)
  useEffect(() => { writeStoredOntology(stored) }, [stored])
  const ontologies = useQuery({ queryKey: ['ontologies', 'switcher'], queryFn: () => ontologyApi.list({ page_size: 200 }) })
  const items = ontologies.data?.items ?? []
  const current = items.find(item => item.id === stored) ?? null
  const currentId = current?.id ?? (ontologies.data ? null : stored)
  const openAlerts = useQuery({
    queryKey: ['alerts', currentId, 'open-count'],
    enabled: !!currentId,
    queryFn: () => apiClientV2.get<{ total: number }>(`/ontologies/${currentId}/alerts?status=open&limit=1`),
  })
  const alertCount = openAlerts.data?.total ?? 0
  // On short screens the active entry can sit below the fold; keep it visible.
  const navRef = useRef<HTMLElement>(null)
  useEffect(() => {
    navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest' })
  }, [location.pathname, location.search])

  const switchOntology = (nextId: string) => {
    setStored(nextId || null)
    if (!nextId) return
    const next = urlForOntologySwitch(location.pathname, location.search, nextId)
    if (next) navigate(next)
  }

  return (
    <div className="min-h-screen bg-[var(--workbench-canvas)]">
      <aside className={`fixed inset-y-0 left-0 z-30 flex flex-col bg-[var(--workbench-graphite)] text-white transition-[width] duration-200 ${collapsed ? 'w-[72px]' : 'w-[232px]'}`}>
        <div className={`flex h-[60px] shrink-0 items-center border-b border-white/10 ${collapsed ? 'justify-center px-3' : 'justify-between px-5'}`}>
          <Link to="/overview" className="flex min-w-0 items-center gap-3" aria-label="返回首页">
            <span className="pku-brand-mark" aria-hidden="true"><img src="/pku-seal.png" alt="" /></span>
            {!collapsed && <span className="truncate text-[15px] font-semibold tracking-[.02em]">本体构筑工作台</span>}
          </Link>
        </div>

        <nav ref={navRef} className="workbench-scrollbar flex-1 overflow-y-auto px-3 py-3" aria-label="主导航">
          {NAV_GROUPS.map(group => {
            const entries = group.entries.filter(entry => !entry.temporalOnly || current?.data_class === 'temporal')
            if (!entries.length) return null
            return <div key={group.label || 'start'} className="mb-2.5">
              {group.label && <p className={`${collapsed ? 'text-center' : 'px-3'} mb-0.5 text-[10px] font-semibold tracking-[.14em] text-slate-400/80`}>{collapsed ? '·' : group.label}</p>}
              <div className="space-y-px">
                {entries.map(entry => {
                  const Icon = entry.icon
                  const active = isEntryActive(entry, location.pathname, location.search)
                  const badge = entry.key === 'alerts' && alertCount > 0 ? alertCount : null
                  const needsPick = !entry.global && !currentId
                  return (
                    <Link key={entry.key} to={entryHref(entry, currentId)} aria-current={active ? 'page' : undefined}
                      title={collapsed ? entry.label : needsPick ? '先选择一个本体' : entry.hint}
                      className={`flex items-center gap-3 rounded-md px-3 py-1.5 text-[13px] transition-colors ${active ? 'bg-[var(--pku-red)] text-white shadow-[0_6px_18px_rgba(139,26,43,.28)]' : 'text-slate-300 hover:bg-white/10 hover:text-white'}`}>
                      <Icon size={16} className="shrink-0" strokeWidth={1.8} />
                      {!collapsed && <span className="min-w-0 flex-1 truncate">{entry.label}</span>}
                      {!collapsed && badge !== null && <span aria-label={`${badge} 条待处理告警`} className={`rounded-full px-1.5 text-[10px] font-semibold ${active ? 'bg-white text-slate-900' : 'bg-red-600 text-white'}`}>{badge}</span>}
                    </Link>
                  )
                })}
              </div>
            </div>
          })}
        </nav>

        <div className="shrink-0 border-t border-white/10 p-3">
          {!collapsed && <div className="mb-2 flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-slate-300"><span className="h-2 w-2 rounded-full bg-emerald-400" />{LOCAL_SINGLE_USER ? '本地单用户模式' : '受保护工作区'}</div>}
          <button type="button" onClick={() => setCollapsed(value => !value)} className="flex w-full items-center justify-center rounded-lg py-1.5 text-slate-400 hover:bg-white/10 hover:text-white" aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>{collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}</button>
          {!LOCAL_SINGLE_USER && <button type="button" onClick={() => { logout(); navigate('/login') }} className={`mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-400 hover:bg-white/10 hover:text-white ${collapsed ? 'justify-center' : ''}`}><LogOut size={15} />{!collapsed && '退出登录'}</button>}
        </div>
      </aside>

      <main className={`min-h-screen min-w-0 transition-[padding] duration-200 ${collapsed ? 'pl-[72px]' : 'pl-[232px]'}`}>
        <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-slate-200 bg-white/95 px-8 backdrop-blur xl:px-10" aria-label="本体选择栏">
          <Network size={16} className="shrink-0 text-slate-400" />
          <label htmlFor="current-ontology" className="shrink-0 text-xs text-slate-500">当前本体</label>
          <select id="current-ontology" value={currentId ?? ''} onChange={event => switchOntology(event.target.value)}
            className="min-w-0 max-w-[340px] flex-1 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-800 focus:border-slate-500 focus:outline-none">
            <option value="">请选择本体…</option>
            {items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          {current && <span className="hidden truncate text-xs text-slate-400 lg:inline">{current.entity_count} 个对象类型 · {current.relation_count} 个关系</span>}
          {ontologies.data && !current && <span className="truncate text-xs text-amber-700">左侧多数功能都在某个本体里工作，请先选一个。</span>}
          {ontologies.error && <span role="alert" className="truncate text-xs text-red-700">本体目录读取失败</span>}
          <Link to="/ontologies" className="ml-auto shrink-0 text-xs text-slate-500 hover:text-slate-900">管理本体库</Link>
        </header>
        <div className="px-8 py-6 xl:px-10">{children}</div>
      </main>
      <HelpFAQ />
    </div>
  )
}
