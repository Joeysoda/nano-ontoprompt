import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { categoryColor, formatCell, PALETTE, severityTone, type Column, type Format, type Kpi } from './binding'

function useChart(build: (chart: echarts.ECharts) => void, deps: unknown[], ready = true) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current || !ready) return
    const chart = echarts.init(ref.current)
    build(chart)
    const resize = () => chart.resize()
    const observer = new ResizeObserver(resize)
    observer.observe(ref.current)
    return () => { observer.disconnect(); chart.dispose() }
  }, [...deps, ready]) // eslint-disable-line react-hooks/exhaustive-deps
  return ref
}

/** Generic dashboard widgets. They know nothing about MRP; pages bind data to them by configuration. */

export function KpiStrip({ items }: { items: Kpi[] }) {
  return <div className="grid border-b border-slate-200 bg-white" style={{ gridTemplateColumns: `minmax(300px, 1.6fr) repeat(${items.length - 1}, minmax(0, 1fr))` }} data-testid="kpi-strip">
    {items.map(item => <div key={item.label} className={`flex min-w-0 flex-wrap items-center gap-x-3 overflow-hidden px-5 py-4 ${item.tone === 'bad' ? 'bg-rose-700 text-white' : item.tone === 'good' ? 'bg-emerald-700 text-white' : ''}`}>
      <strong className="whitespace-nowrap text-3xl font-semibold tabular-nums">{item.value}</strong>
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

/** Share of a total per category, with the total in the middle. Clicking a slice selects its category. */
export function DonutChart({ slices, label, centerLabel = '合计', onSelect, selected }: { slices: Array<{ key: string; value: number }>; label: string; centerLabel?: string; onSelect?: (key: string) => void; selected?: string | null }) {
  const total = slices.reduce((sum, row) => sum + row.value, 0)
  const ref = useChart(chart => {
    chart.setOption({ baseOption: {
      animation: false, tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', right: 0, top: 'middle', formatter: (name: string) => { const row = slices.find(item => item.key === name); return `${name}  ${row ? Math.round(row.value / (total || 1) * 100) : 0}%  ${row?.value ?? ''}` } },
      title: { text: formatCell(total, 'number'), subtext: centerLabel, left: '29%', top: '40%', textAlign: 'center', textStyle: { fontSize: 18 }, subtextStyle: { fontSize: 11 } },
      series: [{ type: 'pie', radius: ['45%', '72%'], center: ['30%', '50%'], label: { show: false }, selectedMode: onSelect ? 'single' : false,
        data: slices.map((row, index) => ({ name: row.key, value: row.value, selected: row.key === selected, itemStyle: { color: categoryColor(row.key, index) } })) }],
    },
    // Narrow regions: legend under the ring instead of beside it.
    media: [{ query: { maxWidth: 380 }, option: { legend: { orient: 'horizontal', left: 'center', right: 'auto', top: 'auto', bottom: 0 },
      title: { left: '50%', top: '34%' }, series: [{ center: ['50%', '44%'], radius: ['40%', '64%'] }] } }],
    })
    if (onSelect) chart.on('click', params => onSelect(String(params.name)))
  }, [slices, total, centerLabel, selected])
  return <div ref={ref} className="h-full min-h-[180px]" role="img" aria-label={`${label}：合计 ${total}`} data-testid="donut-chart" />
}

/** Horizontal ranking: label, proportional bar, value; largest first. */
export function RankBars({ rows, label, format = 'number', color, onSelect, selected }: { rows: Array<{ key: string; value: number }>; label: string; format?: Format; color?: string; onSelect?: (key: string) => void; selected?: string | null }) {
  const max = Math.max(0, ...rows.map(row => row.value))
  return <ol className="space-y-1.5 text-xs" aria-label={label} data-testid="rank-bars">{rows.map((row, index) => <li key={row.key}>
    <button type="button" disabled={!onSelect} onClick={() => onSelect?.(row.key)} className={`grid w-full grid-cols-[minmax(0,9rem)_minmax(0,1fr)_4.5rem] items-center gap-2 rounded px-1 py-0.5 text-left ${selected === row.key ? 'bg-blue-50 ring-1 ring-blue-300' : onSelect ? 'hover:bg-slate-50' : ''}`}>
      <span className="truncate text-slate-700" title={row.key}>{row.key}</span>
      <span className="h-2 rounded bg-slate-100"><span className="block h-2 rounded" style={{ width: `${max > 0 ? Math.max(2, row.value / max * 100) : 0}%`, background: color || PALETTE[index % PALETTE.length] }} /></span>
      <span className="text-right tabular-nums text-slate-600">{formatCell(row.value, format)}</span>
    </button></li>)}</ol>
}

/** id identifies the row; key is the value a click selects (several alerts can share one subject). */
export type AlertItem = { id: string; key: string; title: string; subtitle?: string; severity: string; time?: string }
const SEVERITY_BADGE = { bad: 'border-rose-300 bg-rose-50 text-rose-700', warn: 'border-amber-300 bg-amber-50 text-amber-800', good: 'border-emerald-300 bg-emerald-50 text-emerald-800' }

/** Alerts most severe first, each with a severity badge; clicking one selects it. */
export function AlertList({ items, label, onSelect, selected }: { items: AlertItem[]; label: string; onSelect?: (key: string) => void; selected?: string | null }) {
  return <ul className="divide-y text-sm" aria-label={label} data-testid="alert-list">{items.map(item => { const tone = severityTone(item.severity)
    return <li key={item.id}><button type="button" disabled={!onSelect} onClick={() => onSelect?.(item.key)} className={`flex w-full gap-2 px-1 py-2 text-left ${selected === item.key ? 'bg-blue-50' : onSelect ? 'hover:bg-slate-50' : ''}`}>
      <span className={`h-fit shrink-0 rounded border px-1.5 py-0.5 text-[11px] ${tone ? SEVERITY_BADGE[tone] : 'border-slate-300 text-slate-600'}`}>{item.severity}</span>
      <span className="min-w-0"><span className="block">{item.title}</span>{(item.subtitle || item.time) && <span className="block text-xs text-slate-500">{[item.subtitle, item.time].filter(Boolean).join(' · ')}</span>}</span>
    </button></li> })}</ul>
}

export type MapPoint = { key: string; name: string; lng: number; lat: number; value: number; color: string }
export type MapLink = { from: [number, number]; to: [number, number] }

let world: Promise<void> | null = null
/** Natural Earth 1:50m countries (world-atlas, public domain data), fetched only when a map is shown. */
function loadWorld() {
  world ??= Promise.all([import('world-atlas/countries-50m.json?url'), import('topojson-client')]).then(async ([asset, client]) => {
    const topology = await (await fetch(asset.default)).json()
    echarts.registerMap('world', client.feature(topology, topology.objects.countries) as never)
  })
  return world
}

/** Points (sized by value) and optional links on a world map, zoomed to the points. */
export function GeoMap({ points, links = [], label, onSelect }: { points: MapPoint[]; links?: MapLink[]; label: string; onSelect?: (key: string) => void }) {
  const [ready, setReady] = useState(false)
  useEffect(() => { let live = true; loadWorld().then(() => live && setReady(true)); return () => { live = false } }, [])
  const ref = useChart(chart => {
    const lng = points.map(row => row.lng), lat = points.map(row => row.lat)
    const pad = (span: number) => Math.max(2, span * 0.12)
    const [west, east, south, north] = points.length ? [Math.min(...lng), Math.max(...lng), Math.min(...lat), Math.max(...lat)] : [-170, 170, -60, 80]
    const max = Math.max(1, ...points.map(row => row.value))
    chart.setOption({
      animation: false, tooltip: { trigger: 'item', formatter: (params: { data?: { name?: string; value?: number[] } }) => params.data?.value ? `${params.data.name}: ${formatCell(params.data.value[2], 'number')}` : '' },
      geo: { map: 'world', roam: true, silent: true, itemStyle: { areaColor: '#e2e8f0', borderColor: '#94a3b8' },
        boundingCoords: [[Math.max(-180, west - pad(east - west)), Math.min(85, north + pad(north - south))], [Math.min(180, east + pad(east - west)), Math.max(-85, south - pad(north - south))]] },
      series: [
        { type: 'lines', coordinateSystem: 'geo', silent: true, data: links.map(row => ({ coords: [row.from, row.to] })), lineStyle: { color: '#64748b', width: 1, opacity: 0.6, curveness: 0.2 } },
        { type: 'scatter', coordinateSystem: 'geo', data: points.map(row => ({ id: row.key, name: row.name, value: [row.lng, row.lat, row.value], itemStyle: { color: row.color, opacity: 0.85 } })),
          symbolSize: (value: number[]) => 5 + Math.sqrt(value[2] / max) * 18 },
      ],
    })
    if (onSelect) chart.on('click', params => { const id = (params.data as { id?: string } | undefined)?.id; if (id) onSelect(id) })
  }, [points, links], ready)
  return <div className="relative h-full min-h-[240px]">{!ready && <p className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">地图加载中…</p>}
    <div ref={ref} className="h-full min-h-[240px]" role="img" aria-label={`${label}：${points.length} 个点`} data-testid="geo-map" /></div>
}
