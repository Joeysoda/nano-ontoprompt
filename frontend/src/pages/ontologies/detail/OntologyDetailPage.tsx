import React, { lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ontologyApi } from "@/api/ontologies";
import { apiClientV2, getApiErrorStatus } from "@/api/client";
import StatusBadge from "@/components/StatusBadge";
import EntitiesTab from "./tabs/EntitiesTab";
import LogicTab from "./tabs/LogicTab";
import AuditTab from "./tabs/AuditTab";
import ReasoningTab from "./tabs/ReasoningTab";
import DecisionsTab from "./tabs/DecisionsTab";
import AgentDecisionTab from "./tabs/AgentDecisionTab";
import ManufacturingDataTab from "./tabs/ManufacturingDataTab";
import LogicAssetsTab from "./tabs/LogicAssetsTab";
import WhatIfTab from "./tabs/WhatIfTab";
import OntologyEditorPanel from "./OntologyEditorPanel";
import ChangeHistoryDrawer from "./ChangeHistoryDrawer";
import SemanticControlPanel from "./SemanticControlPanel";
import { useAuthStore } from "@/stores/authStore";

const GraphTab = lazy(() => import("./tabs/GraphTabV2"));
const DataModelTab = lazy(() => import("./tabs/DataModelTab"));
const DynamicEvolutionTab = lazy(() => import("./tabs/DynamicEvolutionTab"));
const ObjectQueryTab = lazy(() => import("./tabs/ObjectQueryTab"));
const AlertsTab = lazy(() => import("./tabs/AlertsTab"));
const RulesTab = lazy(() => import("./tabs/RulesTab"));
const OverviewTab = lazy(() => import("./tabs/OverviewTab"));
const SourcesTab = lazy(() => import("./tabs/SourcesTab"));
type Tab = "overview" | "sources" | "graph" | "data_model" | "dynamic" | "entities" | "objects" | "logic" | "audit" | "what_if" | "reasoning" | "decisions" | "agent" | "manufacturing" | "logic-assets" | "alerts" | "rules";
// Like Ontology Manager's Discover view: land on what this ontology contains.
const DEFAULT_TAB: Tab = "overview";
type NavEntry = { key: Tab; label: string; hint?: string } | { href: string; label: string; hint?: string };

class OntologyCanvasBoundary extends React.Component<
  { children: React.ReactNode },
  { error: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    if (this.state.error)
      return (
        <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">
          <p className="font-medium">本体关系加载失败</p>
          <p className="mt-1 text-xs">{this.state.error}</p>
          <button
            onClick={() => this.setState({ error: "" })}
            className="mt-3 rounded border border-red-200 bg-white px-3 py-1.5 text-xs"
          >
            重试
          </button>
        </div>
      );
    return this.props.children;
  }
}

