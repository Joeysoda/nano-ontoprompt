import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleHelp,
  Database,
  Download,
  FileArchive,
  Image as ImageIcon,
  Loader2,
  Play,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  TriangleAlert,
  UploadCloud,
  Waypoints,
  X,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { apiClient, apiClientV2 } from "@/api/client";
import { constructionApi } from "@/api/construction";
import {
  MultimodalEvidenceWorkspace,
  type MultimodalEvidence,
} from "./MultimodalEvidenceWorkspace";

type Asset = {
  id: string;
  role: string;
  media_type: string;
  original_name?: string;
  mime_type?: string;
  checksum?: string;
  preview_url?: string;
  content_url?: string;
  preview_info_url?: string;
  pointcloud_url?: string;
  storage_uri?: string;
};
type Sample = {
  id: string;
  sample_id: string;
  sample_key: string;
  scene_id?: string;
  split?: string;
  label?: string;
  labels?: string[];
  metadata?: Record<string, unknown>;
  assets: Asset[];
};
type Dataset = {
  id: string;
  name: string;
  data_class: string;
  privacy_level: "standard" | "private";
  version_id?: string;
  manifest?: Record<string, any>;
};
type InstallTask = {
  id: string;
  task_id: string;
  source_id: string;
  status: string;
  progress?: { stage?: string; completed?: number; total?: number };
  dataset_id?: string;
  result?: Record<string, any>;
  error?: string;
  cancel_requested?: boolean;
};
type CatalogSource = {
  id: string;
  name: string;
  repository: string;
  revision: string;
  source_url: string;
  license: string;
  scene_count: number;
  sample_count: number;
  modalities: string[];
  download_policy: string;
  installed: boolean;
  dataset_id?: string;
  install_task?: InstallTask | null;
};
type Ontology = {
  id: string;
  name: string;
  domain?: string;
  data_class?: string;
  status?: string;
};
type Run = {
  id: string;
  ontology_id?: string;
  dataset_id?: string;
  mode?: string;
  config?: {
    privacy_level?: "standard" | "private";
    mode?: "create" | "append";
    selection?: {
      sample_ids?: string[];
      selected_assets?: string[];
    };
  };
  status: string;
  model_name?: string;
  progress?: Record<string, number | string>;
  metrics?: Record<string, unknown>;
  error?: string;
};
type MappingTask = {
  id?: string;
  status?: string;
  stage?: string;
  progress?: { pct?: number; stage?: string };
  trace?: Array<{
    step?: number;
    stage?: string;
    detail?: string;
    status?: string;
  }>;
  result?: { suggestions?: Record<string, unknown>[] };
  error?: string;
};

const STEPS = ["数据集", "内容选择", "处理配置", "本体映射", "确认构建"];
const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  waiting_for_model: "等待模型",
  cancelled: "已取消",
};
const roleLabel: Record<string, string> = {
  rgb: "RGB",
  depth: "深度",
  mask: "异常掩码",
  mask_visible: "可见掩码",
  point_cloud: "点云",
  metadata: "元数据",
  pdf: "PDF 证据",
  docx: "DOCX 证据",
};

function errorText(error: any) {
  return (
    error?.response?.data?.detail?.message ||
    error?.response?.data?.detail ||
    error?.detail ||
    error?.message ||
    "请求失败"
  );
}

function ProgressPill({ task }: { task: InstallTask | null }) {
  if (!task) return null;
  const completed = task.progress?.completed ?? 0;
  const total = task.progress?.total || 12;
  const active = ["queued", "running", "cancel_requested"].includes(
    task.status,
  );
  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span
        className={`wb-status ${task.status === "completed" ? "wb-status-success" : task.status === "failed" ? "wb-status-danger" : "wb-status-warning"}`}
      >
        {task.status === "completed"
          ? "已安装"
          : task.status === "failed"
            ? "安装失败"
            : task.status === "cancelled"
              ? "已取消"
              : "安装中"}
      </span>
      <span>
        {task.progress?.stage || "等待开始"} · {completed}/{total}
      </span>
      {active && <Loader2 size={13} className="animate-spin text-gray-400" />}
    </div>
  );
}

type PointCloudPayload = {
  points?: number[][];
  point_count?: number;
  source_point_count?: number;
  stride?: number;
  bounds?: { min?: number[]; max?: number[] };
};

