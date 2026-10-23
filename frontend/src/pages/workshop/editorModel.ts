import { specOf, type AppDefinition, type DataSource, type OutputColumn, type OutputSchema, type Page, type Widget } from './spec'
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
  if (kind === 'variable') for (const source of definition.data_sources) {
    if (source.kind === 'query' && Object.values(source.params ?? {}).some(value => (value as { variable?: string } | null)?.variable === id)) found.push(`数据源 ${source.label || source.id} 的参数`)
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

export type AutobindField = { name: string; label: string; data_type: string; role: string; distinct: number; samples: string[]; dimension: string | null }
export type AutobindResult = {
  mode: 'rules' | 'llm'; model: string | null; dropped: string[]; data_sources: DataSource[]; page: Page
  profile: { object_type: string; label: string; count: number; fields: AutobindField[]; time_field: string | null }
  plan: { page_title: string; reasoning?: string }
}

/** Keep only the chosen widgets, and the data sources they still use. */
export function pickWidgets(result: AutobindResult, keep: Set<string>): { data_sources: DataSource[]; page: Page } {
  const widgets = result.page.widgets.filter(row => keep.has(row.id))
  const used = new Set(widgets.map(row => row.source))
  return { data_sources: result.data_sources.filter(row => used.has(row.id)), page: { ...result.page, widgets, layout: result.page.layout.filter(row => keep.has(row.i)) } }
}


// --- deterministic binding: a query's output schema fills a widget ---------------------------------------------------

type Binding = { path: string; props: Record<string, unknown> } | { error: string }
const first = (schema: OutputSchema, ...roles: string[]) => roles.map(role => schema.columns.find(col => col.role === role)).find(Boolean)
const metricFormat = (col?: OutputColumn) => !col ? 'number' : /rate|ratio|率|比例/i.test(col.name + col.label) ? 'percent' : 'number'

/**
 * Fill a widget from what a query returns: the result's shape decides which widgets fit, column roles fill the fields.
 * Same idea as Workshop's function-backed charts, which take a 2D/3D aggregation (Palantir docs).
 */
export function bindFromSchema(type: string, schema: OutputSchema): Binding {
  const metric = first(schema, 'metric', 'measure')
  const label = first(schema, 'name', 'dimension')
  const grouped = schema.shape === '2d' || schema.shape === '3d'
  switch (type) {
    case 'kpi_strip':
      if (schema.shape !== 'single') return { error: '指标条需要单行指标的查询（不分组的汇总）' }
      return { path: '', props: { items: schema.columns.filter(col => col.role === 'metric').map(col => ({ label: col.label, path: `${schema.path}.0.${col.name}`, format: metricFormat(col) })) } }
    case 'table':
      return { path: schema.path, props: { columns: schema.columns.filter(col => col.role !== 'identifier').map(col => ({ label: col.label, field: col.name, format: col.role === 'time' ? 'date' : col.role === 'metric' ? metricFormat(col) : 'text' })), max_rows: 50 } }
    case 'rank_bars':
      if (!grouped || !label || !metric) return { error: '排行条需要按字段分组、带指标的查询' }
      return { path: schema.path, props: { label_field: label.name, value_field: metric.name, max_rows: 10, format: metricFormat(metric) } }
    case 'donut_chart': {
      // Only a few-valued category reads as shares; names and ids (cities, machines) belong in a ranking.
      const category = first(schema, 'category')
      if (!grouped || !category || !metric) return { error: '环形图需要按“类别”列（取值只有几种，如高/中/低）分组的查询；按城市、设备等分组请用横向排行条' }
      return { path: schema.path, props: { category_field: category.name, value_field: metric.name, center_label: metric.label } }
    }
    case 'geo_map': {
      const lat = first(schema, 'latitude'), lng = first(schema, 'longitude')
      if (!lat || !lng) return { error: '这条查询的结果没有经纬度列，不能画地图' }
      return { path: schema.path, props: { lat_field: lat.name, lng_field: lng.name, label_field: (label ?? lat).name, size_field: metric?.name ?? '', color_field: first(schema, 'category')?.name ?? '',
        key_field: first(schema, 'dimension', 'identifier')?.name ?? 'id' } }
    }
    case 'bar_chart':
      if (!grouped || !label) return { error: '柱状图需要按字段分组的查询' }
      return { path: schema.path, props: { x_field: first(schema, 'dimension')?.name ?? label.name, series: schema.columns.filter(col => col.role === 'metric').map(col => ({ label: col.label, value_field: col.name })) } }
    default:
      return { error: '这个组件不支持按查询自动填写' }
  }
}

/** Widgets a query can feed, in the order a builder most likely wants them. */
export const suggestWidgets = (schema: OutputSchema) =>
  ['kpi_strip', 'geo_map', 'rank_bars', 'donut_chart', 'bar_chart', 'table'].filter(type => !('error' in bindFromSchema(type, schema)))
