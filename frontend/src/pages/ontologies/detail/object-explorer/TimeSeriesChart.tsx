import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { apiClientV2 } from '@/api/client';
import { explorerError } from './runtime';

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

type Series = { series_id: string; unit: string; interpolation: string; points: Array<{ timestamp: string; value: number }>; page: { has_more: boolean; next_offset: number | null } };

export default function TimeSeriesChart({ ontologyId, dataViewId, seriesId }: { ontologyId: string; dataViewId: string; seriesId: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState(0);
  const result = useQuery({ queryKey: ['time-series', ontologyId, dataViewId, seriesId, offset],
    queryFn: ({ signal }) => apiClientV2.get<Series>(`/ontologies/${ontologyId}/object-query/data-views/${dataViewId}/time-series/${encodeURIComponent(seriesId)}`, { params: { offset, limit: 1000 }, signal }),
  });
  useEffect(() => {
    if (!ref.current || !result.data) return;
    const chart = echarts.init(ref.current);
    chart.setOption({ animation: false, grid: { left: 55, right: 12, top: 20, bottom: 55 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: result.data.points.map(point => point.timestamp), axisLabel: { rotate: 30 } },
      yAxis: { type: 'value', name: result.data.unit },
      series: [{ type: 'line', data: result.data.points.map(point => point.value), connectNulls: result.data.interpolation !== 'none', step: result.data.interpolation === 'step' }],
    });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(ref.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [result.data]);
  return <section aria-label={`时间序列 ${seriesId}`} className="border-t p-3"><p className="text-xs">{seriesId} · {result.data?.unit ?? '…'} · {result.data?.interpolation ?? '…'}</p>
    {result.isPending && <p role="status">正在加载时间序列…</p>}{result.error && <p role="alert">{explorerError(result.error)}</p>}
    <div ref={ref} className="h-56 w-full" role="img" aria-label="时间序列图" />
    <div className="flex gap-2"><button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 1000))}>上一段</button><button disabled={!result.data?.page.has_more} onClick={() => setOffset(result.data?.page.next_offset ?? offset)}>下一段</button></div>
  </section>;
}
