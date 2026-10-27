import { getPath, type Row } from '@/components/dashboard/binding'

export type ScheduleConfig = Record<string, unknown>
export type ScheduledTask = {
  id: string; label: string; resource: string; resources: string[]; layer: string; start: number | null; end: number | null
  objectType: string; objectId: string; predecessors: string[]; raw: Row; sourceStart: unknown; sourceEnd: unknown; sourceResource: unknown
}
export type ScheduleOverride = { resource: string; resources: string[]; start: number; end: number }
export type ScheduleCreate = { id: string; label: string; resource: string; resources: string[]; layer: string; start: number; end: number }
export type ScheduleViolation = { code: 'invalid_time' | 'outside_hours' | 'overlap' | 'dependency'; taskIds: string[]; message: string }
export type ScheduleLaneLayout = { lanes: Map<string, number>; laneCount: number }
export type ScheduleSearchHighlight =
  | { type: 'SLOT'; resource: string; start: number; end: number; comment: string }
  | { type: 'PUCK'; taskId: string; comment: string }
export type ScheduleSearchResult =
  | { state: 'error'; error: string }
  | { state: 'empty' }
  | { state: 'result'; title: string; resourceIds: string[]; sourceTaskIds: string[]; highlights: ScheduleSearchHighlight[] }
export type ScheduleBusinessValidation =
  | { state: 'error'; error: string }
  | { state: 'result'; violations: Array<{ code: string; message: string; taskIds: string[]; severity: 'error' | 'warning' }> }

const text = (value: unknown) => value === undefined || value === null ? '' : String(value)
export const timeMs = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function bindSchedule(rows: Row[], config: ScheduleConfig): ScheduledTask[] {
  if (rows.length > 2_000) throw new Error('排程组件最多显示 2,000 个任务')
  const ids = new Set<string>()
  return rows.map((row, index) => {
    const id = text(getPath(row, text(config.id_field)))
    if (!id || ids.has(id)) throw new Error(`任务标识必须存在且唯一（第 ${index + 1} 行）`)
    ids.add(id)
    const sourceStart = getPath(row, text(config.start_field)), sourceEnd = getPath(row, text(config.end_field))
    const sourceResource = getPath(row, text(config.resource_field))
    const resources = (Array.isArray(sourceResource) ? sourceResource : [sourceResource]).map(value => text(value)).filter(Boolean)
    const start = timeMs(sourceStart)
    let end = timeMs(sourceEnd)
    if (start !== null && end === null) {
      const duration = Number(getPath(row, text(config.duration_field)) ?? config.default_duration_minutes ?? 60)
      if (Number.isFinite(duration) && duration > 0) end = start + duration * 60_000
    }
    const rawPredecessors = config.dependency_field ? getPath(row, text(config.dependency_field)) : []
    if (rawPredecessors !== undefined && rawPredecessors !== null && !Array.isArray(rawPredecessors)) throw new Error(`任务 ${id} 的前置任务字段必须是标识数组`)
    return { id, label: text(getPath(row, text(config.label_field))) || id,
      resource: resources[0] ?? '', resources, layer: text(getPath(row, text(config.layer_field))) || 'default', start, end,
      objectType: text(getPath(row, text(config.object_type_field || 'object_type'))),
      objectId: text(getPath(row, text(config.object_id_field || 'object_id'))) || id,
      predecessors: (Array.isArray(rawPredecessors) ? rawPredecessors : []).map(value => text(value)).filter(Boolean),
      raw: row, sourceStart, sourceEnd, sourceResource }
  })
}

export const applyOverrides = (tasks: ScheduledTask[], overrides: Record<string, ScheduleOverride>) =>
  tasks.map(task => overrides[task.id] ? { ...task, ...overrides[task.id] } : task)

