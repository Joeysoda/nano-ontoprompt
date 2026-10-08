import React from "react";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import Layout from "@/components/Layout";
const LoginPage = React.lazy(() => import("@/pages/login/LoginPage"));
const RegisterPage = React.lazy(() => import("@/pages/register/RegisterPage"));
const OverviewPage = React.lazy(() => import("@/pages/overview/OverviewPage"));
const OntologyListPage = React.lazy(() => import("@/pages/ontologies/list/OntologyListPage"));
const ModelsPage = React.lazy(() => import("@/pages/models/ModelsPage"));
const SettingsPage = React.lazy(() => import("@/pages/settings/SettingsPage"));
const PipelinesLayout = React.lazy(() => import("@/pages/pipelines/PipelinesLayout"));
const PipelineListPage = React.lazy(() => import("@/pages/pipelines/PipelineListPage"));
const PipelineBuilderPage = React.lazy(() => import("@/pages/pipelines/builder/PipelineBuilderPage"));
const ConnectionsTab = React.lazy(() => import("@/pages/pipelines/connections/ConnectionsTab"));
const DatasetsTab = React.lazy(() => import("@/pages/pipelines/datasets/DatasetsTab"));
const TransformsTab = React.lazy(() => import("@/pages/pipelines/transforms/TransformsTab"));
const CuratedTab = React.lazy(() => import("@/pages/pipelines/curated/CuratedTab"));
const DataManagementPage = React.lazy(() => import("@/pages/data-management/DataManagementPage"));
const DatasetOntologyEntryPage = React.lazy(() => import("@/pages/data-management/DatasetOntologyEntryPage"));
const StructuredDataPage = React.lazy(() => import("@/pages/data-management/structured/StructuredDataPage"));
const MultimodalDataPage = React.lazy(() => import("@/pages/data-management/multimodal/MultimodalDataPage"));
const TemporalConstructionWizard = React.lazy(() => import("@/pages/data-management/temporal/TemporalConstructionWizard"));
const TemporalWorkbenchPage = React.lazy(() => import("@/pages/data-management/temporal/TemporalWorkbenchPage"));
const TemporalReplayPage = React.lazy(() => import("@/pages/data-management/temporal/TemporalReplayPage"));
const DynamicDataPage = React.lazy(() => import("@/pages/data-management/dynamic/DynamicDataPage"));
const BenchmarksPage = React.lazy(() => import("@/pages/benchmarks/BenchmarksPage"));
const WhatIfWorkbenchPage = React.lazy(() => import("@/pages/what-if/SupplierStudyPage"));
const WhatIfDemoLandingPage = React.lazy(() => import("@/pages/what-if/SupplierStudyPage").then(module => ({ default: module.WhatIfDemoLandingPage })));
const GenericScenarioWorkbenchPage = React.lazy(() => import("@/pages/what-if/GenericScenarioWorkbenchPage"));
const ScenarioCatalogPage = React.lazy(() => import("@/pages/what-if/GenericScenarioWorkbenchPage").then(module => ({ default: module.ScenarioCatalogPage })));
const ComponentSpikesPage = React.lazy(() => import("@/pages/component-spikes/ComponentSpikesPage"));
const OntologyDetailPage = React.lazy(() => import("@/pages/ontologies/detail/OntologyDetailPage"));

class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; message?: string }
> {
  state = { hasError: false, message: "" };
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error.message };
  }
  render() {
    if (this.state.hasError)
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 p-6">
          <div className="bg-white border rounded-xl p-6 max-w-lg">
            <h2 className="font-semibold">页面加载失败</h2>
            <p className="text-sm text-gray-600 mt-2">
              {this.state.message || "发生未知错误，请刷新或返回数据管理。"}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 bg-black text-white rounded px-4 py-2 text-sm"
            >
              重新加载
            </button>
          </div>
        </div>
      );
    return this.props.children;
  }
}

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

// The local demonstration is intentionally login-free. A deployed instance
// can set VITE_AUTH_MODE=jwt and regain the existing login surface.
const LOCAL_SINGLE_USER = import.meta.env.VITE_AUTH_MODE === 'local_single_user'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  return LOCAL_SINGLE_USER || token ? <Layout>{children}</Layout> : <Navigate to="/login" replace />;
}

/** Legacy detail links are retained as safe entry points, but the old
 * detail forms are no longer part of the workbench surface. */
