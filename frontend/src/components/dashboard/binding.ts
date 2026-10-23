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
  if (format === 'date' && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) return value.slice(0, 16).replace('T', ' ')
  if (Array.isArray(value)) return value.map(item => typeof item === 'object' && item ? String((item as Row).id ?? (item as Row).demand_id ?? JSON.stringify(item)) : plainId(String(item))).join('、')
  return plainId(String(value))
}

/** Object ids may carry an ontology namespace ("5ef77329:ORD-1009"); show the business id. */
export const plainId = (text: string) => text.replace(/\b[0-9a-f]{8}:/g, '')

export function money(value: number) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(abs < 1e4 ? 1 : 0)}K`   // $1.5K vs $1.2K stay distinguishable
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

/** One total per distinct value of a field (sum of valueField, or a row count), largest first. */
export function groupTotals(rows: Row[], field: string, valueField?: string): Array<{ key: string; value: number }> {
  const totals = new Map<string, number>()
  for (const row of rows) {
    const key = String(getPath(row, field) ?? '—')
    totals.set(key, (totals.get(key) ?? 0) + (valueField ? Number(getPath(row, valueField)) || 0 : 1))
  }
  return [...totals].map(([key, value]) => ({ key, value })).sort((a, b) => b.value - a.value || a.key.localeCompare(b.key))
}

/** Epoch milliseconds of a timestamp: ISO strings, or numbers in seconds (< 1e11) or milliseconds. */
export function toTime(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value < 1e11 ? value * 1000 : value
  if (typeof value === 'string' && value) { const time = Date.parse(value); return Number.isNaN(time) ? null : time }
  return null
}

export const RANGE_DAYS: Record<string, number> = { '1d': 1, '7d': 7, '30d': 30 }

/**
 * Rows in the last N days ending at the latest timestamp in the rows themselves, so historical
 * datasets still have a "last 7 days".  Rows without a time are dropped once a range applies.
 */
export function withinRange(rows: Row[], field: string | null | undefined, range: string): Row[] {
  const days = RANGE_DAYS[range]
  if (!field || !days) return rows
  const times = rows.map(row => toTime(getPath(row, field)))
  const anchor = Math.max(...times.filter((time): time is number => time !== null))
  if (!Number.isFinite(anchor)) return []
  const cutoff = anchor - days * 86_400_000
  return rows.filter((_, index) => times[index] !== null && times[index]! > cutoff)
}

/** A finite coordinate or null; strings such as "18.25" count, blanks and out-of-range values do not. */
export function coordinate(value: unknown, limit: number): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && Math.abs(number) <= limit ? number : null
}

const SEVERITY: Record<string, 'bad' | 'warn' | 'good'> = { critical: 'bad', high: 'bad', 严重: 'bad', 高: 'bad', medium: 'warn', 中: 'warn', 预警: 'warn', low: 'good', 低: 'good', 正常: 'good' }
export const severityTone = (value: unknown) => SEVERITY[String(value).toLowerCase()]

export const PALETTE = ['#1d4ed8', '#0f766e', '#a16207', '#7c3aed', '#b91c1c', '#0369a1', '#4d7c0f', '#be185d']
const TONE_COLOR = { bad: '#dc2626', warn: '#f59e0b', good: '#16a34a' }
/** Severity-like categories get traffic-light colours; anything else cycles the palette. */
export function categoryColor(key: string, index: number) {
  const tone = severityTone(key)
  return tone ? TONE_COLOR[tone] : PALETTE[index % PALETTE.length]
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