/** Assign the smallest available visual lane to every interval so overlapping tasks remain selectable. */
export function packScheduleLanes(tasks: ScheduledTask[]): ScheduleLaneLayout {
  const lanes = new Map<string, number>(), laneEnds: number[] = []
  const sorted = tasks.filter(task => task.start !== null && task.end !== null)
    .sort((left, right) => left.start! - right.start! || left.end! - right.end! || left.id.localeCompare(right.id))
  for (const task of sorted) {
    let lane = laneEnds.findIndex(end => end <= task.start!)
    if (lane < 0) lane = laneEnds.length
    laneEnds[lane] = task.end!
    lanes.set(task.id, lane)
  }
  return { lanes, laneCount: Math.max(1, laneEnds.length) }
}

export function snapTime(value: number, minutes: number) {
  const step = Math.max(1, Math.min(1_440, Number.isFinite(minutes) ? minutes : 15)) * 60_000
  return Math.round(value / step) * step
}

function minuteOfDay(value: number) { const date = new Date(value); return date.getUTCHours() * 60 + date.getUTCMinutes() }
function inHours(value: number, start: number, end: number) {
  const minute = minuteOfDay(value)
  return start <= end ? minute >= start && minute <= end : minute >= start || minute <= end
}

export function validateSchedule(tasks: ScheduledTask[], config: ScheduleConfig): ScheduleViolation[] {
  const issues: ScheduleViolation[] = []
  const byId = new Map(tasks.map(task => [task.id, task]))
  for (const task of tasks) if ((task.start === null) !== (task.end === null) || (task.start !== null && task.end !== null && task.end <= task.start))
    issues.push({ code: 'invalid_time', taskIds: [task.id], message: `${task.label} 的开始/结束时间无效` })
  const operatingStart = Number(config.operating_start_minute ?? 0), operatingEnd = Number(config.operating_end_minute ?? 1_440)
  if (Number.isFinite(operatingStart) && Number.isFinite(operatingEnd) && (operatingStart > 0 || operatingEnd < 1_440)) {
    for (const task of tasks) if (task.start !== null && task.end !== null && (!inHours(task.start, operatingStart, operatingEnd) || !inHours(task.end, operatingStart, operatingEnd)))
      issues.push({ code: 'outside_hours', taskIds: [task.id], message: `${task.label} 超出配置的 UTC 运行时段` })
  }
  if (config.allocation_behavior === 'no_overlaps') {
    const byResource = new Map<string, ScheduledTask[]>()
    for (const task of tasks) if (task.start !== null && task.end !== null) for (const resource of task.resources) byResource.set(resource, [...(byResource.get(resource) ?? []), task])
    for (const [resource, rows] of byResource) {
      rows.sort((a, b) => a.start! - b.start!)
      for (let index = 1; index < rows.length; index += 1) if (rows[index].start! < rows[index - 1].end!)
        issues.push({ code: 'overlap', taskIds: [rows[index - 1].id, rows[index].id], message: `${resource} 上的 ${rows[index - 1].label} 与 ${rows[index].label} 重叠` })
    }
  }
  for (const task of tasks) for (const predecessorId of task.predecessors) {
    const predecessor = byId.get(predecessorId)
    if (!predecessor) issues.push({ code: 'dependency', taskIds: [task.id], message: `${task.label} 引用了不存在的前置任务 ${predecessorId}` })
    else if (predecessor.end !== null && task.start !== null && predecessor.end > task.start)
      issues.push({ code: 'dependency', taskIds: [predecessor.id, task.id], message: `${task.label} 必须在 ${predecessor.label} 完成后开始` })
  }
  const stack: string[] = [], stackIndex = new Map<string, number>(), visited = new Set<string>(), cyclic = new Set<string>()
  const visit = (id: string) => {
    const activeAt = stackIndex.get(id)
    if (activeAt !== undefined) { for (const member of stack.slice(activeAt)) cyclic.add(member); return }
    if (visited.has(id)) return
    stackIndex.set(id, stack.length); stack.push(id)
    for (const predecessor of byId.get(id)?.predecessors ?? []) if (byId.has(predecessor)) visit(predecessor)
    stack.pop(); stackIndex.delete(id); visited.add(id)
  }
  for (const task of tasks) visit(task.id)
  if (cyclic.size) issues.push({ code: 'dependency', taskIds: [...cyclic], message: `任务前置关系存在循环：${[...cyclic].join(', ')}` })
  return issues
}

