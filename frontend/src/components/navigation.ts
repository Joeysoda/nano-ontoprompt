import {
  BellRing,
  Bot,
  Boxes,
  Brain,
  Database,
  FileSearch,
  GitCompareArrows,
  LayoutDashboard,
  LayoutGrid,
  Library,
  ListChecks,
  Network,
  Route,
  Settings,
  ShieldCheck,
  Shapes,
  Sparkles,
  SquareFunction,
  Table2,
  Timer,
  Workflow,
} from 'lucide-react'

/**
 * Primary navigation, grouped by what the user is doing (see
 * docs/adr/0001-flat-task-navigation.md). Every ontology-scoped entry still
 * opens an existing page of the current ontology; only the entry point moved
 * from the ontology page's second-level nav to this sidebar.
 */
export interface NavEntry {
  key: string
  label: string
  icon: React.ElementType
  hint?: string
  /** Opens /ontologies/:id?tab=<tab>. */
  tab?: string
  /** Opens an ontology-scoped page outside the ontology detail page. */
  path?: (ontologyId: string) => string
  /** Pathnames (regex) that count as this entry being active. */
  activeOn?: RegExp
  /** Platform-level page that needs no ontology. */
  global?: string
  /** Only shown for temporal ontologies. */
  temporalOnly?: boolean
}

export interface NavGroup { label: string; entries: NavEntry[] }

export const NAV_GROUPS: NavGroup[] = [
  { label: '', entries: [
    { key: 'home', label: '首页', icon: LayoutDashboard, global: '/overview' },
    { key: 'library', label: '本体库', icon: Library, global: '/ontologies', hint: '新建、导入和管理本体' },
  ] },
  { label: '数据接入', entries: [
    { key: 'sources', label: '数据源', icon: Database, tab: 'sources', hint: '把数据导入当前本体' },
    { key: 'manufacturing', label: '业务数据', icon: Table2, tab: 'manufacturing' },
    { key: 'dynamic', label: '动态演化（实验）', icon: Timer, tab: 'dynamic', temporalOnly: true },
  ] },
  { label: '本体建模', entries: [
    { key: 'overview', label: '本体总览', icon: LayoutGrid, tab: 'overview' },
    { key: 'entities', label: '对象类型', icon: Shapes, tab: 'entities' },
    { key: 'data_model', label: '数据模型', icon: Boxes, tab: 'data_model' },
    { key: 'logic', label: '逻辑规则', icon: Workflow, tab: 'logic' },
    { key: 'logic-assets', label: '函数', icon: SquareFunction, tab: 'logic-assets' },
    { key: 'audit', label: '质量审查', icon: ShieldCheck, tab: 'audit' },
  ] },
  { label: '探索', entries: [
    { key: 'objects', label: '对象浏览器', icon: Table2, tab: 'objects', hint: '查找、筛选对象并执行操作' },
    { key: 'graph', label: '关系图', icon: Network, tab: 'graph', hint: '对象类型之间的链接' },
  ] },
  { label: '推演', entries: [
    { key: 'scenario-studies', label: '情景研究', icon: Sparkles, path: id => `/ontologies/${id}/scenario-studies`, activeOn: /^\/(ontologies\/[^/]+\/)?scenario-studies/, hint: '版本化的假设推演与已保存案例' },
    { key: 'scenarios', label: '情景沙箱', icon: GitCompareArrows, path: id => `/ontologies/${id}/scenarios`, activeOn: /^\/(ontologies\/[^/]+\/)?scenarios$/, hint: '在隔离副本上试改数据，再决定是否合并' },
    { key: 'decisions', label: '决策与影响链', icon: Route, tab: 'decisions' },
    { key: 'reasoning', label: '推理验证', icon: Brain, tab: 'reasoning' },
  ] },
  { label: '应用', entries: [
    { key: 'workshop', label: 'Workshop 应用', icon: LayoutDashboard, path: id => `/apps/workshop?ontology=${id}`, activeOn: /^\/apps\/workshop/, hint: '用组件搭出的业务看板' },
  ] },
  { label: '告警', entries: [
    { key: 'alerts', label: '告警中心', icon: BellRing, tab: 'alerts', hint: '规则命中的异常' },
    { key: 'rules', label: '运行规则', icon: ListChecks, tab: 'rules', hint: '定时检查与告警' },
  ] },
  { label: '智能体', entries: [
    { key: 'agent', label: '智能体决策', icon: Bot, tab: 'agent' },
  ] },
  { label: '系统管理', entries: [
    { key: 'models', label: '模型与审查', icon: FileSearch, global: '/models' },
    { key: 'settings', label: '设置', icon: Settings, global: '/settings' },
  ] },
]

