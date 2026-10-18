export const formatStudyValue = (value: unknown): string => {
  if (value == null) return '—'
  if (typeof value === 'number') return Number.isFinite(value) ? Number(value.toFixed(4)).toString() : String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
