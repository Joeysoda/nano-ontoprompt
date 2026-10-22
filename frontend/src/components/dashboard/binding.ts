export type Format = 'money' | 'percent' | 'number' | 'text' | 'date'
export type Column<T> = { label: string; value: (row: T) => unknown; format?: Format; tone?: (row: T) => 'bad' | 'good' | undefined; badge?: boolean }
export type Kpi = { label: string; value: string; note?: string; tone?: 'bad' | 'good'; noteTone?: 'bad' | 'good' }
export type Row = Record<string, unknown>

export function formatCell(value: unknown, format: Format = 'text'): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    if (format === 'money') return money(value)
    if (format === 'percent') return `${Math.round(value * 100)}%`
    return Number.isInteger(value) ? value.toLocaleString('en-US') : value.toLocaleString('en-US', { maximumFractionDigits: 1 })
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(item => typeof item === 'object' && item ? String((item as Row).id ?? (item as Row).demand_id ?? JSON.stringify(item)) : plainId(String(item))).join('、')
  return plainId(String(value))
}

/** Object ids may carry an ontology namespace ("5ef77329:ORD-1009"); show the business id. */
export const plainId = (text: string) => text.replace(/\b[0-9a-f]{8}:/g, '')

export function money(value: number) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`
  return `${sign}$${abs.toFixed(0)}`
}

/** "totals.projected_revenue" -> value; "" -> the object itself. */
export function getPath(value: unknown, path?: string): unknown {
  if (!path) return value
  return path.split('.').reduce<unknown>((current, key) => current && typeof current === 'object' ? (current as Row)[key] : undefined, value)
}

export const asRows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === 'object') : []

/** A row matches a variable filter when the variable is unset, equals the field, or is contained in an array field. */
export function matches(row: Row, field?: string, value?: string | null) {
  if (!field || value === undefined || value === null || value === '') return true
  const actual = getPath(row, field)
  return Array.isArray(actual) ? actual.map(String).includes(value) : String(actual) === value
}

export type Where = { where_field?: string; where_equals?: string }
const passes = (row: Row, where: Where) => !where.where_field || String(getPath(row, where.where_field)) === String(where.where_equals ?? '')

export type Aggregate = Where & { agg?: 'count' | 'sum' | 'ratio'; field?: string }
/** count = matching rows; sum = sum of field over matching rows; ratio = matching / all. */
export function aggregate(rows: Row[], spec: Aggregate): number {
  const hit = rows.filter(row => passes(row, spec))
  if (spec.agg === 'sum') return hit.reduce((sum, row) => sum + (Number(getPath(row, spec.field)) || 0), 0)
  if (spec.agg === 'ratio') return rows.length ? hit.length / rows.length : 0
  return hit.length
}

/** Group rows by x and sum a field per series (each series may have its own where). */
export function groupSeries(rows: Row[], xField: string, series: Array<Where & { value_field?: string }>) {
  const keys = [...new Set(rows.map(row => String(getPath(row, xField))))].sort()
  return { keys, values: series.map(spec => keys.map(key => rows.filter(row => String(getPath(row, xField)) === key && passes(row, spec))
    .reduce((sum, row) => sum + (spec.value_field ? Number(getPath(row, spec.value_field)) || 0 : 1), 0))) }
}

/** "{totals.revenue_gap|money} to target" -> "-$670K to target". */
export function template(text: string, data: unknown, extra: Record<string, unknown> = {}) {
  return text.replace(/\{([\w.]+)(?:\|(\w+))?\}/g, (_, path: string, format?: string) => formatCell(path in extra ? extra[path] : getPath(data, path), (format as Format) ?? 'text'))
}

export type ToneRule = { rule?: 'gt' | 'lt' | 'truthy' | 'negative'; value?: number; field?: string }
export function tone(row: Row, fallbackField: string, spec?: ToneRule): 'bad' | 'good' | undefined {
  if (!spec?.rule) return undefined
  const value = getPath(row, spec.field || fallbackField)
  if (spec.rule === 'truthy') return value ? 'bad' : undefined
  if (spec.rule === 'negative') return Number(value) < 0 ? 'bad' : 'good'
  const number = Number(value)
  if (spec.rule === 'gt') return number > (spec.value ?? 0) ? 'bad' : undefined
  return number < (spec.value ?? 0) ? 'bad' : undefined
}

/** Paths an editor can bind to: arrays (for tables/charts) and scalars (for KPIs), up to three levels deep. */
export function discoverPaths(value: unknown, prefix = '', depth = 0): Array<{ path: string; kind: 'array' | 'number' | 'text' | 'object' }> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || depth > 2) return []
  return Object.entries(value as Row).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (Array.isArray(item)) return [{ path, kind: 'array' as const }]
    if (typeof item === 'number') return [{ path, kind: 'number' as const }]
    if (typeof item === 'string' || typeof item === 'boolean') return [{ path, kind: 'text' as const }]
    return [{ path, kind: 'object' as const }, ...discoverPaths(item, path, depth + 1)]
  })
}

/** Fields of the first element of an array path (one nested level), for column pickers. */
export function discoverFields(rows: Row[]): string[] {
  const first = rows[0]
  if (!first) return []
  return Object.entries(first).flatMap(([key, item]) => item && typeof item === 'object' && !Array.isArray(item) ? Object.keys(item as Row).map(sub => `${key}.${sub}`) : [key])
}
