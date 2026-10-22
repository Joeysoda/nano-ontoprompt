import GridLayout, { useContainerWidth, type Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import WidgetView, { type Runtime } from './WidgetView'
import { specOf, type LayoutItem, type Page } from './spec'

const COLS = 24
const ROW = 32

/** One page of widgets on a 24-column grid; editable in the builder, static at runtime. */
export default function PageCanvas({ page, rt, editing = false, selected, onSelect, onLayout }: {
  page: Page; rt: Runtime; editing?: boolean; selected?: string; onSelect?: (id: string) => void; onLayout?: (layout: LayoutItem[]) => void
}) {
  const { width, containerRef, mounted } = useContainerWidth()
  const layout = page.widgets.map(widget => page.layout.find(item => item.i === widget.id) ?? { i: widget.id, x: 0, y: 0, w: specOf(widget.type)?.w ?? 8, h: specOf(widget.type)?.h ?? 6 })
  return <div ref={containerRef} className="min-h-[200px]" data-testid={`page-${page.id}`}>
    {mounted && <GridLayout width={width} layout={layout} gridConfig={{ cols: COLS, rowHeight: ROW, margin: [12, 12] }}
      dragConfig={{ enabled: editing, handle: '.widget-handle' }} resizeConfig={{ enabled: editing }}
      onLayoutChange={(next: Layout) => editing && onLayout?.(next.map(item => ({ i: item.i, x: item.x, y: item.y, w: item.w, h: item.h })))}>
      {page.widgets.map(widget => <div key={widget.id} data-testid={`widget-${widget.id}`} onMouseDown={() => editing && onSelect?.(widget.id)}
        className={`flex flex-col overflow-hidden rounded border bg-white ${editing && selected === widget.id ? 'border-blue-500 ring-2 ring-blue-200' : 'border-slate-200'}`}>
        {(widget.title || editing) && <div className={`widget-handle flex items-center gap-2 border-b px-3 py-1.5 text-xs font-semibold text-slate-600 ${editing ? 'cursor-move bg-slate-50' : ''}`}>
          <span className="truncate">{widget.title || specOf(widget.type)?.label}</span>{editing && <span className="ml-auto font-normal text-slate-400">{widget.id}</span>}</div>}
        <div className="min-h-0 flex-1 overflow-auto p-2"><WidgetView widget={widget} rt={{ ...rt, editing }} /></div>
      </div>)}
    </GridLayout>}
  </div>
}
