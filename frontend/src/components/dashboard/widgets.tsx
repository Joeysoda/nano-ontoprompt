import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { formatCell, type Column, type Kpi } from './binding'

/** Generic dashboard widgets. They know nothing about MRP; pages bind data to them by configuration. */

export function KpiStrip({ items }: { items: Kpi[] }) {
  return <div className="grid border-b border-slate-200 bg-white" style={{ gridTemplateColumns: `minmax(300px, 1.6fr) repeat(${items.length - 1}, minmax(0, 1fr))` }} data-testid="kpi-strip">
    {items.map(item => <div key={item.label} className={`flex items-center gap-3 px-5 py-4 ${item.tone === 'bad' ? 'bg-rose-700 text-white' : item.tone === 'good' ? 'bg-emerald-700 text-white' : ''}`}>
      <strong className="text-3xl font-semibold tabular-nums">{item.value}</strong>
      <span className={`text-xs leading-tight ${item.tone ? 'text-white/85' : 'text-slate-500'}`}>{item.label}{item.note && <><br /><span className={item.tone ? '' : item.noteTone === 'good' ? 'text-emerald-700' : 'text-rose-700'}>{item.note}</span></>}</span>
    </div>)}
  </div>
}

export function DataTable<T extends Record<string, unknown>>({ rows, columns, rowKey, selected, onSelect, label }: { rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; selected?: string; onSelect?: (row: T) => void; label: string }) {
  return <div className="overflow-auto"><table className="w-full border-collapse text-left text-sm" aria-label={label}>
    <thead className="sticky top-0 bg-slate-50 text-xs text-slate-500"><tr>{columns.map(col => <th key={col.label} className="border-b px-3 py-2 font-medium">{col.label}</th>)}</tr></thead>
    <tbody>{rows.map(row => <tr key={rowKey(row)} onClick={() => onSelect?.(row)} className={`border-b ${onSelect ? 'cursor-pointer hover:bg-slate-50' : ''} ${selected === rowKey(row) ? 'bg-blue-50 outline outline-1 outline-blue-400' : ''}`}>
      {columns.map(col => { const value = col.value(row); const tone = col.tone?.(row)
        return <td key={col.label} className={`px-3 py-2.5 ${tone === 'bad' ? 'font-semibold text-rose-700' : tone === 'good' ? 'text-emerald-700' : ''}`}>{col.badge ? <span className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs text-blue-800">{formatCell(value, col.format)}</span> : formatCell(value, col.format)}</td> })}
    </tr>)}</tbody>
  </table></div>
}

export function StackedBarChart({ categories, series, height = 260, label }: { categories: string[]; series: Array<{ name: string; color: string; data: number[] }>; height?: number; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      animation: false, grid: { left: 48, right: 16, top: 28, bottom: 40 }, legend: { right: 8, top: 0 },
      tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: categories }, yAxis: { type: 'value', name: '需求数量' },
      series: series.map(row => ({ name: row.name, type: 'bar', stack: 'total', data: row.data, itemStyle: { color: row.color }, label: { show: true, position: 'inside', fontSize: 10 } })),
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); chart.dispose() }
  }, [categories, series])
  return <div ref={ref} style={{ height }} role="img" aria-label={label} data-testid="stacked-bar-chart" />
}

/** Rows x time buckets heat grid; negative cells are deficits. */
export function CoverageGrid({ columns, rows, label }: { columns: string[]; rows: Array<{ key: string; label: string; values: number[]; emphasis?: boolean }>; label: string }) {
  const tone = (value: number) => value < 0 ? 'bg-rose-100 text-rose-800' : value === 0 ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-800'
  return <div className="overflow-auto" data-testid="coverage-grid"><table className="border-collapse text-xs" aria-label={label}>
    <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr><th className="sticky left-0 z-10 min-w-48 border-b bg-slate-50 px-3 py-2 text-left font-medium">物料</th>{columns.map(col => <th key={col} className="min-w-20 border-b px-2 py-2 text-right font-medium">{col}</th>)}</tr></thead>
    <tbody>{rows.map(row => <tr key={row.key} className="border-b">
      <td className={`sticky left-0 bg-white px-3 py-1.5 ${row.emphasis ? 'font-semibold' : ''}`}>{row.label}</td>
      {row.values.map((value, index) => <td key={index} className={`px-2 py-1.5 text-right tabular-nums ${tone(value)}`}>{Math.round(value)}</td>)}
    </tr>)}</tbody>
  </table></div>
}
