/** Workshop-style app definition (mirrors backend workshop_apps.AppDefinition) and the widget catalogue. */

export type Event = { set_variable?: string | null; field?: string | null; navigate?: string | null }
export type Widget = { id: string; type: WidgetType; title: string; source?: string | null; path: string; props: Record<string, unknown>; on_select?: Event | null }
export type LayoutItem = { i: string; x: number; y: number; w: number; h: number }
export type Page = { id: string; title: string; widgets: Widget[]; layout: LayoutItem[] }
export type DataSource = { id: string; kind: 'study_run'; label: string; study_id: string; node: string }
export type Variable = { id: string; label: string; default?: unknown }
export type AppDefinition = { schema: 'workshop-app/v1'; data_sources: DataSource[]; variables: Variable[]; pages: Page[] }
export type AppRecord = { id: string; name: string; description: string; head_version: number; head: { version: number; definition: AppDefinition } }
export type ResolvedSource = { source_id: string; study_id: string; node: string; run_id: string; status: string; scenario_id: string; scenario_revision: number; completed_at: string | null; outputs: Record<string, unknown> | null; artifacts: Array<{ id: string; artifact_type: string; content_digest: string }> }

export type FieldKind = 'text' | 'number' | 'boolean' | 'select' | 'scalar_path' | 'field' | 'variable' | 'page' | 'list' | 'color'
export type FieldSpec = { key: string; label: string; kind: FieldKind; options?: string[]; item?: FieldSpec[]; help?: string }
export type WidgetType = 'kpi_strip' | 'table' | 'bar_chart' | 'coverage_grid' | 'facet_list' | 'select' | 'stat_row' | 'banner' | 'action_list' | 'action_detail' | 'rerun_button'
export type WidgetSpec = { type: WidgetType; label: string; description: string; w: number; h: number; usesArray: boolean; selectable: boolean; props: FieldSpec[]; defaults: Record<string, unknown> }

const FORMAT = ['text', 'number', 'money', 'percent', 'date']
const WHERE: FieldSpec[] = [{ key: 'where_field', label: '条件字段', kind: 'field' }, { key: 'where_equals', label: '等于', kind: 'text' }]
const FILTER: FieldSpec[] = [{ key: 'filter_field', label: '按变量过滤的字段', kind: 'field', help: '行的这个字段等于（或数组包含）变量值时显示' }, { key: 'filter_variable', label: '过滤变量', kind: 'variable' }]

