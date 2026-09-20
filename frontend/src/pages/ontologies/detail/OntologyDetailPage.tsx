import React, { lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ontologyApi } from "@/api/ontologies";
import StatusBadge from "@/components/StatusBadge";
import EntitiesTab from "./tabs/EntitiesTab";
import LogicTab from "./tabs/LogicTab";
import AuditTab from "./tabs/AuditTab";
import DataModelTab from "./tabs/DataModelTab";
import WhatIfTab from "./tabs/WhatIfTab";
import DynamicEvolutionTab from "./tabs/DynamicEvolutionTab";
import OntologyEditorPanel from "./OntologyEditorPanel";
import ChangeHistoryDrawer from "./ChangeHistoryDrawer";

const GraphTab = lazy(() => import("./tabs/GraphTabV2"));
type Tab = "graph" | "data_model" | "dynamic" | "entities" | "logic" | "audit" | "what_if";

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
  const { data: ontology, isLoading } = useQuery({
    queryKey: ["ontology", id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  });
  const temporal = ontology?.data_class === "temporal";
  const validTabs = temporal ? ["graph", "data_model", "dynamic", "entities", "logic", "audit", "what_if"] : ["graph", "data_model", "entities", "logic", "audit"];
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
    return <div className="p-6 text-sm text-slate-500">正在加载本体</div>;
  if (!ontology)
    return <div className="p-6 text-sm text-red-600">未找到本体</div>;
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "graph", label: "本体" },
    { key: "data_model", label: "数据模型" },
    ...(temporal ? [{ key: "dynamic" as Tab, label: "动态演化" }] : []),
    { key: "entities", label: "实体" },
    { key: "logic", label: "逻辑规则" },
    { key: "audit", label: "质量审查" },
  ];
  if (temporal) tabs.push({ key: "what_if", label: "What-If 推演" });
  const select = (tab: Tab) => {
    const entity = searchParams.get("entity");
    const target = searchParams.get("target_instance_id");
    const episode = searchParams.get("episode_id");
    const at = searchParams.get("at");
    const mode = searchParams.get("mode");
    const runId = searchParams.get("run_id");
    const dynamicContext = tab === "dynamic" && runId ? `&run_id=${encodeURIComponent(runId)}${at ? `&at=${encodeURIComponent(at)}` : ""}` : "";
    const context = tab === "what_if" && target ? `&target_instance_id=${encodeURIComponent(target)}${episode ? `&episode_id=${encodeURIComponent(episode)}` : ""}${at ? `&at=${encodeURIComponent(at)}` : ""}${mode ? `&mode=${encodeURIComponent(mode)}` : ""}` : dynamicContext;
    navigate(
      `/ontologies/${id}?tab=${tab}${(tab === "graph" || tab === "data_model") && entity ? `&entity=${encodeURIComponent(entity)}` : ""}${context}`,
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
      <nav className="mb-5 flex gap-1 border-b border-slate-200">
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
      {activeTab === "data_model" && (
        <DataModelTab key={refreshKey} ontologyId={id!} dataClass={ontology.data_class} />
      )}
      {activeTab === "dynamic" && temporal && <DynamicEvolutionTab ontologyId={id!} />}
      {activeTab === "entities" && <EntitiesTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "logic" && <LogicTab key={refreshKey} ontologyId={id!} />}
      {activeTab === "audit" && <AuditTab ontologyId={id!} />}
      {activeTab === "what_if" && temporal && <WhatIfTab ontologyId={id!} />}
      {showHistory && <ChangeHistoryDrawer ontologyId={id!} onClose={() => setShowHistory(false)} />}
    </div>
  );
}
