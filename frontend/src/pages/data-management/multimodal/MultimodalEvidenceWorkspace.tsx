import { useEffect, useMemo, useRef, useState } from "react";
import {
  FileText,
  Image as ImageIcon,
  Layers3,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";

export type EvidenceAsset = {
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
  metadata?: Record<string, unknown>;
};

export type DepthStats = {
  shape?: number[];
  min?: number | null;
  max?: number | null;
  p01?: number | null;
  p50?: number | null;
  p99?: number | null;
  invalid_ratio?: number;
  valid_values?: number;
  total_values?: number;
  unit?: string;
};

export type MultimodalEvidence = {
  sample: {
    id: string;
    sample_key?: string;
    scene_id?: string;
    split?: string;
    label?: string;
    labels?: string[];
    metadata?: Record<string, unknown>;
  };
  assets: EvidenceAsset[];
  metadata?: {
    sample?: Record<string, unknown>;
    sources?: Array<{
      asset_id: string;
      name: string;
      value?: unknown;
      error?: string;
    }>;
  };
  depth?: {
    asset_id?: string;
    stats?: DepthStats | null;
    error?: string | null;
  } | null;
  completeness?: { roles?: string[]; missing_required_roles?: string[] };
};

type PointCloudPayload = {
  points?: number[][];
  point_count?: number;
  source_point_count?: number;
  stride?: number;
  bounds?: { min?: number[]; max?: number[] };
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

/**
 * The input remains the stored I-BADAS point array.  This viewer only draws a
 * bounded preview (provided by the API) using WebGL; it never fabricates
 * geometry or replaces the evidence asset.
 */
function WebGLPointCloud({ asset }: { asset: EvidenceAsset }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{
    x: number;
    y: number;
    yaw: number;
    pitch: number;
  } | null>(null);
  const [payload, setPayload] = useState<PointCloudPayload | null>(null);
  const [camera, setCamera] = useState({ yaw: 0.62, pitch: -0.34, zoom: 1 });
  const [preset, setPreset] = useState("等距");
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
      .catch((err) => {
        if (!cancelled) setError(errorText(err) || "点云预览加载失败");
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
    const points = payload?.points;
    if (!canvas || !points?.length) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const gl = canvas.getContext("webgl", { antialias: true, alpha: false });
    if (!gl) {
      setError("当前浏览器未启用 WebGL，无法展示点云");
      return;
    }

    const vertexSource = `
      attribute vec3 a_position;
      attribute vec3 a_color;
      uniform float u_yaw;
      uniform float u_pitch;
      uniform float u_zoom;
      uniform float u_point_size;
      varying vec3 v_color;
      void main() {
        float cy = cos(u_yaw), sy = sin(u_yaw);
        float cp = cos(u_pitch), sp = sin(u_pitch);
        float rx = a_position.x * cy - a_position.z * sy;
        float rz = a_position.x * sy + a_position.z * cy;
        float ry = a_position.y * cp - rz * sp;
        float depth = a_position.y * sp + rz * cp;
        float perspective = 1.0 / (1.72 - depth);
        gl_Position = vec4(rx * perspective * u_zoom, ry * perspective * u_zoom, depth * 0.12, 1.0);
        gl_PointSize = u_point_size;
        v_color = a_color;
      }
    `;
    const fragmentSource = `precision mediump float; varying vec3 v_color; void main() { gl_FragColor = vec4(v_color, 1.0); }`;
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) throw new Error("无法创建 WebGL 着色器");
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(shader) || "WebGL 着色器编译失败");
      return shader;
    };
    try {
      const program = gl.createProgram();
      if (!program) throw new Error("无法创建 WebGL 程序");
      const vertex = compile(gl.VERTEX_SHADER, vertexSource);
      const fragment = compile(gl.FRAGMENT_SHADER, fragmentSource);
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(program) || "WebGL 程序链接失败");
      gl.useProgram(program);
      const min = payload?.bounds?.min || [0, 0, 0];
      const max = payload?.bounds?.max || [1, 1, 1];
      const center = [0, 1, 2].map(
        (index) => ((Number(min[index]) || 0) + (Number(max[index]) || 0)) / 2,
      );
      const span = Math.max(
        1e-6,
        ...[0, 1, 2].map((index) =>
          Math.abs((Number(max[index]) || 0) - (Number(min[index]) || 0)),
        ),
      );
      const vertices: number[] = [];
      for (const point of points) {
        if (!point || point.length < 3 || !point.every(Number.isFinite))
          continue;
        const x = (point[0] - center[0]) / span;
        const y = (point[1] - center[1]) / span;
        const z = (point[2] - center[2]) / span;
        const height = Math.max(0, Math.min(1, y + 0.5));
        vertices.push(
          x,
          y,
          z,
          0.1 + height * 0.35,
          0.3 + height * 0.55,
          0.68 + (1 - height) * 0.18,
        );
      }
      const lines: number[] = [];
      const line = (a: number[], b: number[], colour: number[]) =>
        lines.push(...a, ...colour, ...b, ...colour);
      for (let index = -5; index <= 5; index += 1) {
        const offset = index / 10;
        line([-0.56, -0.56, offset], [0.56, -0.56, offset], [0.72, 0.77, 0.84]);
        line([offset, -0.56, -0.56], [offset, -0.56, 0.56], [0.72, 0.77, 0.84]);
      }
      line([0, -0.56, 0], [0.62, -0.56, 0], [0.9, 0.24, 0.24]);
      line([0, -0.56, 0], [0, 0.62, 0], [0.18, 0.64, 0.36]);
      line([0, -0.56, 0], [0, -0.56, 0.62], [0.22, 0.46, 0.92]);
      const corners = [
        [-0.5, -0.5, -0.5],
        [0.5, -0.5, -0.5],
        [0.5, 0.5, -0.5],
        [-0.5, 0.5, -0.5],
        [-0.5, -0.5, 0.5],
        [0.5, -0.5, 0.5],
        [0.5, 0.5, 0.5],
        [-0.5, 0.5, 0.5],
      ];
      for (const [a, b] of [
        [0, 1],
        [1, 2],
        [2, 3],
        [3, 0],
        [4, 5],
        [5, 6],
        [6, 7],
        [7, 4],
        [0, 4],
        [1, 5],
        [2, 6],
        [3, 7],
      ])
        line(corners[a], corners[b], [0.38, 0.46, 0.58]);
      const pointBuffer = gl.createBuffer();
      const lineBuffer = gl.createBuffer();
      if (!pointBuffer || !lineBuffer) throw new Error("无法创建点云缓冲区");
      const position = gl.getAttribLocation(program, "a_position");
      const colour = gl.getAttribLocation(program, "a_color");
      const bind = (buffer: WebGLBuffer, data: number[]) => {
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 3, gl.FLOAT, false, 24, 0);
        gl.enableVertexAttribArray(colour);
        gl.vertexAttribPointer(colour, 3, gl.FLOAT, false, 24, 12);
      };
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.965, 0.973, 0.984, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.uniform1f(gl.getUniformLocation(program, "u_yaw"), camera.yaw);
      gl.uniform1f(gl.getUniformLocation(program, "u_pitch"), camera.pitch);
      gl.uniform1f(gl.getUniformLocation(program, "u_zoom"), camera.zoom);
      bind(lineBuffer, lines);
      gl.uniform1f(gl.getUniformLocation(program, "u_point_size"), 1);
      gl.drawArrays(gl.LINES, 0, lines.length / 6);
      bind(pointBuffer, vertices);
      gl.uniform1f(
        gl.getUniformLocation(program, "u_point_size"),
        Math.max(1.35, Math.min(3, 54000 / Math.max(1, vertices.length / 6))),
      );
      gl.drawArrays(gl.POINTS, 0, vertices.length / 6);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
      gl.deleteBuffer(pointBuffer);
      gl.deleteBuffer(lineBuffer);
      gl.deleteProgram(program);
    } catch (err: any) {
      setError(err?.message || "点云渲染失败");
    }
  }, [payload, camera]);

  const choosePreset = (value: string) => {
    setPreset(value);
    if (value === "正视") setCamera({ yaw: 0, pitch: 0, zoom: 1.1 });
    else if (value === "俯视") setCamera({ yaw: 0, pitch: -1.26, zoom: 1.05 });
    else setCamera({ yaw: 0.62, pitch: -0.34, zoom: 1 });
  };
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
    setPreset("自定义");
    setCamera((current) => ({
      ...current,
      yaw: start.yaw + (event.clientX - start.x) * 0.012,
      pitch: Math.max(
        -1.42,
        Math.min(1.42, start.pitch + (event.clientY - start.y) * 0.012),
      ),
    }));
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    setPreset("自定义");
    setCamera((current) => ({
      ...current,
      zoom: Math.max(
        0.48,
        Math.min(2.8, current.zoom * (event.deltaY > 0 ? 0.9 : 1.1)),
      ),
    }));
  };

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2">
        <div className="flex items-center gap-2 text-xs text-slate-600">
          <span className="wb-status">WebGL</span>
          <span>XYZ 轴 · 网格 · 边界范围</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            aria-label="点云视角"
            value={preset}
            onChange={(event) => choosePreset(event.target.value)}
            className="rounded border border-slate-200 bg-white px-1.5 py-1 text-[11px] text-slate-600"
          >
            <option>等距</option>
            <option>正视</option>
            <option>俯视</option>
            <option>自定义</option>
          </select>
          <button
            type="button"
            onClick={() => choosePreset("等距")}
            className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
          >
            <RotateCcw size={11} />
            重置
          </button>
        </div>
      </div>
      <div className="relative">
        <canvas
          ref={canvasRef}
          className="h-[286px] w-full cursor-grab touch-none active:cursor-grabbing"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
        />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-50/90 text-xs text-slate-500">
            <Loader2 size={15} className="mr-1 animate-spin" />
            加载真实点云
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-red-50/95 px-5 text-center text-xs text-red-700">
            {error}
          </div>
        )}
        {!loading && !error && (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-white/90 px-2 py-1 text-[10px] text-slate-500">
            {payload?.point_count || 0} 点 · 拖动旋转 · 滚轮缩放
          </div>
        )}
      </div>
    </div>
  );
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function sourcePreviewText(value: unknown) {
  const text = JSON.stringify(value, null, 2);
  // COCO annotations and pose files describe a whole scene.  Rendering their
  // complete payload inline makes the sample-level evidence panel look empty
  // or unreadable.  Keep an inspectable beginning and make the truncation
  // explicit instead of pretending the source file is smaller than it is.
  const limit = 6000;
  return text.length > limit
    ? `${text.slice(0, limit)}\n\n… 已省略 ${text.length - limit} 个字符；完整文件仍作为来源证据保留。`
    : text;
}

