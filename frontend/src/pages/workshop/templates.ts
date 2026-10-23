import { specOf, type LayoutItem, type Page, type WidgetType } from './spec'

/** Page templates: named regions laid out on a 24-column page; each region is its own grid as wide as its span. */
export type Region = { id: string; tag: string; label: string; span: number }
export type TemplateId = 'free' | 'scene_kpi_insight' | 'split' | 'kpi_top'

export const TEMPLATES: Record<TemplateId, { label: string; description: string; regions: Region[] }> = {
  free: { label: '自由布局', description: '整页一个 24 列网格，组件随意摆放。', regions: [] },
  scene_kpi_insight: { label: '场景 + 指标 + 洞察', description: '左上场景区放地图或主图，右上指标区放数字卡片，下方洞察区放图表和列表。', regions: [
    { id: 'scene', tag: 'SCENE', label: '场景区', span: 15 }, { id: 'kpi', tag: 'KPI', label: '指标区', span: 9 }, { id: 'insight', tag: 'INSIGHT', label: '洞察区', span: 24 }] },
  split: { label: '左右分屏', description: '左右各占一半，适合对比。', regions: [
    { id: 'left', tag: 'LEFT', label: '左屏', span: 12 }, { id: 'right', tag: 'RIGHT', label: '右屏', span: 12 }] },
  kpi_top: { label: '顶部指标 + 主区', description: '上方一排指标，下方主区放图表。', regions: [
    { id: 'kpi', tag: 'KPI', label: '指标区', span: 24 }, { id: 'main', tag: 'MAIN', label: '主区', span: 24 }] },
}

export const regionsOf = (page: Pick<Page, 'template'>) => TEMPLATES[(page.template ?? 'free') as TemplateId]?.regions ?? []
export const spanOf = (page: Pick<Page, 'template'>, region?: string | null) => regionsOf(page).find(row => row.id === region)?.span ?? 24

/** First spot where a w x h box fits, scanning rows top-down and columns left-right; the box is narrowed to the grid. */
export function place(taken: LayoutItem[], w: number, h: number, cols: number): { x: number; y: number; w: number } {
  const width = Math.max(1, Math.min(w, cols))
  const bottom = Math.max(0, ...taken.map(item => item.y + item.h))
  for (let y = 0; y <= bottom; y += 1) {
    for (let x = 0; x + width <= cols; x += 1) {
      if (!taken.some(item => x < item.x + item.w && item.x < x + width && y < item.y + item.h && item.y < y + h)) return { x, y, w: width }
    }
  }
  return { x: 0, y: bottom, w: width }
}

// Where a widget goes when a template is applied or it is added without a chosen region.
const PREFERRED: Partial<Record<WidgetType, string[]>> = {
  kpi_strip: ['kpi'], stat_row: ['kpi'], banner: ['kpi'], geo_map: ['scene', 'main', 'left'],
  donut_chart: ['kpi', 'insight', 'main', 'right'], rerun_button: ['kpi'],
}

export function defaultRegion(page: Pick<Page, 'template'>, type: WidgetType): string | null {
  const regions = regionsOf(page)
  if (!regions.length) return null
  return (PREFERRED[type] ?? []).find(id => regions.some(row => row.id === id)) ?? regions[regions.length - 1].id
}

/** Next free spot for a w x h box in a region (or the whole page), ignoring the box's own current position. */
export function placeBox(page: Page, region: string | null, w: number, h: number, id: string): LayoutItem {
  const regionOf = new Map(page.widgets.map(row => [row.id, row.region ?? null]))
  const taken = page.layout.filter(item => item.i !== id && (regionOf.get(item.i) ?? null) === region)
  return { i: id, h, ...place(taken, w, h, spanOf(page, region)) }
}

export const placeNew = (page: Page, type: WidgetType, region: string | null, id: string) =>
  placeBox(page, region, specOf(type)?.w ?? 8, specOf(type)?.h ?? 6, id)

/** Move a widget to another region, keeping its size where the region is wide enough. */
export function moveToRegion(page: Page, id: string, region: string): Page {
  const current = page.layout.find(item => item.i === id)
  const widget = page.widgets.find(row => row.id === id)!
  const widgets = page.widgets.map(row => row.id === id ? { ...row, region } : row)
  const box = placeBox({ ...page, widgets }, region, current?.w ?? specOf(widget.type)?.w ?? 8, current?.h ?? specOf(widget.type)?.h ?? 6, id)
  return { ...page, widgets, layout: [...page.layout.filter(item => item.i !== id), box] }
}

/** Switch a page's template: keep regions that still exist, send the rest to their preferred region, and re-flow every region. */
export function applyTemplate(page: Page, template: TemplateId): Page {
  const next = { ...page, template }
  const regions = regionsOf(next)
  const original = new Map(page.layout.map(item => [item.i, item]))
  const ordered = [...page.widgets].sort((a, b) => { const p = original.get(a.id), q = original.get(b.id); return (p?.y ?? 0) - (q?.y ?? 0) || (p?.x ?? 0) - (q?.x ?? 0) })
  const widgets = page.widgets.map(row => ({ ...row, region: regions.some(region => region.id === row.region) ? row.region : defaultRegion(next, row.type) }))
  const regionOf = new Map(widgets.map(row => [row.id, row.region ?? null]))
  const layout: LayoutItem[] = []
  for (const widget of ordered) {
    const region = regionOf.get(widget.id) ?? null
    const old = original.get(widget.id), spec = specOf(widget.type)
    const w = old?.w ?? spec?.w ?? 8, h = old?.h ?? spec?.h ?? 6
    layout.push({ i: widget.id, h, ...place(layout.filter(item => (regionOf.get(item.i) ?? null) === region), w, h, spanOf(next, region)) })
  }
  return { ...next, widgets, layout }
}
