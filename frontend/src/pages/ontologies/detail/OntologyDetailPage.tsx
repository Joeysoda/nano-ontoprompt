import React, { lazy, Suspense, useEffect } from "react";
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
import ObjectQueryTab from "./tabs/ObjectQueryTab";

const GraphTab = lazy(() => import("./tabs/GraphTabV2"));
type Tab = "graph" | "entities" | "objects" | "logic" | "audit" | "reasoning" | "decisions" | "agent" | "manufacturing" | "logic-assets";

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
  const requested = searchParams.get("tab") || "graph";
  const activeTab: Tab =
    requested === "entities" || requested === "objects" || requested === "logic" || requested === "audit" || requested === "reasoning" || requested === "decisions"
      || requested === "agent" || requested === "manufacturing" || requested === "logic-assets"
      ? requested
      : "graph";
  useEffect(() => {
    if (!id || ["graph", "entities", "objects", "logic", "audit", "reasoning", "decisions", "agent", "manufacturing", "logic-assets"].includes(requested))
      return;
    const entity = searchParams.get("entity");
    navigate(
      `/ontologies/${id}?tab=graph${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`,
      { replace: true },
    );
  }, [id, navigate, requested, searchParams]);
  const { data: ontology, isLoading, error, refetch } = useQuery({
    queryKey: ["ontology", id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  });
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
    navigate(
      `/ontologies/${id}?tab=${tab}${tab === "graph" && entity ? `&entity=${encodeURIComponent(entity)}` : ""}`,
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
      </div>
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
            <GraphTab key={id} ontologyId={id!} />
          </Suspense>
        </OntologyCanvasBoundary>
      )}
      {activeTab === "entities" && <EntitiesTab ontologyId={id!} />}
      {activeTab === "objects" && <ObjectQueryTab key={id} ontologyId={id!} />}
      {activeTab === "logic" && <LogicTab ontologyId={id!} />}
      {activeTab === "audit" && <AuditTab ontologyId={id!} />}
      {activeTab === "reasoning" && <ReasoningTab key={id} ontologyId={id!} />}
      {activeTab === "decisions" && <DecisionsTab key={id} ontologyId={id!} />}
      {activeTab === "agent" && <AgentDecisionTab key={id} ontologyId={id!} />}
      {activeTab === "manufacturing" && <ManufacturingDataTab key={id} ontologyId={id!} />}
      {activeTab === "logic-assets" && <LogicAssetsTab key={id} ontologyId={id!} />}
    </div>
  );
}