function LegacyOntologyRedirect({ tab = "graph" }: { tab?: "graph" | "entities" | "logic" }) {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={id ? `/ontologies/${id}?tab=${tab}` : "/ontologies"} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AppErrorBoundary>
          <React.Suspense fallback={<div role="status" className="p-6 text-sm text-slate-500">正在加载页面</div>}><Routes>
            <Route path="/login" element={LOCAL_SINGLE_USER ? <Navigate to="/overview" replace /> : <LoginPage />} />
            <Route path="/register" element={LOCAL_SINGLE_USER ? <Navigate to="/overview" replace /> : <RegisterPage />} />
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/demo" element={<Navigate to="/overview" replace />} />
            <Route
              path="/overview"
              element={
                <ProtectedRoute>
                  <OverviewPage />
                </ProtectedRoute>
              }
            />

            {/* ── 数据管理 ── */}
            <Route
              path="/data"
              element={
                <ProtectedRoute>
                  <DataManagementPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal"
              element={
                <ProtectedRoute>
                  <DatasetOntologyEntryPage dataClass="temporal" />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal/build"
              element={
                <ProtectedRoute>
                  <TemporalConstructionWizard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal/runs/:runId"
              element={
                <ProtectedRoute>
                  <TemporalWorkbenchPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal/replays/:replayId"
              element={
                <ProtectedRoute>
                  <TemporalReplayPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/regular"
              element={<Navigate to="/data" replace />}
            />
            <Route
              path="/data/regular/build"
              element={<Navigate to="/data" replace />}
            />
            <Route
              path="/data/multimodal"
              element={
                <ProtectedRoute>
                  <DatasetOntologyEntryPage dataClass="multimodal" />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/multimodal/build"
              element={
                <ProtectedRoute>
                  <MultimodalDataPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/dynamic"
              element={
                <ProtectedRoute>
                  <DynamicDataPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/dynamic/build"
              element={
                <ProtectedRoute>
                  <DynamicDataPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal/new"
              element={<Navigate to="/data/temporal/build" replace />}
            />
            <Route
              path="/data/structured"
              element={
                <ProtectedRoute>
                  <StructuredDataPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/pipelines"
              element={
                <ProtectedRoute>
                  <PipelinesLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<PipelineListPage />} />
              <Route path="connections" element={<ConnectionsTab />} />
              <Route path="datasets" element={<DatasetsTab />} />
              <Route path="transforms" element={<TransformsTab />} />
              <Route path="curated" element={<CuratedTab />} />
            </Route>
            <Route
              path="/data/pipelines/:pipelineId"
              element={
                <ProtectedRoute>
                  <PipelineBuilderPage />
                </ProtectedRoute>
              }
            />

            {/* Legacy redirect — keep old /pipelines URLs working */}
            <Route
              path="/pipelines"
              element={<Navigate to="/data/pipelines" replace />}
            />
            <Route
              path="/pipelines/*"
              element={<Navigate to="/data/pipelines" replace />}
            />

            <Route
              path="/ontologies"
              element={
                <ProtectedRoute>
                  <OntologyListPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/ontologies/new"
              element={
                <ProtectedRoute>
                  <Navigate to="/data" replace />
                </ProtectedRoute>
              }
            />
            <Route
              path="/ontologies/:id"
              element={
                <ProtectedRoute>
                  <React.Suspense fallback={<div role="status" className="p-6 text-sm text-slate-500">正在加载本体工作台</div>}><OntologyDetailPage /></React.Suspense>
                </ProtectedRoute>
              }
            />
            <Route
              path="/what-if"
              element={<ProtectedRoute><WhatIfDemoLandingPage /></ProtectedRoute>}
            />
            <Route
              path="/scenarios"
              element={<ProtectedRoute><ScenarioCatalogPage /></ProtectedRoute>}
            />
            <Route
              path="/ontologies/:id/scenarios"
              element={<ProtectedRoute><GenericScenarioWorkbenchPage /></ProtectedRoute>}
            />
            <Route
              path="/ontologies/:id/what-if"
              element={<ProtectedRoute><WhatIfWorkbenchPage /></ProtectedRoute>}
            />
            <Route
              path="/ontologies/:id/entities/:eid"
              element={
                <ProtectedRoute>
                  <LegacyOntologyRedirect tab="entities" />
                </ProtectedRoute>
              }
            />
            <Route
              path="/ontologies/:id/logic/:lid"
              element={
                <ProtectedRoute>
                  <LegacyOntologyRedirect tab="logic" />
                </ProtectedRoute>
              }
            />
            <Route
              path="/ontologies/:id/actions/:aid"
              element={
                <ProtectedRoute>
                  <LegacyOntologyRedirect />
                </ProtectedRoute>
              }
            />
            <Route
              path="/models"
              element={
                <ProtectedRoute>
                  <ModelsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/benchmarks"
              element={
                <ProtectedRoute>
                  <BenchmarksPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/component-spikes"
              element={<ProtectedRoute><React.Suspense fallback={<div className="p-6 text-sm text-slate-500">Loading component lab…</div>}><ComponentSpikesPage /></React.Suspense></ProtectedRoute>}
            />
          </Routes></React.Suspense>
        </AppErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
