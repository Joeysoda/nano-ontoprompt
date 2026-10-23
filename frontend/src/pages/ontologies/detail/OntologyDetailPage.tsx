import React, { lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ontologyApi } from "@/api/ontologies";
import { getApiErrorStatus } from "@/api/client";
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
import { NAV_ENTRIES } from "@/components/navigation";

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
  // Sections are picked from the primary sidebar (docs/adr/0001-flat-task-navigation.md);
  // this page only renders the chosen one for the current ontology.
  const sectionLabel = NAV_ENTRIES.find((entry) => entry.tab === activeTab)?.label
    ?? ({ what_if: "What-if（旧版）" } as Record<string, string>)[activeTab] ?? "";
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
        <div className="min-w-0">
          <p className="truncate text-xs text-slate-500">{ontology.name}</p>
          <h1 className="text-xl font-semibold text-slate-900">{sectionLabel}</h1>
        </div>
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
      {showHistory && <ChangeHistoryDrawer ontologyId={id!} onClose={() => setShowHistory(false)} />}
    </div>
  );
}
