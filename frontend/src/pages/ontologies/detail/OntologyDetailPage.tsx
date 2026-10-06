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
import OntologyEditorPanel from "./OntologyEditorPanel";
import ChangeHistoryDrawer from "./ChangeHistoryDrawer";

const GraphTab = lazy(() => import("./tabs/GraphTabV2"));
const DataModelTab = lazy(() => import("./tabs/DataModelTab"));
const DynamicEvolutionTab = lazy(() => import("./tabs/DynamicEvolutionTab"));
const ObjectQueryTab = lazy(() => import("./tabs/ObjectQueryTab"));
type Tab = "graph" | "data_model" | "dynamic" | "entities" | "objects" | "logic" | "audit" | "reasoning" | "decisions" | "agent" | "manufacturing" | "logic-assets";

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
  const requested = searchParams.get("tab") || "graph";
  const { data: ontology, isLoading, error, refetch } = useQuery({
    queryKey: ["ontology", id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  });
  const temporal = ontology?.data_class === "temporal";
  const commonTabs = ["graph", "data_model", "entities", "objects", "logic", "audit", "reasoning", "decisions", "agent", "manufacturing", "logic-assets"];
  const validTabs = temporal ? [...commonTabs, "dynamic"] : commonTabs;
  const activeTab: Tab = validTabs.includes(requested) ? (requested as Tab) : "graph";
  useEffect(() => {
    if (isLoading || !id || validTabs.includes(requested))
      return;
    const entity = searchParams.get("entity");
    navigate(
      `/ontologies/${id}?tab=graph${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`,
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
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "graph", label: "本体" },
    { key: "data_model", label: "数据模型" },
    ...(temporal ? [{ key: "dynamic" as Tab, label: "动态演化（实验）" }] : []),
    { key: "entities", label: "实体" },
    { key: "objects", label: "Objects" },
    { key: "logic", label: "逻辑规则" },
    { key: "audit", label: "质量审查" },
    { key: "reasoning", label: "推理验证" },
    { key: "decisions", label: "决策与影响链" },
    { key: "agent", label: "Agent 决策" },
    { key: "manufacturing", label: "业务数据" },
    { key: "logic-assets", label: "逻辑绑定" },
  ];
  const select = (tab: Tab) => {
    const entity = searchParams.get("entity");
    const at = searchParams.get("at");
    const runId = searchParams.get("run_id");
    const dynamicContext = tab === "dynamic" && runId ? `&run_id=${encodeURIComponent(runId)}${at ? `&at=${encodeURIComponent(at)}` : ""}` : "";
    navigate(
      `/ontologies/${id}?tab=${tab}${(tab === "graph" || tab === "data_model") && entity ? `&entity=${encodeURIComponent(entity)}` : ""}${dynamicContext}`,
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
        {ontology.status && <StatusBadge status={ontology.status} />}
        <div className="ml-auto flex items-center gap-2"><button onClick={() => setEditing((value) => !value)} className={`rounded-md border px-3 py-2 text-xs ${editing ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300 text-slate-700 hover:border-slate-600"}`}>{editing ? "关闭编辑" : "编辑本体"}</button><button onClick={() => setShowHistory(true)} className="rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-700 hover:border-slate-600">修改记录</button></div>
      </div>
      {editing && <OntologyEditorPanel ontologyId={id!} onChanged={() => setRefreshKey((value) => value + 1)} />}
      <nav className="mb-5 flex flex-wrap gap-1 border-b border-slate-200">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => select(tab.key)}
            className={`border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${activeTab === tab.key ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"}`}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {activeTab === "graph" && (
        <OntologyCanvasBoundary>
          <Suspense
            fallback={
              <div className="py-12 text-center text-sm text-slate-500">
                正在加载本体
              </div>
            }
          >
            <GraphTab key={refreshKey} ontologyId={id!} />
          </Suspense>
        </OntologyCanvasBoundary>
      )}
      {activeTab === "data_model" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载数据模型</div>}><DataModelTab key={refreshKey} ontologyId={id!} dataClass={ontology.data_class} /></Suspense>}
      {activeTab === "dynamic" && temporal && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载动态演化</div>}><DynamicEvolutionTab ontologyId={id!} /></Suspense>}
      {activeTab === "entities" && <EntitiesTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "objects" && <Suspense fallback={<div role="status" className="py-12 text-center text-sm text-slate-500">正在加载 Objects</div>}><ObjectQueryTab key={id} ontologyId={id!} /></Suspense>}
      {activeTab === "logic" && <LogicTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "audit" && <AuditTab ontologyId={id!} />}
      {activeTab === "reasoning" && <ReasoningTab key={id} ontologyId={id!} />}
      {activeTab === "decisions" && <DecisionsTab key={id} ontologyId={id!} />}
      {activeTab === "agent" && <AgentDecisionTab key={id} ontologyId={id!} />}
      {activeTab === "manufacturing" && <ManufacturingDataTab key={id} ontologyId={id!} />}
      {activeTab === "logic-assets" && <LogicAssetsTab key={id} ontologyId={id!} />}
      {showHistory && <ChangeHistoryDrawer ontologyId={id!} onClose={() => setShowHistory(false)} />}
    </div>
  );
}