export default function OntologyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const role = useAuthStore((state) => state.user?.role);
  const canEdit = role === "admin" || role === "editor";
  const requested = searchParams.get("tab") || DEFAULT_TAB;
  const { data: ontology, isLoading, error, refetch } = useQuery({
    queryKey: ["ontology", id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  });
  const temporal = ontology?.data_class === "temporal";
  const commonTabs = ["overview", "sources", "graph", "data_model", "entities", "objects", "logic", "audit", "reasoning", "decisions", "agent", "manufacturing", "logic-assets", "alerts", "rules"];
  const ontologyList = useQuery({ queryKey: ["ontologies", "switcher"], queryFn: () => ontologyApi.list({ page_size: 200 }) });
  const openAlerts = useQuery({ queryKey: ["alerts", id, "open-count"], enabled: !!id,
    queryFn: () => apiClientV2.get<{ total: number }>(`/ontologies/${id}/alerts?status=open&limit=1`) });
  const validTabs = temporal ? [...commonTabs, "dynamic", "what_if"] : commonTabs;
  const activeTab: Tab = validTabs.includes(requested) ? (requested as Tab) : DEFAULT_TAB;
  useEffect(() => {
    if (isLoading || !id || validTabs.includes(requested))
      return;
    const entity = searchParams.get("entity");
    navigate(
      `/ontologies/${id}?tab=${entity ? "graph" : DEFAULT_TAB}${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`,
      { replace: true },
    );
  }, [id, isLoading, navigate, requested, searchParams, temporal]); // eslint-disable-line react-hooks/exhaustive-deps
  if (isLoading)
    return <div role="status" className="p-6 text-sm text-slate-500">正在加载本体</div>;
  if (error)
    return <div role="alert" className="p-6 text-sm text-red-600">
      {getApiErrorStatus(error) === 404 ? "未找到本体或无访问权限" : "本体加载失败，请重试。"}
      <button onClick={() => { void refetch(); }} className="ml-3 underline">重新加载</button>
    </div>;
  if (!ontology)
    return <div className="p-6 text-sm text-red-600">未找到本体</div>;
  // Ontology Manager layout: overview, resources (schema), data brought into
  // this ontology, the applications that run on it, then governance.
  const navGroups: Array<{ label: string; entries: NavEntry[] }> = [
    { label: "", entries: [{ key: "overview", label: "总览" }] },
    { label: "资源", entries: [
      { key: "entities", label: "对象类型" },
      { key: "graph", label: "链接与本体图" },
      { key: "data_model", label: "数据模型" },
      { key: "logic", label: "逻辑规则" },
      { key: "rules", label: "运行规则", hint: "定时检查与告警" },
      { key: "logic-assets", label: "函数" },
    ] },
    { label: "数据", entries: [
      { key: "sources", label: "数据源", hint: "导入数据到这个本体" },
      { key: "manufacturing", label: "业务数据" },
      ...(temporal ? [{ key: "dynamic" as Tab, label: "动态演化（实验）" }] : []),
    ] },
    { label: "应用", entries: [
      { key: "objects", label: "对象浏览器", hint: "查找、筛选、关系图与操作" },
      { key: "alerts", label: "告警中心", hint: "规则命中的异常" },
      { href: `/ontologies/${id}/scenario-studies`, label: "情景研究", hint: "版本化的假设推演与已保存案例" },
      { key: "decisions", label: "决策与影响链" },
      { key: "reasoning", label: "推理验证" },
      { key: "agent", label: "Agent 决策" },
    ] },
    { label: "治理", entries: [{ key: "audit", label: "质量审查" }] },
  ];
  const select = (tab: Tab, extra = "") => {
    const entity = searchParams.get("entity");
    const at = searchParams.get("at");
    const runId = searchParams.get("run_id");
    const dynamicContext = tab === "dynamic" && runId ? `&run_id=${encodeURIComponent(runId)}${at ? `&at=${encodeURIComponent(at)}` : ""}` : "";
    navigate(
      `/ontologies/${id}?tab=${tab}${(tab === "graph" || tab === "data_model") && entity ? `&entity=${encodeURIComponent(entity)}` : ""}${dynamicContext}${extra ? `&${extra}` : ""}`,
      { replace: true },
    );
  };
  return (
    <div className="wb-page max-w-[1540px]">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <button
          onClick={() => navigate("/ontologies")}
          className="text-sm text-slate-500 hover:text-slate-900"
        >
          返回本体库
        </button>
        <span className="text-slate-300">/</span>
        <h1 className="text-xl font-semibold text-slate-900">
          {ontology.name}
        </h1>
        {(ontologyList.data?.items.length ?? 0) > 1 && <select aria-label="切换本体" value={id} onChange={(event) => navigate(`/ontologies/${event.target.value}`)}
          className="max-w-[220px] rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600">
          {ontologyList.data!.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>}
        {ontology.status && <StatusBadge status={ontology.status} />}
        <div className="ml-auto flex items-center gap-2"><details className="relative">
          <summary className="cursor-pointer list-none rounded-md bg-slate-900 px-3 py-2 text-xs text-white">新建 ▾</summary>
          <div className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-slate-200 bg-white p-1 text-sm shadow-lg" role="menu">
            {[["添加数据", () => select("sources")], ["运行规则", () => select("rules")], ["情景研究", () => navigate(`/ontologies/${id}/scenario-studies`)]].map(([label, run]) =>
              <button key={label as string} type="button" role="menuitem" onClick={(event) => { (event.currentTarget.closest("details") as HTMLDetailsElement).open = false; (run as () => void)(); }}
                className="block w-full rounded px-3 py-1.5 text-left text-slate-700 hover:bg-slate-100">{label as string}</button>)}
          </div>
        </details>{canEdit && <button onClick={() => setEditing((value) => !value)} className={`rounded-md border px-3 py-2 text-xs ${editing ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300 text-slate-700 hover:border-slate-600"}`}>{editing ? "关闭编辑" : "编辑本体"}</button>}<button onClick={() => setShowHistory(true)} className="rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-700 hover:border-slate-600">修改记录</button></div>
      </div>
      {editing && canEdit && <OntologyEditorPanel ontologyId={id!} onChanged={() => setRefreshKey((value) => value + 1)} />}
      {editing && canEdit && <SemanticControlPanel ontologyId={id!} />}
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
      <nav aria-label="本体导航" className="shrink-0 lg:sticky lg:top-4 lg:w-[196px]">
        {navGroups.map((group) => <div key={group.label || "home"} className="mb-4">
          {group.label && <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-[.16em] text-slate-400">{group.label}</p>}
          <ul className="flex flex-wrap gap-1 lg:block lg:space-y-0.5">{group.entries.map((entry) => {
            const active = "key" in entry && activeTab === entry.key;
            const badge = "key" in entry && entry.key === "alerts" && (openAlerts.data?.total ?? 0) > 0 ? openAlerts.data!.total : null;
            return <li key={entry.label}>
              <button type="button" onClick={() => ("key" in entry ? select(entry.key) : navigate(entry.href))} title={entry.hint}
                aria-current={active ? "page" : undefined}
                className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-1.5 text-left text-sm transition-colors ${active ? "bg-slate-900 font-medium text-white" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"}`}>
                <span className="truncate">{entry.label}{"href" in entry && <span aria-hidden="true" className="ml-1 text-[10px] text-slate-400">↗</span>}</span>
                {badge !== null && <span aria-label={`${badge} 条待处理告警`} className={`rounded-full px-1.5 text-[10px] font-semibold ${active ? "bg-white text-slate-900" : "bg-red-600 text-white"}`}>{badge}</span>}
              </button>
            </li>;
          })}</ul>
        </div>)}
      </nav>
      <div className="min-w-0 flex-1">
      {activeTab === "graph" && (
        <OntologyCanvasBoundary>
          <Suspense
            fallback={
              <div className="py-12 text-center text-sm text-slate-500">
                正在加载本体
              </div>
            }
          >
            <GraphTab key={refreshKey} ontologyId={id!} dataClass={ontology.data_class} />
          </Suspense>
        </OntologyCanvasBoundary>
      )}
      {activeTab === "data_model" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载数据模型</div>}><DataModelTab key={refreshKey} ontologyId={id!} dataClass={ontology.data_class} /></Suspense>}
      {activeTab === "dynamic" && temporal && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载动态演化</div>}><DynamicEvolutionTab ontologyId={id!} /></Suspense>}
      {activeTab === "entities" && <EntitiesTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "objects" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载对象查询</div>}><ObjectQueryTab key={id} ontologyId={id!} /></Suspense>}
      {activeTab === "logic" && <LogicTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "audit" && <AuditTab ontologyId={id!} />}
      {activeTab === "what_if" && temporal && <WhatIfTab ontologyId={id!} />}
      {activeTab === "reasoning" && <ReasoningTab key={id} ontologyId={id!} />}
      {activeTab === "decisions" && <DecisionsTab key={id} ontologyId={id!} />}
      {activeTab === "agent" && <AgentDecisionTab key={id} ontologyId={id!} />}
      {activeTab === "manufacturing" && <ManufacturingDataTab key={id} ontologyId={id!} />}
      {activeTab === "logic-assets" && <LogicAssetsTab key={id} ontologyId={id!} />}
      {activeTab === "overview" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载总览</div>}><OverviewTab ontologyId={id!} onOpen={(tab, extra) => select(tab as Tab, extra)} /></Suspense>}
      {activeTab === "sources" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载数据源</div>}><SourcesTab ontologyId={id!} dataClass={ontology.data_class} onOpen={(tab) => select(tab as Tab)} /></Suspense>}
      {activeTab === "alerts" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载告警中心</div>}><AlertsTab ontologyId={id!} onOpenRules={() => select("rules")} /></Suspense>}
      {activeTab === "rules" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载运行规则</div>}><RulesTab ontologyId={id!} onOpenAlerts={() => select("alerts")} /></Suspense>}
      </div>
      </div>
      {showHistory && <ChangeHistoryDrawer ontologyId={id!} onClose={() => setShowHistory(false)} />}
    </div>
  );
}
