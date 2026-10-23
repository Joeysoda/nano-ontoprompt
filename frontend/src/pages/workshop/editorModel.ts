import { specOf, type AppDefinition, type Page, type Widget } from './spec'
import { regionsOf, spanOf } from './templates'

export type DefinitionIssue = { location: string; message: string }

const REQUIRED_PROPS: Record<string, string[]> = {
  kpi_strip: ['items'], table: ['columns'], bar_chart: ['x_field', 'series'], coverage_grid: ['label_field', 'values_field'],
  facet_list: ['field'], select: ['value_field', 'label_field', 'variable'], stat_row: ['items'], banner: ['template'],
  action_list: ['label_field'], action_detail: ['variable'], rerun_button: ['steps'],
  donut_chart: ['category_field'], rank_bars: ['label_field'], alert_list: ['title'], geo_map: ['lat_field', 'lng_field'],
}

/** References that make deleting a builder resource unsafe. */
export function resourceReferences(definition: AppDefinition, kind: 'source' | 'variable' | 'page', id: string) {
  const found: string[] = []
  for (const page of definition.pages) for (const widget of page.widgets) {
    const where = `${page.title} / ${widget.title || widget.id}`
    if (kind === 'source' && widget.source === id) found.push(where)
    if (kind === 'page' && (widget.on_select?.navigate === id || widget.props.navigate === id)) found.push(where)
    if (kind === 'variable' && (widget.on_select?.set_variable === id || widget.props.variable === id || widget.props.filter_variable === id)) found.push(where)
  }
  return found
}

/** Fast client-side counterpart of the server validator, so errors are visible before publish. */
export function validateDefinition(definition: AppDefinition): DefinitionIssue[] {
  const issues: DefinitionIssue[] = []
  const sources = new Set(definition.data_sources.map(row => row.id))
  const variables = new Set(definition.variables.map(row => row.id))
  const pages = new Set(definition.pages.map(row => row.id))
  for (const page of definition.pages) {
    const widgets = new Set(page.widgets.map(row => row.id))
    for (const item of page.layout) if (!widgets.has(item.i)) issues.push({ location: page.title, message: `布局引用了不存在的组件 ${item.i}` })
    for (const widget of page.widgets) {
      const location = `${page.title} / ${widget.title || widget.id}`
      const spec = specOf(widget.type)
      if (!spec) issues.push({ location, message: `未知组件类型 ${widget.type}` })
      for (const key of REQUIRED_PROPS[widget.type] ?? []) if (!(key in widget.props)) issues.push({ location, message: `缺少必要配置 ${key}` })
      if (widget.source && !sources.has(widget.source)) issues.push({ location, message: `数据源 ${widget.source} 不存在` })
      const regions = regionsOf(page)
      if (regions.length && !regions.some(row => row.id === widget.region)) issues.push({ location, message: '组件不在本页模板的任何区域里' })
      const item = page.layout.find(row => row.i === widget.id)
      if (item && item.x + item.w > spanOf(page, widget.region)) issues.push({ location, message: `宽度超出所在区域（${spanOf(page, widget.region)} 列）` })
      const usedVariables = [widget.props.variable, widget.props.filter_variable, widget.on_select?.set_variable].filter(Boolean) as string[]
      for (const id of usedVariables) if (!variables.has(id)) issues.push({ location, message: `变量 ${id} 不存在` })
      for (const id of [widget.props.navigate, widget.on_select?.navigate].filter(Boolean) as string[]) if (!pages.has(id)) issues.push({ location, message: `目标页面 ${id} 不存在` })
    }
  }
  return issues
}

export function duplicateWidget(page: Page, widget: Widget, allWidgetIds: string[]): { widget: Widget; layout: Page['layout'][number] } {
  const prefix = widget.id.replace(/\d+$/, '') || 'widget'
  let index = 1
  while (allWidgetIds.includes(`${prefix}${index}`)) index += 1
  const id = `${prefix}${index}`
  const current = page.layout.find(row => row.i === widget.id)
  return {
    widget: { ...structuredClone(widget), id, title: `${widget.title}（副本）` },
    layout: current ? { ...current, i: id, x: Math.min(23, current.x + 1), y: current.y + 1 } : { i: id, x: 0, y: 0, w: specOf(widget.type)?.w ?? 8, h: specOf(widget.type)?.h ?? 6 },
  }
}