/** Dependency-free bounded point-cloud viewer for the evidence canvas. */
function PointCloudPreview({ asset }: { asset: Asset }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{
    x: number;
    y: number;
    yaw: number;
    pitch: number;
  } | null>(null);
  const [payload, setPayload] = useState<PointCloudPayload | null>(null);
  const [camera, setCamera] = useState({ yaw: 0.45, pitch: -0.25, zoom: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    const endpoint =
      asset.pointcloud_url?.replace(/^\/api\/v2/, "") ||
      `/multimodal/assets/${asset.id}/pointcloud`;
    apiClientV2
      .get<PointCloudPayload>(endpoint, { params: { limit: 50000 } })
      .then((result) => {
        if (!cancelled) setPayload(result);
      })
      .catch((err: any) => {
        if (!cancelled)
          setError(err?.detail || err?.message || "点云预览加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [asset.id, asset.pointcloud_url]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const data = payload;
    const points = data?.points;
    if (!canvas || !data || !points?.length) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = rect.height;
    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, width, height);
    const bounds = data.bounds || {};
    const min = bounds.min || [0, 0, 0];
    const max = bounds.max || [1, 1, 1];
    const center = [0, 1, 2].map(
      (index) => ((min[index] || 0) + (max[index] || 0)) / 2,
    );
    const span = Math.max(
      1e-6,
      ...[0, 1, 2].map((index) => (max[index] || 0) - (min[index] || 0)),
    );
    const cy = Math.cos(camera.yaw);
    const sy = Math.sin(camera.yaw);
    const cp = Math.cos(camera.pitch);
    const sp = Math.sin(camera.pitch);
    const visible =
      points.length > 14000
        ? points.filter(
            (_, index) => index % Math.ceil(points.length / 14000) === 0,
          )
        : points;
    const projected: Array<{ x: number; y: number; z: number; color: string }> =
      [];
    for (const point of visible) {
      if (!point || point.length < 3) continue;
      const x = (point[0] - center[0]) / span;
      const y = (point[1] - center[1]) / span;
      const z = (point[2] - center[2]) / span;
      const rx = x * cy - z * sy;
      const rz = x * sy + z * cy;
      const ry = y * cp - rz * sp;
      const depth = y * sp + rz * cp;
      const perspective = 1.35 / (1.9 - depth);
      const px = width / 2 + rx * perspective * width * 0.82 * camera.zoom;
      const py = height / 2 - ry * perspective * height * 0.82 * camera.zoom;
      if (px < -4 || py < -4 || px > width + 4 || py > height + 4) continue;
      const shade = Math.max(0, Math.min(1, (depth + 0.5) / 1.1));
      projected.push({
        x: px,
        y: py,
        z: depth,
        color: `rgb(${35 + Math.round(25 * shade)}, ${100 + Math.round(75 * shade)}, ${170 + Math.round(60 * shade)})`,
      });
    }
    projected.sort((a, b) => a.z - b.z);
    for (const point of projected) {
      ctx.fillStyle = point.color;
      ctx.fillRect(point.x, point.y, 1.6, 1.6);
    }
    ctx.fillStyle = "#64748b";
    ctx.font = "10px system-ui";
    ctx.fillText(
      `${data.point_count || projected.length} 点 · 拖动旋转 · 滚轮缩放`,
      10,
      height - 10,
    );
  }, [payload, camera]);

  const reset = () => setCamera({ yaw: 0.45, pitch: -0.25, zoom: 1 });
  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      yaw: camera.yaw,
      pitch: camera.pitch,
    };
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const start = dragRef.current;
    if (!start) return;
    setCamera((current) => ({
      ...current,
      yaw: start.yaw + (event.clientX - start.x) * 0.012,
      pitch: Math.max(
        -1.4,
        Math.min(1.4, start.pitch + (event.clientY - start.y) * 0.012),
      ),
    }));
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    setCamera((current) => ({
      ...current,
      zoom: Math.max(
        0.45,
        Math.min(4, current.zoom * (event.deltaY > 0 ? 0.9 : 1.1)),
      ),
    }));
  };

  return (
    <div className="relative overflow-hidden rounded border border-slate-200 bg-slate-50">
      <canvas
        ref={canvasRef}
        className="h-full min-h-[190px] w-full cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
      />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-50/90 text-xs text-slate-500">
          <Loader2 size={15} className="mr-1 animate-spin" />
          加载点云
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-red-50/90 px-3 text-center text-xs text-red-700">
          {error}
        </div>
      )}
      <button
        type="button"
        onClick={reset}
        className="absolute right-2 top-2 rounded border border-slate-200 bg-white/90 px-2 py-1 text-[10px] text-slate-600 hover:bg-white"
      >
        重置视角
      </button>
    </div>
  );
}

