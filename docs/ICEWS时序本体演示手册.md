# ICEWS 时序本体演示手册

## 1. 演示目标

本版本用 Harvard Dataverse 的 ICEWS 官方三日事件切片演示“事件表 → Instant 时序本体 → FalkorDB 实例图 → 时间窗口调查”。数据文件固定为 `20230106-icews-events.tab.zip`（File ID `7070776`，SHA-256 `39adf9bb3f9b263763f5d46f224c578de0eda2ca5f6a1b843004b6aff29e62e5`），共 3,155 条事件，日期为 2023-01-01 至 2023-01-03。日期只有天级精度，系统不会补造时分秒或时区。

来源：[ICEWS Coded Event Data（Harvard Dataverse）](https://doi.org/10.7910/DVN/28075)。原始压缩包不提交 Git；安装后只在 Dataset Manifest 中保存 DOI、File ID、哈希、条款和统计。

## 2. 启动

在项目 worktree 执行：

```bash
docker compose -f docker-compose.v2.yml up -d --build
```

浏览器打开 `http://127.0.0.1:5173`，使用本地管理员账号登录（默认开发环境为 `admin / admin123`，如 `.env` 已修改则以实际配置为准）。

## 3. 五步构建

1. 进入“数据管理 → 时序数据”，点击“开始构建向导”。用户必须主动选择一个数据源：ICEWS 官方样例、已有 Dataset，或上传 TSV/CSV/JSON；向导不会静默替你选第一项。
2. 未安装时点击“安装并校验官方样例”。服务下载 File ID 7070776，校验哈希、字段和 3,155 行；任何校验失败都不会注册占位数据。
3. 在“筛选数据”选择全部事件、俄乌、朝韩、负向强度，或自定义日期、国家、事件类型、CAMEO Code、强度和最大事件数。筛选先在后端执行，预览中的预计事件数随之变化。
4. 在“确认时间语义”确认 `Instant · day`，字段是 `Event Date`。Interval 和 Ordinal 对 ICEWS 禁用；已有 Dataset/上传文件可以在此选择三种时间语义并填写实体、时间或区间列。
5. 在“配置本体映射”检查字段表和一条真实事件的预览。ICEWS 的最终结构为 `Actor`、`InteractionEvent`、`EventCategory`、`Country`、`Location`，关系为 `INITIATED`、`TARGETED`、`CLASSIFIED_AS`、`ASSOCIATED_WITH`、`OCCURRED_IN`；通用 Dataset 则按实体 ID、时间列和 Observation 属性构建。
6. 在“确认并执行”选择复用/创建 `ICEWS 2023 时序事件本体`，点击“创建时序构建任务”。相同筛选和映射配置会复用已完成 Run，重复执行不会增加 FalkorDB 节点或边。

## 4. 调查工作台

构建完成后自动进入 `/data/temporal/runs/{run_id}`：

- 左侧筛选：日期、国家、参与者、事件类型、CAMEO 类别和强度范围；时间模式可选“截至当前（累计）”或“当日窗口”。
- 中间图谱：本体结构、Actor 关系投影、事件证据三种视图。画布最多渲染 200 个节点，顶部仍显示完整节点/边数量。
- 顶部调查工具支持节点搜索、一跳/两跳 Ego、隐藏标签和适配/放大画布；这些只改变当前视图，不修改图数据。
- 时间轴：拖动日期滑块或点击播放/暂停，快照会随日期重新查询。
- 右侧详情：点击节点查看类型和属性；事件节点可查看 Event ID、Story ID、Publisher、原始行号和 EvidenceRef。
- 底部“两个日期窗口对比”：选择起止日期，绿色表示窗口出现，红色表示窗口未出现；这表示窗口差异，不表示现实事实被删除。
- 增长曲线：按日显示事件节点累计增长。

数据源安装后保存在 MinIO `raw-datasets` 的 DatasetVersion 对象中；PostgreSQL 保存 Dataset、Manifest、ConstructionRun 和 EvidenceRef；FalkorDB 保存按本体隔离的实例图。当前演示本体 ID 为 `5aec61a8-27f2-47a5-9a80-04b31af9f894`，图名为 `nano_5aec61a8_27f2_47a5_9a80_04b31af9f894`。

数据全部来自按 ontology 隔离的 FalkorDB 图。FalkorDB 不可用时页面明确报错，不把 SQLite Schema 回退冒充实例图。

## 5. API 快速检查

```bash
# 数据源状态
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v2/temporal/sources

# 预览（安装后）
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8000/api/v2/temporal/sources/icews_2023_demo/preview?limit=25'

# 运行结果
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v2/construction-runs/$RUN_ID
```

## 6. 常见问题

- **显示未安装**：先点击官方样例安装；没有完整文件时不会显示占位行。
- **哈希不一致/字段缺失**：删除下载文件并重新从 DOI 页面获取，不能绕过校验。
- **FalkorDB 未连接**：检查 `FALKORDB_HOST`、`FALKORDB_PORT` 和容器健康；任务不会回退写 SQLite。
- **任务失败**：在工作台查看错误和问题数量；无效日期、空参与者、未知 CAMEO 会进入问题清单并跳过。
- **重复 Run**：同一 ontology、数据集、筛选、时间和映射配置会返回已有成功 Run；改变配置才会创建新 Run。
- **已有 Dataset/上传入口**：向导第 1 步会列出已登记的结构化 Dataset；上传 TSV/CSV/JSON 后会先创建 Dataset，再回到向导确认时间语义。常规数据的 Pipeline/Curated 页面仍可独立使用。

## 7. 5 分钟组会顺序

数据源统计 → 25 行真实预览 → 俄乌/负向快捷筛选 → 确认 Instant → 启动构建 → 事件图与 Actor 投影 → 拖动 1 月 1 日到 1 月 3 日 → 点击事件查看来源证据 → 比较两个日期窗口 → 展示增长曲线和重复运行幂等结果。