export const WIDGETS: WidgetSpec[] = [
  { type: 'kpi_strip', label: '指标条', description: '一行大号数字。第一个指标可按另一个数值的正负着色。', w: 24, h: 3, usesArray: false, selectable: false,
    props: [{ key: 'items', label: '指标', kind: 'list', item: [{ key: 'label', label: '名称', kind: 'text' }, { key: 'path', label: '数值', kind: 'scalar_path' }, { key: 'format', label: '格式', kind: 'select', options: FORMAT },
      { key: 'note', label: '说明（可用 {路径|格式}）', kind: 'text' }, { key: 'tone_path', label: '按此数值正负着色', kind: 'scalar_path' }] }],
    defaults: { items: [] } },
  { type: 'table', label: '表格', description: '把一个列表画成表格；点一行可设置变量并跳页。', w: 16, h: 10, usesArray: true, selectable: true,
    props: [{ key: 'columns', label: '列', kind: 'list', item: [{ key: 'label', label: '列名', kind: 'text' }, { key: 'field', label: '字段', kind: 'field' }, { key: 'format', label: '格式', kind: 'select', options: FORMAT },
      { key: 'badge', label: '显示为标签', kind: 'boolean' }, { key: 'tone_rule', label: '标红规则', kind: 'select', options: ['', 'gt', 'lt', 'truthy'] }, { key: 'tone_value', label: '阈值', kind: 'number' }, { key: 'tone_field', label: '判断字段（默认本列）', kind: 'field' }] },
      ...FILTER, { key: 'sort_field', label: '排序字段', kind: 'field' }, { key: 'sort_desc', label: '降序', kind: 'boolean' }, { key: 'max_rows', label: '最多行数', kind: 'number' }],
    defaults: { columns: [] } },
  { type: 'bar_chart', label: '堆叠柱状图', description: '按某字段分组，每个系列对满足条件的行求和。', w: 16, h: 8, usesArray: true, selectable: false,
    props: [{ key: 'x_field', label: '分组字段（横轴）', kind: 'field' }, { key: 'series', label: '系列', kind: 'list', item: [{ key: 'label', label: '名称', kind: 'text' }, { key: 'value_field', label: '求和字段（空=计数）', kind: 'field' }, ...WHERE, { key: 'color', label: '颜色', kind: 'color' }] }, ...FILTER],
    defaults: { series: [] } },
  { type: 'coverage_grid', label: '时间格子表', description: '每行一组按时间排列的数值，负数标红。', w: 24, h: 12, usesArray: true, selectable: false,
    props: [{ key: 'label_field', label: '行名字段', kind: 'field' }, { key: 'values_field', label: '数值数组字段', kind: 'field' }, { key: 'columns_path', label: '列标题（数组路径）', kind: 'scalar_path' },
      { key: 'emphasis_field', label: '加粗字段', kind: 'field' }, { key: 'weeks', label: '默认显示列数', kind: 'number' }, ...FILTER],
    defaults: { weeks: 12 } },
  { type: 'facet_list', label: '分面筛选', description: '列出某字段的取值和计数，点击设置变量。', w: 6, h: 5, usesArray: true, selectable: false,
    props: [{ key: 'field', label: '字段', kind: 'field' }, { key: 'options_path', label: '全部取值（可选数组路径）', kind: 'scalar_path' }, { key: 'variable', label: '写入变量', kind: 'variable' }],
    defaults: {} },
  { type: 'select', label: '下拉选择', description: '从列表中选一项写入变量。', w: 6, h: 3, usesArray: true, selectable: false,
    props: [{ key: 'value_field', label: '取值字段', kind: 'field' }, { key: 'label_field', label: '显示字段', kind: 'field' }, { key: 'variable', label: '写入变量', kind: 'variable' }, { key: 'allow_all', label: '允许“全部”', kind: 'boolean' }],
    defaults: { allow_all: true } },
  { type: 'stat_row', label: '统计数', description: '对列表计数、求和或算占比。', w: 6, h: 3, usesArray: true, selectable: false,
    props: [{ key: 'items', label: '统计项', kind: 'list', item: [{ key: 'label', label: '名称', kind: 'text' }, { key: 'agg', label: '方式', kind: 'select', options: ['count', 'sum', 'ratio'] }, { key: 'field', label: '求和字段', kind: 'field' }, ...WHERE,
      { key: 'format', label: '格式', kind: 'select', options: FORMAT }] }, ...FILTER],
    defaults: { items: [] } },
  { type: 'banner', label: '提醒条', description: '满足条件的行数大于 0 时显示一句提醒，{count} 代表行数。', w: 16, h: 2, usesArray: true, selectable: false,
    props: [{ key: 'template', label: '文字', kind: 'text' }, ...WHERE, ...FILTER],
    defaults: { template: '有 {count} 项需要关注' } },
  { type: 'action_list', label: '动作列表', description: '候选动作列表，点击选中写入变量。', w: 8, h: 14, usesArray: true, selectable: false,
    props: [{ key: 'label_field', label: '名称字段', kind: 'field' }, { key: 'subtitle', label: '副标题（可用 {字段|格式}）', kind: 'text' }, { key: 'variable', label: '选中写入变量', kind: 'variable' },
      { key: 'only_applicable', label: '只显示可执行的', kind: 'boolean' }, { key: 'max_rows', label: '最多条数', kind: 'number' }, { key: 'navigate', label: '点击后跳到页面', kind: 'page' }],
    defaults: { label_field: 'label' } },
  { type: 'action_detail', label: '动作详情与审批', description: '变量选中的候选动作：前后指标、动作清单、AI 解释、决策包审批与执行。', w: 16, h: 18, usesArray: true, selectable: false,
    props: [{ key: 'variable', label: '读取的变量', kind: 'variable' }, { key: 'metrics', label: '前后指标', kind: 'list', item: [{ key: 'label', label: '名称', kind: 'text' }, { key: 'before_path', label: '执行前（来源路径）', kind: 'scalar_path' },
      { key: 'after_field', label: '执行后（动作字段）', kind: 'field' }, { key: 'format', label: '格式', kind: 'select', options: FORMAT }] }, { key: 'explain', label: '提供 AI 解释', kind: 'boolean' }],
    defaults: { metrics: [], explain: true } },
  { type: 'rerun_button', label: '重跑按钮', description: '用最新本体数据重跑数据源的 Study，并逐步显示前后变化。', w: 8, h: 2, usesArray: false, selectable: false,
    props: [{ key: 'label', label: '按钮文字', kind: 'text' }, { key: 'steps', label: '对比的数值', kind: 'list', item: [{ key: 'label', label: '名称', kind: 'text' }, { key: 'path', label: '数值', kind: 'scalar_path' }, { key: 'format', label: '格式', kind: 'select', options: FORMAT }] }],
    defaults: { label: '重跑并更新', steps: [] } },
]

export const specOf = (type: string) => WIDGETS.find(row => row.type === type)

export const emptyDefinition = (): AppDefinition => ({ schema: 'workshop-app/v1', data_sources: [], variables: [], pages: [{ id: 'page1', title: '页面 1', widgets: [], layout: [] }] })

export function nextId(prefix: string, taken: string[]) {
  let index = 1
  while (taken.includes(`${prefix}${index}`)) index += 1
  return `${prefix}${index}`
}