function sourceHighlights(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const record = value as Record<string, unknown>;
  const preferred = [
    "scene_id",
    "camera",
    "camera_id",
    "width",
    "height",
    "unit",
    "coordinate_system",
    "version",
  ];
  return preferred
    .filter((key) => record[key] !== undefined && record[key] !== null)
    .slice(0, 4)
    .map((key) => [key, valueText(record[key])] as const);
}

function MetadataPanel({ evidence }: { evidence: MultimodalEvidence }) {
  const sampleMetadata =
    evidence.metadata?.sample || evidence.sample.metadata || {};
  const sources = evidence.metadata?.sources || [];
  const overview = [
    ["场景", evidence.sample.scene_id || "—"],
    ["标签", evidence.sample.labels?.join(" · ") || evidence.sample.label || "—"],
    ["样例", evidence.sample.sample_key || evidence.sample.id || "—"],
    ["模态", evidence.completeness?.roles?.map((role) => roleLabel[role] || role).join(" · ") || "—"],
  ];
  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-xs font-medium text-slate-600">样例摘要</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {overview.map(([key, value]) => (
            <div
              key={key}
              className="rounded border border-slate-200 bg-white px-3 py-2"
            >
              <p className="text-[11px] text-slate-500">{key}</p>
              <p className="mt-1 break-words text-sm text-slate-800">{value}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {Object.entries(sampleMetadata).map(([key, value]) => (
          <div
            key={key}
            className="rounded border border-slate-200 bg-white px-3 py-2"
          >
            <p className="text-[11px] text-slate-500">{key}</p>
            <p className="mt-1 break-words text-sm text-slate-800">
              {valueText(value)}
            </p>
          </div>
        ))}
        {Object.keys(sampleMetadata).length === 0 && (
          <div className="col-span-2 rounded border border-dashed border-slate-300 px-3 py-5 text-center text-sm text-slate-500">
            该样例没有额外结构化字段
          </div>
        )}
      </div>
      {sources.map((source) => (
        <div
          key={source.asset_id}
          className="rounded border border-slate-200 bg-slate-50 p-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm font-medium text-slate-800">
            <span className="flex items-center gap-2">
              <FileText size={15} />
              {source.name}
            </span>
            <span className="wb-status">来源文件</span>
          </div>
          {source.error ? (
            <p className="mt-2 text-xs text-red-700">{source.error}</p>
          ) : (
            <>
              {sourceHighlights(source.value).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {sourceHighlights(source.value).map(([key, value]) => (
                    <span key={key} className="wb-tag">
                      {key}: {value}
                    </span>
                  ))}
                </div>
              )}
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-slate-600 hover:text-slate-900">
                  查看来源内容
                </summary>
                <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-2 text-[11px] leading-5 text-slate-600">
                  {sourcePreviewText(source.value)}
                </pre>
              </details>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

export function MultimodalEvidenceWorkspace({
  evidence,
  selectedRoles,
}: {
  evidence: MultimodalEvidence | null;
  selectedRoles: string[];
}) {
  const [tab, setTab] = useState("rgb");
  const [maskOpacity, setMaskOpacity] = useState(0.52);
  const availableRoles = useMemo(
    () =>
      evidence?.assets
        .map((asset) => asset.role)
        .filter((role, index, all) => all.indexOf(role) === index) || [],
    [evidence],
  );
  useEffect(() => {
    if (!availableRoles.includes(tab))
      setTab(
        availableRoles.includes("rgb") ? "rgb" : availableRoles[0] || "rgb",
      );
  }, [availableRoles.join("|"), tab]);
  if (!evidence)
    return (
      <div className="wb-empty mt-4">
        <ImageIcon size={20} />
        在内容选择中点选一个样例查看同步证据
      </div>
    );
  const asset = (role: string) =>
    evidence.assets.find((item) => item.role === role);
  const rgb = asset("rgb");
  const mask = asset("mask") || asset("mask_visible");
  const selectedAvailable = availableRoles.filter(
    (role) => selectedRoles.includes(role) || role === "mask_visible",
  );
  const tabs = selectedAvailable.length ? selectedAvailable : availableRoles;
  const depth = asset("depth");
  const stats = evidence.depth?.stats;
  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {tabs.map((role) => (
          <button
            key={role}
            type="button"
            onClick={() => setTab(role)}
            className={`wb-filter-chip ${tab === role ? "wb-filter-chip-active" : ""}`}
          >
            {roleLabel[role] || role}
          </button>
        ))}
      </div>
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        {tab === "rgb" && rgb && (
          <div>
            <img
              src={rgb.preview_url}
              alt="I-BADAS RGB 原始彩色图"
              className="max-h-[360px] w-full rounded border border-slate-200 bg-white object-contain"
            />
            <p className="mt-2 text-xs text-slate-500">
              原始彩色图 · {rgb.original_name || rgb.id} · SHA-256{" "}
              {rgb.checksum?.slice(0, 12) || "—"}
            </p>
          </div>
        )}
        {tab === "depth" && depth && (
          <div>
            <img
              src={depth.preview_url}
              alt="深度伪彩预览"
              className="max-h-[360px] w-full rounded border border-slate-200 bg-slate-950 object-contain"
            />
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              {[
                [
                  "范围",
                  stats?.min == null
                    ? "—"
                    : `${Number(stats.min).toPrecision(4)} – ${Number(stats.max).toPrecision(4)}`,
                ],
                [
                  "分位数",
                  stats?.p01 == null
                    ? "—"
                    : `P01 ${Number(stats.p01).toPrecision(3)} · P99 ${Number(stats.p99).toPrecision(3)}`,
                ],
                [
                  "无效值",
                  stats?.invalid_ratio == null
                    ? "—"
                    : `${(Number(stats.invalid_ratio) * 100).toFixed(2)}%`,
                ],
                ["来源单位", stats?.unit || "来源未声明"],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="rounded border border-slate-200 bg-white px-2.5 py-2"
                >
                  <p className="text-[10px] text-slate-500">{label}</p>
                  <p className="mt-1 break-words text-slate-800">{value}</p>
                </div>
              ))}
            </div>
            {evidence.depth?.error && (
              <p className="mt-2 text-xs text-red-700">
                {evidence.depth.error}
              </p>
            )}
          </div>
        )}
        {(tab === "mask" || tab === "mask_visible") && (
          <div>
            <div className="relative flex min-h-[280px] items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-900">
              {rgb && (
                <img
                  src={rgb.preview_url}
                  alt="RGB 底图"
                  className="max-h-[360px] max-w-full object-contain"
                />
              )}
              {mask && (
                <img
                  src={mask.preview_url}
                  alt="异常掩码叠加"
                  className="absolute inset-0 h-full w-full object-contain"
                  style={{ opacity: maskOpacity, mixBlendMode: "screen" }}
                />
              )}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-600">
              <label className="inline-flex items-center gap-2">
                覆盖透明度
                <input
                  aria-label="掩码透明度"
                  type="range"
                  min="0.1"
                  max="0.9"
                  step="0.05"
                  value={maskOpacity}
                  onChange={(event) =>
                    setMaskOpacity(Number(event.target.value))
                  }
                />
              </label>
              <span className="inline-flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded bg-white ring-1 ring-slate-400" />
                正常区域
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded bg-cyan-300" />
                掩码覆盖区域
              </span>
            </div>
          </div>
        )}
        {tab === "point_cloud" && asset("point_cloud") && (
          <WebGLPointCloud asset={asset("point_cloud")!} />
        )}
        {tab === "metadata" && <MetadataPanel evidence={evidence} />}
        {!tabs.includes(tab) && (
          <div className="wb-empty">
            <Layers3 size={20} />
            当前样例没有此模态
          </div>
        )}
      </div>
      {evidence.completeness?.missing_required_roles?.length ? (
        <p className="text-xs text-amber-700">
          缺失必需模态：
          {evidence.completeness.missing_required_roles
            .map((role) => roleLabel[role] || role)
            .join("、")}
        </p>
      ) : (
        <p className="text-xs text-emerald-700">
          必需模态齐全，可作为本体证据。
        </p>
      )}
    </div>
  );
}