export const NAV_ENTRIES = NAV_GROUPS.flatMap(group => group.entries)

/** Where an entry goes. Without a current ontology, ontology-scoped entries open a picker. */
export function entryHref(entry: NavEntry, ontologyId: string | null): string {
  if (entry.global) return entry.global
  if (!ontologyId) return entry.key === 'workshop' ? '/apps/workshop' : `/apps/${entry.key}`
  if (entry.path) return entry.path(ontologyId)
  return `/ontologies/${ontologyId}?tab=${entry.tab}`
}

const DETAIL_PAGE = /^\/ontologies\/(?!new$)[^/]+$/

export function isEntryActive(entry: NavEntry, pathname: string, search: string): boolean {
  if (pathname === `/apps/${entry.key}`) return true
  if (entry.global) return entry.global === '/ontologies' ? pathname === '/ontologies' || pathname === '/ontologies/new' : pathname === entry.global || pathname.startsWith(`${entry.global}/`)
  if (entry.activeOn) return entry.activeOn.test(pathname)
  if (!DETAIL_PAGE.test(pathname)) return false
  return (new URLSearchParams(search).get('tab') || 'overview') === entry.tab
}

/** The ontology a URL is about: /ontologies/:id/..., or ?ontology= / ?ontology_id=. */
export function ontologyIdFromUrl(pathname: string, search: string): string | null {
  const match = /^\/ontologies\/([^/]+)/.exec(pathname)
  if (match && match[1] !== 'new') return decodeURIComponent(match[1])
  const params = new URLSearchParams(search)
  return params.get('ontology') || params.get('ontology_id') || null
}

/**
 * The URL to open when the current ontology changes while on `pathname`.
 * Returns null when the page is not about an ontology (only the stored choice changes).
 */
export function urlForOntologySwitch(pathname: string, search: string, nextId: string): string | null {
  const params = new URLSearchParams(search)
  const detail = /^\/ontologies\/(?!new$)[^/]+(\/.*)?$/.exec(pathname)
  if (detail) {
    const rest = detail[1] ?? ''
    // Keep the section; drop ids (objects, studies, runs) that belong to the old ontology.
    if (rest === '/scenario-studies' || rest === '/scenarios') return `/ontologies/${nextId}${rest}`
    if (rest) return `/ontologies/${nextId}`
    const tab = params.get('tab')
    return `/ontologies/${nextId}${tab ? `?tab=${encodeURIComponent(tab)}` : ''}`
  }
  if (pathname.startsWith('/apps/workshop')) return `/apps/workshop?ontology=${nextId}`
  const picker = /^\/apps\/([^/]+)$/.exec(pathname)
  const pickerEntry = picker && NAV_ENTRIES.find(entry => entry.key === picker[1])
  if (pickerEntry) return entryHref(pickerEntry, nextId)
  for (const key of ['ontology', 'ontology_id']) {
    if (params.has(key)) {
      params.set(key, nextId)
      return `${pathname}?${params.toString()}`
    }
  }
  return null
}

const STORAGE_KEY = 'workbench_current_ontology'

export function readStoredOntology(): string | null {
  try { return localStorage.getItem(STORAGE_KEY) } catch { return null }
}

export function writeStoredOntology(id: string | null) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id)
    else localStorage.removeItem(STORAGE_KEY)
  } catch { /* storage blocked: the URL still carries the choice */ }
}
