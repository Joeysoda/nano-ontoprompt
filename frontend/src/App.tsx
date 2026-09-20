import React from "react";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import Layout from "@/components/Layout";
import LoginPage from "@/pages/login/LoginPage";
import RegisterPage from "@/pages/register/RegisterPage";
import OverviewPage from "@/pages/overview/OverviewPage";
import OntologyListPage from "@/pages/ontologies/list/OntologyListPage";
import OntologyDetailPage from "@/pages/ontologies/detail/OntologyDetailPage";
import ModelsPage from "@/pages/models/ModelsPage";
import SettingsPage from "@/pages/settings/SettingsPage";
import PipelinesLayout from "@/pages/pipelines/PipelinesLayout";
import PipelineListPage from "@/pages/pipelines/PipelineListPage";
import PipelineBuilderPage from "@/pages/pipelines/builder/PipelineBuilderPage";
import ConnectionsTab from "@/pages/pipelines/connections/ConnectionsTab";
import DatasetsTab from "@/pages/pipelines/datasets/DatasetsTab";
import TransformsTab from "@/pages/pipelines/transforms/TransformsTab";
import CuratedTab from "@/pages/pipelines/curated/CuratedTab";
import DataManagementPage from "@/pages/data-management/DataManagementPage";
import StructuredDataPage from "@/pages/data-management/structured/StructuredDataPage";
import RegularDataPage from "@/pages/data-management/regular/RegularDataPage";
import MultimodalDataPage from "@/pages/data-management/multimodal/MultimodalDataPage";
import TemporalConstructionWizard from "@/pages/data-management/temporal/TemporalConstructionWizard";
import TemporalWorkbenchPage from "@/pages/data-management/temporal/TemporalWorkbenchPage";
import TemporalReplayPage from "@/pages/data-management/temporal/TemporalReplayPage";
import DynamicDataPage from "@/pages/data-management/dynamic/DynamicDataPage";
import BenchmarksPage from "@/pages/benchmarks/BenchmarksPage";

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
          <Routes>
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
                  <TemporalConstructionWizard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/temporal/new"
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
              element={
                <ProtectedRoute>
                  <RegularDataPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/data/multimodal"
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
                  <OntologyDetailPage />
                </ProtectedRoute>
              }
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
          </Routes>
        </AppErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
