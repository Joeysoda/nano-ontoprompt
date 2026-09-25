import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import type { ObjectPanelRecord } from "./ObjectPanel";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

function bucketLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "无值";
  if (typeof value === "object") return "结构化值";
  return String(value);
}

export default function ObjectDistributionChart({ objects, property }: { objects: ObjectPanelRecord[]; property: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const buckets = useMemo(() => {
    const counts = new Map<string, number>();
    for (const object of objects) {
      const label = bucketLabel(object.properties[property]);
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    return [...counts.entries()].sort((left, right) => right[1] - left[1]).slice(0, 12);
  }, [objects, property]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animationDuration: 180,
      color: ["#2563eb"],
      grid: { left: 38, right: 12, top: 12, bottom: 54 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "category", data: buckets.map(([label]) => label), axisLabel: { interval: 0, rotate: buckets.length > 5 ? 30 : 0, overflow: "truncate", width: 84 } },
      yAxis: { type: "value", minInterval: 1 },
      series: [{ type: "bar", data: buckets.map(([, count]) => count), barMaxWidth: 36 }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [buckets]);

  if (!property) return <div className="oe-chart-empty">当前对象类型没有可用于预览的属性。</div>;
  return <div ref={ref} className="oe-chart" role="img" aria-label={`${property} 当前页分布`} />;
}