export default function MultimodalDataPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const restoredRunId = searchParams.get("run");
  const importRef = useRef<HTMLInputElement>(null);
  const [catalog, setCatalog] = useState<CatalogSource[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selectedSampleIds, setSelectedSampleIds] = useState<string[]>([]);
  const [sceneFilter, setSceneFilter] = useState("all");
  const [labelFilter, setLabelFilter] = useState("all");
  const [selectedRoles, setSelectedRoles] = useState<string[]>([
    "rgb",
    "depth",
    "mask",
    "point_cloud",
    "metadata",
  ]);
  const [privacy, setPrivacy] = useState<"standard" | "private">("standard");
  const [sendFields, setSendFields] = useState<Record<string, boolean>>({
    metadata: true,
    labels: true,
    image_stats: true,
    point_stats: true,
  });
  const [ontologies, setOntologies] = useState<Ontology[]>([]);
  const [ontologyId, setOntologyId] = useState("");
  const [targetMode, setTargetMode] = useState<"create" | "append">("append");
  const [newOntologyName, setNewOntologyName] = useState("I-BADAS 多模态本体");
  const [modelStatus, setModelStatus] = useState<any>(null);
  const [step, setStep] = useState(0);
  const [task, setTask] = useState<InstallTask | null>(null);
  const [draftId, setDraftId] = useState("");
  const [mappingTaskId, setMappingTaskId] = useState("");
  const [mappingTask, setMappingTask] = useState<MappingTask | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mappingGenerated, setMappingGenerated] = useState(false);
  const [mappingSuggestions, setMappingSuggestions] = useState<
    Record<string, unknown>[]
  >([]);
  const [mappingConfirmed, setMappingConfirmed] = useState<number[]>([]);
  const [previewSampleId, setPreviewSampleId] = useState("");
  const [evidence, setEvidence] = useState<MultimodalEvidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [error, setError] = useState("");

  const selectedDataset = useMemo(
    () => datasets.find((item) => item.id === selectedDatasetId),
    [datasets, selectedDatasetId],
  );
  const filteredSamples = useMemo(
    () =>
      samples.filter(
        (sample) =>
          (sceneFilter === "all" || sample.scene_id === sceneFilter) &&
          (labelFilter === "all" || sample.label === labelFilter),
      ),
    [samples, sceneFilter, labelFilter],
  );
  const previewSample = useMemo(
    () =>
      samples.find((item) => item.id === previewSampleId) ||
      samples.find((item) => selectedSampleIds.includes(item.id)),
    [samples, previewSampleId, selectedSampleIds],
  );
  const scenes = useMemo(
    () =>
      Array.from(
        new Set(samples.map((item) => item.scene_id).filter(Boolean)),
      ) as string[],
    [samples],
  );
  const labels = useMemo(
    () =>
      Array.from(
        new Set(samples.map((item) => item.label).filter(Boolean)),
      ) as string[],
    [samples],
  );
  const standardConfigured = Boolean(modelStatus?.configured);

  const loadCatalog = async () => {
    setError("");
    try {
      const [result, ontologyResult, model] = await Promise.all([
        apiClientV2.get<{ sources: CatalogSource[]; datasets: Dataset[] }>(
          "/multimodal/catalog",
        ),
        apiClient.get<{ items: Ontology[] }>("/ontologies?page_size=100"),
        // Opening the page is not permission to call the cloud model. The
        // confirmed mapping task performs the real authorization check.
        apiClientV2.get("/multimodal/status?probe=false"),
      ]);
      const nextDatasets = result?.datasets || [];
      setCatalog(result?.sources || []);
      setDatasets(nextDatasets);
      // Append is reserved for a successfully materialised ontology.  Failed
      // or empty historical drafts remain in storage for auditability but do
      // not silently become a user's construction target.
      const matchingOntologies = (ontologyResult?.items || []).filter(
        (item) => item.data_class === "multimodal" && item.status === "created",
      );
      setOntologies(matchingOntologies);
      setOntologyId((current) =>
        matchingOntologies.some((item) => item.id === current) ? current : "",
      );
      setTargetMode((current) =>
        current === "append" && !matchingOntologies.length ? "create" : current,
      );
      setNewOntologyName((current) =>
        current === "I-BADAS 多模态本体" &&
        (ontologyResult?.items || []).some(
          (item) => item.name === "I-BADAS 多模态本体",
        )
          ? "I-BADAS 多模态本体（新建）"
          : current,
      );
      setModelStatus(model);
      const installed = nextDatasets.find(
        (item) => item.data_class === "multimodal",
      );
      setSelectedDatasetId((current) => current || installed?.id || "");
      const sourceTask = result?.sources?.[0]?.install_task;
      if (
        sourceTask &&
        ["queued", "running", "cancel_requested"].includes(sourceTask.status)
      )
        setTask(sourceTask);
    } catch (err: any) {
      setError(
        err?.detail?.message ||
          err?.detail ||
          err?.message ||
          "多模态目录加载失败",
      );
    } finally {
      setLoading(false);
    }
  };

  const loadSamples = async (datasetId: string) => {
    if (!datasetId) {
      setSamples([]);
      return;
    }
    try {
      const result = await apiClientV2.get<{ samples: Sample[] }>(
        `/multimodal/datasets/${datasetId}/samples`,
      );
      setSamples(result?.samples || []);
      setSelectedSampleIds((current) =>
        current.filter((id) =>
          (result?.samples || []).some((sample) => sample.id === id),
        ),
      );
    } catch (err: any) {
      setError(err?.detail || err?.message || "样例列表加载失败");
    }
  };

  useEffect(() => {
    loadCatalog();
  }, []);
  useEffect(() => {
    if (!restoredRunId || run?.id === restoredRunId) return;
    let cancelled = false;
    constructionApi
      .getRun(restoredRunId)
      .then((restored) => {
        if (cancelled) return;
        if (restored.mode !== "multimodal") {
          setError("该链接指向的不是多模态构建任务");
          return;
        }
        setRun(restored);
        if (restored.dataset_id) setSelectedDatasetId(restored.dataset_id);
        const recoveredSelection = restored.config?.selection;
        if (recoveredSelection?.sample_ids)
          setSelectedSampleIds(recoveredSelection.sample_ids);
        if (recoveredSelection?.selected_assets)
          setSelectedRoles(recoveredSelection.selected_assets);
        if (restored.config?.privacy_level)
          setPrivacy(restored.config.privacy_level);
        if (restored.config?.mode === "append") {
          setTargetMode("append");
          if (restored.ontology_id) setOntologyId(restored.ontology_id);
        } else {
          setTargetMode("create");
          if (restored.ontology_id) {
            apiClient
              .get<{ name?: string }>(`/ontologies/${restored.ontology_id}`)
              .then((ontology) => {
                if (!cancelled && ontology?.name)
                  setNewOntologyName(ontology.name);
              })
              .catch(() => {});
          }
        }
        setStep(4);
      })
      .catch((reason) => {
        if (!cancelled) setError(`无法恢复构建任务：${errorText(reason)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [restoredRunId, run?.id]);
  useEffect(() => {
    if (selectedDatasetId) {
      loadSamples(selectedDatasetId);
      const ds = datasets.find((item) => item.id === selectedDatasetId);
      if (ds) setPrivacy(ds.privacy_level || "standard");
    }
  }, [selectedDatasetId]);
  useEffect(() => {
    if (
      !task ||
      !["queued", "running", "cancel_requested"].includes(task.status)
    )
      return;
    const timer = window.setInterval(
      () =>
        apiClientV2
          .get<InstallTask>(`/multimodal/catalog/install/${task.id}`)
          .then((next) => {
            setTask(next);
            if (next.status === "completed" && next.dataset_id) {
              setSelectedDatasetId(next.dataset_id);
              loadCatalog();
            }
          })
          .catch(() => {}),
      1800,
    );
    return () => window.clearInterval(timer);
  }, [task?.id, task?.status]);
  useEffect(() => {
    if (
      !run ||
      !["queued", "running", "waiting_for_model"].includes(run.status)
    )
      return;
    const timer = window.setInterval(
      () =>
        constructionApi
          .getRun(run.id)
          .then(setRun)
          .catch(() => {}),
      1600,
    );
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);
  useEffect(() => {
    if (!mappingTaskId || mappingGenerated) return;
    const timer = window.setInterval(
      () =>
        apiClientV2
          .get<MappingTask>(`/mapping-tasks/${mappingTaskId}`)
          .then((result) => {
            setMappingTask(result);
            if (result.status === "completed") {
              const next = Array.isArray(result.result?.suggestions)
                ? result.result.suggestions
                : [];
              setMappingSuggestions(next);
              setMappingConfirmed(
                next.map((_: unknown, index: number) => index),
              );
              setMappingGenerated(next.length > 0);
              setMappingTaskId("");
              if (!next.length)
                setError("映射任务完成，但没有返回可确认的建议");
            } else if (
              ["failed", "waiting_for_model", "cancelled"].includes(
                result.status || "",
              )
            ) {
              setError(
                result.error ||
                  (result.status === "waiting_for_model"
                    ? "MiniMax M3 正在等待恢复"
                    : "映射任务未完成"),
              );
              setMappingTaskId("");
            }
          })
          .catch(() => {}),
      1200,
    );
    return () => window.clearInterval(timer);
  }, [mappingTaskId, mappingGenerated]);
  useEffect(() => {
    if (!previewSample?.id) {
      setEvidence(null);
      return;
    }
    let cancelled = false;
    setEvidenceLoading(true);
    setEvidence(null);
    apiClientV2
      .get<MultimodalEvidence>(
        `/multimodal/samples/${previewSample.id}/evidence`,
      )
      .then((result) => {
        if (!cancelled) setEvidence(result);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err) || "样例证据加载失败");
      })
      .finally(() => {
        if (!cancelled) setEvidenceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [previewSample?.id]);

  const installExample = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await apiClientV2.post<{
        task_id: string;
        status: string;
      }>("/multimodal/catalog/i-badas/install", {});
      setTask({
        id: created.task_id,
        task_id: created.task_id,
        source_id: "ibadas_12_demo",
        status: created.status,
        progress: { stage: "等待开始", completed: 0, total: 12 },
      });
    } catch (err: any) {
      setError(
        err?.detail?.message ||
          err?.detail ||
          err?.message ||
          "样例安装任务创建失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const importArchive = async (file: File) => {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await apiClientV2.post<{
        task_id: string;
        status: string;
      }>("/multimodal/imports", form);
      setTask({
        id: created.task_id,
        task_id: created.task_id,
        source_id: "zip_import",
        status: created.status,
        progress: { stage: "等待校验", completed: 0, total: 0 },
      });
    } catch (err: any) {
      setError(
        err?.detail?.message ||
          err?.detail ||
          err?.message ||
          "多模态清单导入失败",
      );
    } finally {
      setBusy(false);
      if (importRef.current) importRef.current.value = "";
    }
  };

  const resetTargetDraft = () => {
    setDraftId("");
    setMappingTaskId("");
    setMappingTask(null);
    setMappingGenerated(false);
    setMappingSuggestions([]);
    setMappingConfirmed([]);
  };
  const switchTargetMode = (mode: "create" | "append") => {
    setTargetMode(mode);
    resetTargetDraft();
    if (mode === "create") setOntologyId("");
    else setOntologyId((current) => current || ontologies[0]?.id || "");
  };
  const ensureDraft = async () => {
    if (!selectedDatasetId) throw new Error("请先选择数据集");
    if (targetMode === "append" && !ontologyId)
      throw new Error("请选择要追加的目标本体");
    if (targetMode === "create" && !newOntologyName.trim())
      throw new Error("请输入新本体名称");
    const selection = {
      sample_ids: selectedSampleIds,
      selected_assets: selectedRoles,
      send_fields: Object.keys(sendFields).filter((key) => sendFields[key]),
      sample_limit: Math.min(Math.max(selectedSampleIds.length, 1), 32),
    };
    const processing = { privacy_level: privacy };
    const target = {
      target_mode: targetMode,
      ontology_id: targetMode === "append" ? ontologyId : null,
      new_ontology_name:
        targetMode === "create" ? newOntologyName.trim() : null,
      new_ontology_domain: "制造",
    };
    if (draftId) {
      const patched = await apiClientV2.patch<any>(
        `/construction/drafts/${draftId}`,
        {
          privacy_level: privacy,
          selection,
          processing_config: processing,
          ...target,
        },
      );
      if (patched?.privacy_level && patched.privacy_level !== privacy)
        setPrivacy(patched.privacy_level);
      return draftId;
    }
    const created = await apiClientV2.post<{
      id: string;
      privacy_level?: "standard" | "private";
    }>("/construction/drafts", {
      dataset_id: selectedDatasetId,
      data_class: "multimodal",
      privacy_level: privacy,
      selection,
      processing_config: processing,
      ...target,
    });
    if (created?.privacy_level && created.privacy_level !== privacy)
      setPrivacy(created.privacy_level);
    setDraftId(created.id);
    return created.id;
  };

  const invalidateMapping = () => {
    if (mappingGenerated || draftId) resetTargetDraft();
  };
  const toggleSample = (id: string) => {
    invalidateMapping();
    setSelectedSampleIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  };
  const toggleRole = (role: string) => {
    invalidateMapping();
    setSelectedRoles((current) =>
      current.includes(role)
        ? current.filter((item) => item !== role)
        : [...current, role],
    );
  };
  const toggleSendField = (key: string) => {
    invalidateMapping();
    setSendFields((current) => ({ ...current, [key]: !current[key] }));
  };
  const generateMapping = async () => {
    if (privacy === "standard" && !standardConfigured) {
      setError("MiniMax M3 尚未配置；标准数据不会自动切换到其他模型");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const id = await ensureDraft();
      const result = await apiClientV2.post<any>(
        `/construction/drafts/${id}/generate-mapping`,
      );
      if (result?.mapping_task_id) {
        setMappingTaskId(result.mapping_task_id);
        setMappingTask({
          id: result.mapping_task_id,
          status: "queued",
          stage: "排队",
          progress: { pct: 0 },
          trace: [
            { stage: "排队", status: "queued", detail: "映射任务已提交" },
          ],
        });
        setMappingGenerated(false);
        setMappingSuggestions([]);
        setMappingConfirmed([]);
        return;
      }
      if (result?.mapping?.status === "waiting_for_model") {
        setError(result.mapping.error || "MiniMax M3 尚未返回映射");
        return;
      }
      setMappingGenerated(true);
      setMappingSuggestions(
        Array.isArray(result?.mapping?.suggestions)
          ? result.mapping.suggestions
          : [],
      );
      setMappingConfirmed(
        (result?.mapping?.suggestions || []).map(
          (_: unknown, index: number) => index,
        ),
      );
    } catch (err: any) {
      setError(
        err?.detail?.message ||
          err?.detail ||
          err?.message ||
          "映射建议生成失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const build = async () => {
    if (!selectedDatasetId || selectedSampleIds.length === 0) {
      setError("请完成数据集和样例选择");
      return;
    }
    if (!mappingGenerated || mappingConfirmed.length === 0) {
      setError("请先生成并确认本体映射");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const id = await ensureDraft();
      await apiClientV2.patch(`/construction/drafts/${id}`, {
        mapping: {
          confirmed: mappingConfirmed.map((index) => mappingSuggestions[index]),
          suggestions: mappingSuggestions,
        },
      });
      const created = await apiClientV2.post<Run>(
        `/construction/drafts/${id}/build`,
        {
          mapping_confirmed: true,
          mode: targetMode,
          model_id: privacy === "private" ? null : modelStatus?.model_id,
        },
      );
      setRun(created);
      setStep(4);
      setSearchParams({ run: created.id }, { replace: true });
    } catch (err: any) {
      setError(
        err?.detail?.message ||
          err?.detail ||
          err?.message ||
          "构建任务创建失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const next = async () => {
    if (step === 0 && !selectedDatasetId) {
      setError("请选择已有数据集，或先安装/导入样例");
      return;
    }
    if (step === 0 && targetMode === "append" && !ontologyId) {
      setError("请选择同为多模态数据的目标本体");
      return;
    }
    if (step === 0 && targetMode === "create" && !newOntologyName.trim()) {
      setError("请输入新本体名称");
      return;
    }
    if (step === 1 && selectedSampleIds.length === 0) {
      setError("至少选择一个样例");
      return;
    }
    if (step === 3 && (!mappingGenerated || mappingConfirmed.length === 0)) {
      setError(mappingTaskId ? "映射任务仍在处理中" : "请先生成并确认映射建议");
      return;
    }
    setError("");
    if (step === 0 || step === 1 || step === 2) {
      setBusy(true);
      try {
        const id = await ensureDraft();
        if (step === 2) {
          if (privacy === "standard" && !standardConfigured) {
            throw new Error("MiniMax M3 尚未配置；标准数据不能开始映射");
          }
          const preflight = await apiClientV2.post<any>(
            `/construction/drafts/${id}/m3-preflight`,
          );
          if (preflight?.preflight?.allowed || privacy === "private") {
            const result = await apiClientV2.post<any>(
              `/construction/drafts/${id}/generate-mapping`,
            );
            if (result?.mapping_task_id) {
              setMappingTaskId(result.mapping_task_id);
              setMappingTask({
                id: result.mapping_task_id,
                status: "queued",
                stage: "排队",
                progress: { pct: 0 },
                trace: [
                  {
                    stage: "排队",
                    status: "queued",
                    detail: "映射任务已提交",
                  },
                ],
              });
              setMappingGenerated(false);
              setMappingSuggestions([]);
              setMappingConfirmed([]);
            }
            if (result?.mapping?.status === "waiting_for_model") {
              setError(result.mapping.error || "MiniMax M3 正在等待恢复");
              return;
            }
            const suggested = Array.isArray(result?.mapping?.suggestions)
              ? result.mapping.suggestions
              : [];
            setMappingSuggestions(suggested);
            setMappingConfirmed(
              suggested.map((_: unknown, index: number) => index),
            );
            setMappingGenerated(suggested.length > 0);
          } else {
            throw new Error(
              preflight?.preflight?.reason || "发送范围未通过校验",
            );
          }
        }
      } catch (err: any) {
        setError(
          err?.detail?.message ||
            err?.detail ||
            err?.message ||
            "保存构筑草案失败",
        );
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    setStep((value) => Math.min(4, value + 1));
  };

  return (
    <div className="wb-page max-w-[1320px]">
      <header className="wb-page-header">
        <div>
          <div className="wb-eyebrow">
            <Waypoints size={13} /> 数据构筑 / 多模态
          </div>
          <h1 className="wb-page-title">多模态数据构建</h1>
          <p className="wb-page-subtitle">
            RGB、深度、掩码、点云与结构化元数据
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="wb-button-secondary" onClick={() => loadCatalog()}>
            <RefreshCw size={14} />
            刷新
          </button>
          <button
            className="wb-button-secondary"
            onClick={() => importRef.current?.click()}
          >
            <UploadCloud size={14} />
            导入 ZIP
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) importArchive(file);
            }}
          />
        </div>
      </header>

      {error && (
        <div className="wb-alert wb-alert-danger">
          <TriangleAlert size={16} />
          <span>{error}</span>
          <button onClick={() => setError("")} className="ml-auto">
            <X size={15} />
          </button>
        </div>
      )}

      <div className="wb-stepper wb-surface">
        {STEPS.map((label, index) => (
          <button
            key={label}
            onClick={() => index <= step && setStep(index)}
            className={`wb-step ${index === step ? "wb-step-active" : index < step ? "wb-step-done" : ""}`}
          >
            <span className="wb-step-index">
              {index < step ? <Check size={13} /> : index + 1}
            </span>
            <span>{label}</span>
          </button>
        ))}
      </div>

      {step === 0 && (
        <section className="wb-surface p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="wb-section-kicker">构建目标</div>
              <h2 className="wb-section-title mt-1">新建或追加本体</h2>
            </div>
            <span className="wb-status">最终确认时创建修订</span>
          </div>
          <div className="mt-4 grid md:grid-cols-2 gap-3">
            <button
              type="button"
              className={`wb-radio-card ${targetMode === "create" ? "wb-choice-selected" : ""}`}
              onClick={() => switchTargetMode("create")}
            >
              <Database size={16} />
              <div>
                <strong>新建本体</strong>
                <p>填写名称后选择数据来源</p>
              </div>
            </button>
            <button
              type="button"
              className={`wb-radio-card ${targetMode === "append" ? "wb-choice-selected" : ""}`}
              onClick={() => switchTargetMode("append")}
            >
              <Waypoints size={16} />
              <div>
                <strong>追加本体</strong>
                <p>写入同类本体的新修订</p>
              </div>
            </button>
          </div>
          <div className="mt-3 max-w-xl">
            {targetMode === "create" ? (
              <label className="wb-label">
                本体名称
                <input
                  className="wb-input mt-1"
                  value={newOntologyName}
                  onChange={(event) => {
                    setNewOntologyName(event.target.value);
                    resetTargetDraft();
                  }}
                />
              </label>
            ) : (
              <label className="wb-label">
                目标本体
                <select
                  className="wb-input mt-1"
                  value={ontologyId}
                  onChange={(event) => {
                    setOntologyId(event.target.value);
                    resetTargetDraft();
                  }}
                >
                  <option value="">请选择多模态本体</option>
                  {ontologies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.domain || "通用"}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </section>
      )}

      {step === 0 && (
        <div className="grid xl:grid-cols-[1.3fr_0.7fr] gap-4">
          <section className="wb-surface p-5 space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="wb-section-kicker">01 / 数据集</div>
                <h2 className="wb-section-title">选择数据来源</h2>
              </div>
              <ProgressPill task={task} />
            </div>
            {loading ? (
              <div className="wb-empty">
                <Loader2 size={19} className="animate-spin" />
                加载目录
              </div>
            ) : (
              <div className="grid md:grid-cols-2 gap-3">
                {catalog.map((source) => (
                  <div
                    key={source.id}
                    className={`wb-choice-card ${source.installed ? "wb-choice-selected" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="wb-icon-box">
                        <Database size={17} />
                      </div>
                      {source.installed ? (
                        <span className="wb-status wb-status-success">
                          已安装
                        </span>
                      ) : (
                        <span className="wb-status">在线样例</span>
                      )}
                    </div>
                    <h3 className="mt-4 font-semibold text-gray-900">
                      {source.name}
                    </h3>
                    <p className="mt-1 text-xs text-gray-500">
                      {source.repository} · revision{" "}
                      {source.revision.slice(0, 8)}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {source.modalities.slice(0, 5).map((item) => (
                        <span key={item} className="wb-tag">
                          {item}
                        </span>
                      ))}
                    </div>
                    <div className="mt-4 flex items-center justify-between gap-2">
                      <span className="text-xs text-gray-500">
                        {source.sample_count} 组 · {source.license}
                      </span>
                      {source.installed && source.dataset_id ? (
                        <button
                          onClick={() =>
                            setSelectedDatasetId(source.dataset_id || "")
                          }
                          className="wb-button-secondary text-xs"
                        >
                          选用
                        </button>
                      ) : (
                        <button
                          disabled={
                            busy ||
                            Boolean(
                              task &&
                              ["queued", "running"].includes(task.status),
                            )
                          }
                          onClick={installExample}
                          className="wb-button-primary text-xs"
                        >
                          <Download size={13} />
                          安装样例
                        </button>
                      )}
                    </div>
                    <p className="mt-3 text-[11px] text-gray-400">
                      {source.download_policy}
                    </p>
                  </div>
                ))}
              </div>
            )}
            {datasets.length > 0 && (
              <div className="border-t border-gray-100 pt-4">
                <label className="wb-label">
                  已入库数据集
                  <select
                    className="wb-input mt-1"
                    value={selectedDatasetId}
                    onChange={(event) =>
                      setSelectedDatasetId(event.target.value)
                    }
                  >
                    <option value="">请选择</option>
                    {datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>
                        {dataset.name} · {dataset.privacy_level}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </section>
          <section className="wb-surface p-5">
            <div className="wb-section-kicker">导入约定</div>
            <h2 className="wb-section-title mt-1">ZIP + manifest</h2>
            <div className="mt-4 space-y-3 text-sm text-gray-600">
              <div className="flex gap-3">
                <ShieldCheck size={16} className="text-emerald-600 shrink-0" />
                <span>
                  导入前校验路径穿越、重复样本、缺失文件、格式和 SHA-256。
                </span>
              </div>
              <div className="flex gap-3">
                <FileArchive size={16} className="text-gray-500 shrink-0" />
                <span>
                  manifest.json 或 manifest.csv，资产角色可填
                  RGB、深度、掩码、点云和元数据。
                </span>
              </div>
              <div className="flex gap-3">
                <CircleHelp size={16} className="text-gray-500 shrink-0" />
                <span>
                  PDF / DOCX 仅登记为证据附件；本轮不宣称音视频语义理解。
                </span>
              </div>
            </div>
            <button
              className="wb-button-secondary mt-5 w-full"
              onClick={() =>
                navigate(
                  `/data/pipelines/connections?return_to=${encodeURIComponent("/data/multimodal")}${draftId ? `&draft_id=${encodeURIComponent(draftId)}` : ""}`,
                )
              }
            >
              查看其他数据来源模板
            </button>
            {task?.error && (
              <div className="wb-alert wb-alert-danger mt-5">
                <TriangleAlert size={15} />
                {task.error}
              </div>
            )}
          </section>
        </div>
      )}

      {step === 1 && (
        <section className="wb-surface p-5 space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <div className="wb-section-kicker">02 / 内容选择</div>
              <h2 className="wb-section-title">选择样例与模态</h2>
            </div>
            <span className="wb-status">
              已选 {selectedSampleIds.length} 组
            </span>
          </div>
          {selectedDataset ? (
            <>
              <div className="grid md:grid-cols-3 gap-3">
                <label className="wb-label">
                  场景
                  <select
                    className="wb-input mt-1"
                    value={sceneFilter}
                    onChange={(event) => setSceneFilter(event.target.value)}
                  >
                    <option value="all">全部场景</option>
                    {scenes.map((scene) => (
                      <option key={scene} value={scene}>
                        {scene}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="wb-label">
                  标签
                  <select
                    className="wb-input mt-1"
                    value={labelFilter}
                    onChange={(event) => setLabelFilter(event.target.value)}
                  >
                    <option value="all">全部标签</option>
                    {labels.map((label) => (
                      <option key={label} value={label}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="wb-label">
                  模态
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {["rgb", "depth", "mask", "point_cloud", "metadata"].map(
                      (role) => (
                        <button
                          key={role}
                          onClick={() => toggleRole(role)}
                          className={`wb-filter-chip ${selectedRoles.includes(role) ? "wb-filter-chip-active" : ""}`}
                        >
                          {roleLabel[role]}
                        </button>
                      ),
                    )}
                  </div>
                </div>
              </div>
              <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
                {filteredSamples.map((sample) => (
                  <button
                    key={sample.id}
                    onClick={() => {
                      toggleSample(sample.id);
                      setPreviewSampleId(sample.id);
                    }}
                    className={`wb-sample-card text-left ${selectedSampleIds.includes(sample.id) ? "wb-choice-selected" : ""}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm">
                        {sample.scene_id || "样例"} / {sample.sample_key}
                      </span>
                      <span
                        className={`wb-status ${sample.label === "anomaly" ? "wb-status-warning" : "wb-status-success"}`}
                      >
                        {sample.label || "未标注"}
                      </span>
                    </div>
                    <div className="mt-2 text-xs text-gray-500">
                      {sample.labels?.join(" · ") || "无附加标签"}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {sample.assets.map((asset) => (
                        <span key={asset.id} className="wb-tag">
                          {roleLabel[asset.role] || asset.role}
                        </span>
                      ))}
                    </div>
                  </button>
                ))}
              </div>
              {filteredSamples.length === 0 && (
                <div className="wb-empty">
                  <ScanSearch size={20} />
                  当前筛选没有样例
                </div>
              )}
            </>
          ) : (
            <div className="wb-empty">
              <Database size={20} />
              请先在上一步选择数据集
            </div>
          )}
        </section>
      )}

      {step === 2 && (
        <section className="grid xl:grid-cols-[1fr_0.9fr] gap-4">
          <div className="wb-surface p-5 space-y-5">
            <div>
              <div className="wb-section-kicker">03 / 处理配置</div>
              <h2 className="wb-section-title">确定性处理与发送范围</h2>
            </div>
            <div>
              <div className="wb-label mb-2">隐私级别</div>
              <div className="grid sm:grid-cols-2 gap-3">
                <button
                  onClick={() => setPrivacy("standard")}
                  className={`wb-radio-card ${privacy === "standard" ? "wb-choice-selected" : ""}`}
                >
                  <ShieldCheck size={16} />
                  <div>
                    <strong>标准 standard</strong>
                    <p>允许把确认范围发送给 MiniMax M3</p>
                  </div>
                </button>
                <button
                  onClick={() => setPrivacy("private")}
                  className={`wb-radio-card ${privacy === "private" ? "wb-choice-selected" : ""}`}
                >
                  <ShieldCheck size={16} />
                  <div>
                    <strong>私密 private</strong>
                    <p>禁止云模型，仅使用规则、本地模型与人工映射</p>
                  </div>
                </button>
              </div>
            </div>
            <div>
              <div className="wb-label mb-2">发送摘要字段</div>
              <div className="grid sm:grid-cols-2 gap-2">
                {Object.entries({
                  metadata: "结构化元数据",
                  labels: "官方标签与场景 ID",
                  image_stats: "图像统计摘要",
                  point_stats: "点云统计摘要",
                }).map(([key, label]) => (
                  <label
                    key={key}
                    className="flex items-center gap-2 text-sm text-gray-700"
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(sendFields[key])}
                      disabled={privacy === "private"}
                      onChange={() => toggleSendField(key)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <p className="text-xs text-gray-400 mt-3">
                RGB、深度伪彩图和掩码叠加图可按选择发送；原始点云、深度数组和掩码二进制不直接发送。
              </p>
            </div>
            <div className="wb-config-row">
              <span>模型</span>
              <span
                className={
                  privacy === "private"
                    ? "text-gray-500"
                    : modelStatus?.upstream_authorized
                      ? "text-emerald-700"
                      : standardConfigured
                        ? "text-slate-700"
                        : "text-amber-700"
                }
              >
                {privacy === "private"
                  ? "不调用云模型"
                  : modelStatus?.upstream_authorized
                    ? "MiniMax M3 · 探测成功"
                    : standardConfigured
                      ? "MiniMax M3 · 已配置，提交时验证"
                      : "MiniMax M3 · 未配置"}
              </span>
            </div>
          </div>
          <div className="wb-surface p-5">
            <div className="wb-section-kicker">样例证据</div>
            <h2 className="wb-section-title mt-1">
              {previewSample?.scene_id || "尚未选择"} /{" "}
              {previewSample?.label || "—"}
            </h2>
            {evidenceLoading ? (
              <div className="wb-empty mt-4">
                <Loader2 size={18} className="animate-spin" />
                加载同步证据
              </div>
            ) : previewSample ? (
              <MultimodalEvidenceWorkspace
                evidence={evidence}
                selectedRoles={selectedRoles}
              />
            ) : (
              <div className="wb-empty mt-4">
                <ImageIcon size={20} />
                在内容选择中点选一个样例查看证据
              </div>
            )}
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="wb-surface p-5 space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="wb-section-kicker">04 / 本体映射</div>
              <h2 className="wb-section-title">确认语义建议</h2>
              <p className="text-xs text-gray-500 mt-1">
                官方标签和源 ID 优先；建议仅在确认后进入构建。
              </p>
            </div>
            <button
              className="wb-button-primary"
              onClick={generateMapping}
              disabled={
                (privacy === "standard" && !standardConfigured) ||
                busy ||
                Boolean(mappingTaskId)
              }
            >
              <Play size={14} />
              {mappingGenerated
                ? "重新生成建议"
                : mappingTaskId
                  ? "映射处理中"
                  : "重新请求映射"}
            </button>
          </div>
          {mappingGenerated ? (
            <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
              {mappingSuggestions.map((item, index) => (
                <label
                  key={`${item.source || item.target || "mapping"}-${index}`}
                  className={`wb-mapping-card ${mappingConfirmed.includes(index) ? "wb-choice-selected" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={mappingConfirmed.includes(index)}
                    onChange={() =>
                      setMappingConfirmed((current) =>
                        current.includes(index)
                          ? current.filter((value) => value !== index)
                          : [...current, index],
                      )
                    }
                  />
                  <div>
                    <strong>
                      {String(item.target || item.kind || "映射")}
                    </strong>
                    <p>
                      {String(item.source || "来源字段")}{" "}
                      {item.target_relation ? `→ ${item.target_relation}` : ""}
                    </p>
                    <span>
                      {String(item.extractor || "规则处理")} · 置信度{" "}
                      {item.confidence == null
                        ? "—"
                        : Number(item.confidence).toFixed(2)}
                    </span>
                  </div>
                </label>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-5 py-5">
              <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <Loader2 size={18} className="animate-spin text-slate-500" />
                {mappingTask?.stage || "映射任务排队中"}
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded bg-slate-200">
                <div
                  className="h-full bg-slate-700 transition-all duration-500"
                  style={{
                    width: `${Math.max(4, Math.min(100, Number(mappingTask?.progress?.pct ?? (mappingTaskId ? 8 : 0))))}%`,
                  }}
                />
              </div>
              <div className="mt-4 grid gap-2 md:grid-cols-5">
                {[
                  "来源识别",
                  "读取计划",
                  "范围校验",
                  "内容发送",
                  "本体生成",
                ].map((stage) => {
                  const entry = mappingTask?.trace?.find(
                    (item) => item.stage === stage,
                  );
                  return (
                    <div
                      key={stage}
                      className={`rounded border px-2.5 py-2 text-xs ${entry?.status === "completed" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : entry?.status === "running" ? "border-slate-400 bg-white text-slate-800" : "border-slate-200 bg-white text-slate-400"}`}
                    >
                      <p className="font-medium">{stage}</p>
                      <p className="mt-1 line-clamp-2 text-[10px]">
                        {entry?.detail ||
                          (entry?.status === "completed" ? "已完成" : "等待")}
                      </p>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 space-y-1 text-xs text-slate-500">
                {mappingTask?.trace?.slice(-3).map((item, index) => (
                  <p key={`${item.stage}-${index}`}>
                    {item.stage || "处理中"}：
                    {item.detail || item.status || "已提交"}
                  </p>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {step === 4 && (
        <section className="grid xl:grid-cols-[1fr_0.7fr] gap-4">
          <div className="wb-surface p-5 space-y-4">
            <div>
              <div className="wb-section-kicker">05 / 确认构建</div>
              <h2 className="wb-section-title">启动构建任务</h2>
            </div>
            <div className="wb-summary-grid">
              <div>
                <span>数据集</span>
                <strong>{selectedDataset?.name || "—"}</strong>
              </div>
              <div>
                <span>样例</span>
                <strong>{selectedSampleIds.length} 组</strong>
              </div>
              <div>
                <span>隐私</span>
                <strong>{privacy}</strong>
              </div>
              <div>
                <span>目标本体</span>
                <strong>
                  {targetMode === "create"
                    ? newOntologyName
                    : ontologies.find((item) => item.id === ontologyId)?.name ||
                      "—"}
                </strong>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-gray-100 pt-4">
              <span className="text-xs text-gray-500">
                构建通过规则门禁后发布本体；标准模式 M3 失败会停在“等待模型”。
              </span>
              <button
                onClick={build}
                disabled={
                  busy ||
                  Boolean(
                    run &&
                    ["queued", "running", "waiting_for_model"].includes(
                      run.status,
                    ),
                  )
                }
                className="wb-button-primary"
              >
                <Play size={14} />
                {busy ? "提交中" : "确认并构建"}
              </button>
            </div>
            {run && (
              <div className="wb-task-card">
                <div className="flex items-center gap-2">
                  {run.status === "completed" ? (
                    <CheckCircle2 size={16} className="text-emerald-600" />
                  ) : run.status === "failed" ? (
                    <TriangleAlert size={16} className="text-red-600" />
                  ) : (
                    <Loader2 size={16} className="animate-spin text-gray-500" />
                  )}
                  <strong>{statusLabel[run.status] || run.status}</strong>
                  <span className="text-xs text-gray-400">
                    {String(run.progress?.stage || "等待处理")} ·{" "}
                    {String(run.progress?.completed ?? 0)} /{" "}
                    {String(run.progress?.total ?? 0)}
                  </span>
                </div>
                {run.status !== "failed" && run.status !== "completed" && (
                  <div className="mt-3 h-1.5 overflow-hidden rounded bg-slate-200">
                    <div
                      className="h-full bg-slate-700 transition-all duration-500"
                      style={{
                        width: `${Math.max(5, Number(run.progress?.pct || 0))}%`,
                      }}
                    />
                  </div>
                )}
                {run.error && (
                  <p className="text-xs text-red-700 mt-2">{run.error}</p>
                )}
                {run.metrics && (
                  <div className="mt-3 grid grid-cols-2 lg:grid-cols-5 gap-2 text-xs text-gray-500">
                    <span>
                      媒体 {String(run.metrics.media_processed ?? "—")}
                    </span>
                    <span>
                      实体类型{" "}
                      {String(
                        run.metrics.entity_type_count ??
                          run.metrics.entity_types ??
                          "—",
                      )}
                    </span>
                    <span>
                      真实实例{" "}
                      {String(
                        run.metrics.instance_count ??
                          run.metrics.instances ??
                          "—",
                      )}
                    </span>
                    <span>
                      关系{" "}
                      {String(
                        run.metrics.relation_count ??
                          run.metrics.relations ??
                          "—",
                      )}
                    </span>
                    <span>
                      逻辑规则{" "}
                      {String(
                        run.metrics.logic_rule_count ??
                          run.metrics.logic_rules ??
                          "—",
                      )}
                    </span>
                  </div>
                )}
                {run.status === "completed" && (
                  <button
                    onClick={() =>
                      navigate(
                        `/ontologies/${run.ontology_id || ontologyId}?tab=graph`,
                      )
                    }
                    className="wb-button-secondary mt-3 text-xs"
                  >
                    打开本体 <ArrowRight size={13} />
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="wb-surface p-5">
            <div className="wb-section-kicker">结果</div>
            <h2 className="wb-section-title mt-1">构建后可查看</h2>
            <div className="mt-4 space-y-2">
              {[
                {
                  icon: Waypoints,
                  label: "本体关系",
                  note: "实体类型、属性与关系",
                },
                {
                  icon: ScanSearch,
                  label: "证据画布",
                  note: "RGB / 深度 / 掩码定位",
                },
                { icon: Database, label: "点云联动", note: "最多 50,000 点" },
                {
                  icon: ShieldCheck,
                  label: "逻辑规则与质量审查",
                  note: "规则和审查结论",
                },
              ].map((item) => (
                <div
                  key={item.label}
                  className="flex items-center gap-3 rounded border border-gray-100 p-3"
                >
                  <item.icon size={16} className="text-gray-500" />
                  <div>
                    <p className="text-sm font-medium">{item.label}</p>
                    <p className="text-xs text-gray-400">{item.note}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {step === 4 && (
        <div className="wb-surface px-5 py-3 text-xs text-gray-500">
          构建方式：
          <strong className="text-gray-800">
            {targetMode === "create"
              ? `新建 · ${newOntologyName}`
              : `追加 · ${ontologies.find((item) => item.id === ontologyId)?.name || "—"}`}
          </strong>
          。确认后会创建新的不可变修订。
        </div>
      )}
      <div className="wb-wizard-actions">
        {step > 0 ? (
          <button
            className="wb-button-secondary"
            onClick={() => setStep((value) => Math.max(0, value - 1))}
          >
            <ArrowLeft size={14} />
            上一步
          </button>
        ) : (
          <span />
        )}
        {step < 4 && (
          <button className="wb-button-primary" onClick={next} disabled={busy}>
            {step === 0
              ? selectedDatasetId
                ? "使用已有数据并继续"
                : "选择数据后继续"
              : "下一步"}
            <ArrowRight size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
