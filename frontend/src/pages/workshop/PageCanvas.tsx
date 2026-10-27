import GridLayout, { useContainerWidth, type Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import WidgetView, { type Runtime } from './WidgetView'
import { specOf, type LayoutItem, type Page, type Widget } from './spec'
import { regionsOf, type Region } from './templates'

const ROW = 32

type CanvasProps = { page: Page; rt: Runtime; editing?: boolean; selected?: string; onSelect?: (id: string) => void; onLayout?: (layout: LayoutItem[]) => void
  /** Editor only: the region new widgets go to, and the per-region "+ 组件" button. */
  activeRegion?: string | null; onActiveRegion?: (id: string) => void; onAddToRegion?: (id: string) => void }

/** One page of widgets.  Free pages are one 24-column grid; templated pages are regions, each its own grid as wide as its span. */
export default function PageCanvas(props: CanvasProps) {
  const { page, onLayout } = props
  const regions = regionsOf(page)
  if (!regions.length) return <div data-testid={`page-${page.id}`}><WidgetGrid {...props} widgets={page.widgets} cols={24} /></div>
  // A region reports only its own items; merge them back into the page layout.
  const regionLayout = (region: string) => (next: LayoutItem[]) => {
    const inRegion = new Set(page.widgets.filter(row => row.region === region).map(row => row.id))
    onLayout?.([...page.layout.filter(item => !inRegion.has(item.i)), ...next])
  }
  return <div className="grid grid-cols-24 gap-3" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }} data-testid={`page-${page.id}`}>
    {regions.map(region => <RegionBox key={region.id} region={region} {...props} widgets={page.widgets.filter(row => row.region === region.id)} onLayout={regionLayout(region.id)} />)}
  </div>
}

function RegionBox({ region, editing, activeRegion, onActiveRegion, onAddToRegion, ...rest }: CanvasProps & { region: Region; widgets: Widget[] }) {
  const active = editing && activeRegion === region.id
  return <section style={{ gridColumn: `span ${region.span} / span ${region.span}` }} aria-label={`${region.tag} ${region.label}`} data-testid={`region-${region.id}`}
    onMouseDown={() => editing && onActiveRegion?.(region.id)}
    className={`min-w-0 rounded-lg border bg-white/60 ${active ? 'border-blue-400 ring-2 ring-blue-100' : 'border-slate-200'}`}>
    <header className="flex items-center gap-2 px-3 pt-2 text-xs">
      <span className="rounded bg-blue-50 px-1.5 py-0.5 font-semibold tracking-wide text-blue-700">{region.tag}</span><span className="text-slate-600">{region.label}</span>
      {editing && <button type="button" className="ml-auto text-blue-700 hover:underline" onClick={() => onAddToRegion?.(region.id)}>＋ 组件</button>}
    </header>
    {rest.widgets.length === 0 && editing && <p className="m-3 rounded border border-dashed p-4 text-center text-xs text-slate-400">点“＋ 组件”或先选中本区域，再从左侧组件库添加</p>}
    <WidgetGrid {...rest} editing={editing} cols={region.span} />
  </section>
}

/** The grid reports its layout on mount and after every render; only real moves may become an edit (and an undo step). */
function moved(current: LayoutItem[], next: Layout) {
  const before = new Map(current.map(item => [item.i, item]))
  return next.length !== current.length || next.some(item => { const old = before.get(item.i); return !old || old.x !== item.x || old.y !== item.y || old.w !== item.w || old.h !== item.h })
}

function WidgetGrid({ page, rt, editing = false, selected, onSelect, onLayout, widgets, cols }: CanvasProps & { widgets: Widget[]; cols: number }) {
  const { width, containerRef, mounted } = useContainerWidth()
  const layout = widgets.map(widget => page.layout.find(item => item.i === widget.id) ?? { i: widget.id, x: 0, y: 0, w: Math.min(cols, specOf(widget.type)?.w ?? 8), h: specOf(widget.type)?.h ?? 6 })
  return <div ref={containerRef} className="min-h-[60px]">
    {mounted && <GridLayout width={width} layout={layout} gridConfig={{ cols, rowHeight: ROW, margin: [12, 12] }}
      dragConfig={{ enabled: editing, handle: '.widget-handle' }} resizeConfig={{ enabled: editing }}
      onLayoutChange={(next: Layout) => { if (editing && moved(layout, next)) onLayout?.(next.map(item => ({ i: item.i, x: item.x, y: item.y, w: item.w, h: item.h }))) }}>
      {widgets.map(widget => <div key={widget.id} data-testid={`widget-${widget.id}`} onMouseDown={() => editing && onSelect?.(widget.id)}
        className={`flex flex-col overflow-hidden rounded border bg-white ${editing && selected === widget.id ? 'border-blue-500 ring-2 ring-blue-200' : 'border-slate-200'}`}>
        {(widget.title || editing) && <div className={`widget-handle flex items-center gap-2 border-b px-3 py-1.5 text-xs font-semibold text-slate-600 ${editing ? 'cursor-move bg-slate-50' : ''}`}>
          <span className="truncate">{widget.title || specOf(widget.type)?.label}</span>{editing && <><button type="button" className="ml-auto cursor-pointer font-normal text-blue-700" aria-label={`编辑组件 ${widget.title || widget.id}`} onMouseDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); onSelect?.(widget.id) }}>配置</button><span className="font-normal text-slate-400">{widget.id}</span></>}</div>}
        <div className="min-h-0 flex-1 overflow-auto p-2"><WidgetView widget={widget} rt={{ ...rt, editing }} /></div>
      </div>)}
    </GridLayout>}
  </div>
}