export const outputTime = (original: unknown, value: number) => typeof original === 'number' ? value : new Date(value).toISOString()
export const outputResources = (original: unknown, resources: string[]) => Array.isArray(original) ? resources : resources[0] ?? null

export function parseScheduleSearchOutput(value: unknown): ScheduleSearchResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('推荐 Function 必须返回对象')
  const output = value as Record<string, unknown>
  if (output.schema !== 'schedule-search/v1') throw new Error('推荐 Function 输出缺少 schedule-search/v1 schema')
  if (typeof output.error === 'string' && output.error.trim()) return { state: 'error', error: output.error.trim() }
  if (output.row_group === undefined || output.row_group === null) return { state: 'empty' }
  if (typeof output.row_group !== 'object' || Array.isArray(output.row_group)) throw new Error('推荐 row_group 必须是对象')
  const group = output.row_group as Record<string, unknown>, rawHighlights = group.highlights
  if (!Array.isArray(rawHighlights) || rawHighlights.length > 1_000) throw new Error('推荐 highlights 必须是最多 1,000 项的数组')
  const highlights = rawHighlights.map((raw, index): ScheduleSearchHighlight => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`推荐 highlight ${index + 1} 必须是对象`)
    const row = raw as Record<string, unknown>, kind = text(row.type).toUpperCase(), comment = text(row.comment)
    if (kind === 'PUCK') {
      const taskId = text(row.schedulable_object_primary_key)
      if (!taskId) throw new Error(`推荐 highlight ${index + 1} 缺少任务标识`)
      return { type: 'PUCK', taskId, comment }
    }
    if (kind === 'SLOT') {
      const domain = row.domain as Record<string, unknown> | undefined, start = timeMs(domain?.start), end = timeMs(domain?.end), resource = text(row.container_id)
      if (!resource || start === null || end === null || end <= start) throw new Error(`推荐 highlight ${index + 1} 的资源或时间段无效`)
      return { type: 'SLOT', resource, start, end, comment }
    }
    throw new Error(`推荐 highlight ${index + 1} 的类型必须是 SLOT 或 PUCK`)
  })
  const strings = (candidate: unknown, name: string) => {
    if (candidate === undefined) return []
    if (!Array.isArray(candidate) || candidate.some(item => typeof item !== 'string')) throw new Error(`推荐 ${name} 必须是字符串数组`)
    return candidate as string[]
  }
  return { state: highlights.length ? 'result' : 'empty', title: text(group.title) || '排程推荐', resourceIds: strings(group.container_ids, 'container_ids'), sourceTaskIds: strings(output.source_puck_ids, 'source_puck_ids'), highlights }
}

export function parseScheduleValidationOutput(value: unknown): ScheduleBusinessValidation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('业务校验 Function 必须返回对象')
  const output = value as Record<string, unknown>
  if (output.schema !== 'schedule-validation/v1') throw new Error('业务校验 Function 输出缺少 schedule-validation/v1 schema')
  if (typeof output.error === 'string' && output.error.trim()) return { state: 'error', error: output.error.trim() }
  if (!Array.isArray(output.violations) || output.violations.length > 500) throw new Error('业务校验 violations 必须是最多 500 项的数组')
  const violations = output.violations.map((raw, index) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`业务校验 violation ${index + 1} 必须是对象`)
    const row = raw as Record<string, unknown>, message = text(row.message), severity = text(row.severity || 'error')
    if (!message || !['error', 'warning'].includes(severity)) throw new Error(`业务校验 violation ${index + 1} 的消息或级别无效`)
    if (row.task_ids !== undefined && (!Array.isArray(row.task_ids) || row.task_ids.some(item => typeof item !== 'string'))) throw new Error(`业务校验 violation ${index + 1} 的 task_ids 必须是字符串数组`)
    return { code: text(row.code) || 'business_rule', message, taskIds: (row.task_ids as string[] | undefined) ?? [], severity: severity as 'error' | 'warning' }
  })
  return { state: 'result', violations }
}
