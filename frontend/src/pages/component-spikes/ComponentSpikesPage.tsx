import { useEffect, useMemo, useRef, useState } from "react";
import { QueryBuilder, formatQuery, type Field, type RuleGroupType } from "react-querybuilder";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import * as echarts from "echarts";

import "./component-spikes.css";

type ObjectRow = {
  id: string;
  type: string;
  status: "Active" | "At risk" | "Planned";
  value: number;
  updated: string;
};

const SAMPLE_ROWS: ObjectRow[] = [
  { id: "order-1042", type: "Purchase Order", status: "Active", value: 128400, updated: "2026-09-25" },
  { id: "order-1041", type: "Purchase Order", status: "At risk", value: 84200, updated: "2026-09-24" },
  { id: "order-1038", type: "Purchase Order", status: "Planned", value: 196000, updated: "2026-09-23" },
];
const spikeTableFeatures = tableFeatures({});
const spikeColumnHelper = createColumnHelper<typeof spikeTableFeatures, ObjectRow>();

const fields: Field[] = [
  { name: "status", label: "Status", valueEditorType: "select", values: [
    { name: "Active", label: "Active" },
    { name: "At risk", label: "At risk" },
    { name: "Planned", label: "Planned" },
  ] },
  { name: "value", label: "Value", inputType: "number" },
  { name: "updated", label: "Updated", inputType: "date" },
];

const initialQuery: RuleGroupType = {
  combinator: "and",
  rules: [
    { field: "status", operator: "=", value: "At risk" },
    { field: "value", operator: ">", value: "50000" },
  ],
};

function QueryBuilderSpike() {
  const [query, setQuery] = useState<RuleGroupType>(initialQuery);
  const serialized = useMemo(() => formatQuery(query, "json_without_ids"), [query]);
  return (
    <section className="spike-card" aria-labelledby="query-builder-spike">
      <div className="spike-card-header">
        <div>
          <p className="spike-eyebrow">01 · Filter AST</p>
          <h2 id="query-builder-spike">React Query Builder</h2>
        </div>
        <span className="spike-status">narrow role</span>
      </div>
      <p className="spike-copy">验证嵌套条件编辑和 AST 导出。这里的 JSON 只是 adapter preview，不是 Object Set 后端合同。</p>
      <div className="query-builder-shell">
        <QueryBuilder fields={fields} query={query} onQueryChange={setQuery} showCombinatorsBetweenRules />
      </div>
      <pre className="spike-code" aria-label="query AST preview">{serialized}</pre>
    </section>
  );
}

function TableSpike() {
  const [rows, setRows] = useState(SAMPLE_ROWS);
  const [selected, setSelected] = useState<string[]>([]);
  const columns = useMemo(() => spikeColumnHelper.columns([
    spikeColumnHelper.display({
      id: "select",
      header: "",
      cell: ({ row }: { row: { original: ObjectRow } }) => (
        <input
          aria-label={`Select ${row.original.id}`}
          type="checkbox"
          checked={selected.includes(row.original.id)}
          onChange={() => setSelected((current) => current.includes(row.original.id)
            ? current.filter((id) => id !== row.original.id)
            : [...current, row.original.id])}
        />
      ),
    }),
    spikeColumnHelper.accessor("id", { header: "Object ID" }),
    spikeColumnHelper.accessor("type", { header: "Type" }),
    spikeColumnHelper.accessor("status", { header: "Status" }),
    spikeColumnHelper.accessor("value", { header: "Value", cell: (info) => `$${info.getValue().toLocaleString()}` }),
    spikeColumnHelper.accessor("updated", { header: "Updated" }),
  ]), [selected]);
  const table = useTable({ features: spikeTableFeatures, data: rows, columns });

  return (
    <section className="spike-card" aria-labelledby="table-spike">
      <div className="spike-card-header">
        <div>
          <p className="spike-eyebrow">02 · Headless table</p>
          <h2 id="table-spike">TanStack Table</h2>
        </div>
        <span className="spike-status">narrow role</span>
      </div>
      <p className="spike-copy">验证自有 markup、row selection 和 server-side adapter 边界。分页、权限和 live consistency 仍由项目负责。</p>
      <div className="table-toolbar">
        <span>{selected.length} selected</span>
        <button type="button" onClick={() => setRows((current) => [...current])}>Refresh adapter</button>
      </div>
      <div className="table-scroll">
        <table className="spike-table">
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{header.isPlaceholder ? null : <table.FlexRender header={header} />}</th>)}</tr>)}</thead>
          <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getAllCells().map((cell) => <td key={cell.id}><table.FlexRender cell={cell} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

function ChartSpike() {
  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current);
    chart.setOption({
      animation: false,
      color: ["#2563eb", "#f97316"],
      tooltip: { trigger: "axis" },
      legend: { data: ["Actual", "Scenario preview"], top: 0, right: 0 },
      grid: { left: 38, right: 18, top: 42, bottom: 28 },
      xAxis: { type: "category", data: ["Sep 19", "Sep 20", "Sep 21", "Sep 22", "Sep 23", "Sep 24", "Sep 25"] },
      yAxis: { type: "value", axisLabel: { formatter: "${value}k" } },
      series: [
        { name: "Actual", type: "line", smooth: true, data: [72, 78, 74, 86, 91, 89, 96] },
        { name: "Scenario preview", type: "line", smooth: true, lineStyle: { type: "dashed" }, data: [72, 78, 74, 86, 91, 97, 108] },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, []);
  return (
    <section className="spike-card" aria-labelledby="echarts-spike">
      <div className="spike-card-header">
        <div>
          <p className="spike-eyebrow">03 · Rendering adapter</p>
          <h2 id="echarts-spike">Apache ECharts</h2>
        </div>
        <span className="spike-status">narrow role</span>
      </div>
      <p className="spike-copy">验证 Actual/Scenario overlay、tooltip 和 resize。数据时间语义、权限、降级状态不由图表库决定。</p>
      <div ref={chartRef} className="chart-canvas" role="img" aria-label="Actual and Scenario preview time series" />
    </section>
  );
}

export default function ComponentSpikesPage() {
  return (
    <div className="wb-page max-w-[1280px]">
      <div className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Integration lab</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-900">开源组件 Spike</h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-600">三个组件都被限制在可替换的 UI adapter 边界内。本页面不读取生产 Ontology 数据，也不改变 Object Set、Action 或 Scenario 语义。</p>
      </div>
      <div className="spike-grid">
        <QueryBuilderSpike />
        <TableSpike />
        <ChartSpike />
      </div>
    </div>
  );
}
