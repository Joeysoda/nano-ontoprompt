# ICEWS 时序本体构建验收记录

> 本文件是可复跑记录模板。执行命令后把日期、commit、运行 ID 和实际计数填入；没有实测的数值不得写成性能结论。

## 数据来源

- DOI：<https://doi.org/10.7910/DVN/28075>
- File：`20230106-icews-events.tab.zip`
- File ID：`7070776`
- SHA-256：`39adf9bb3f9b263763f5d46f224c578de0eda2ca5f6a1b843004b6aff29e62e5`
- 预期规模：3,155 条事件，2023-01-01 至 2023-01-03，Instant/day。

## 执行环境

```text
运行日期：2026-09-01（Asia/Shanghai）
分支：codex/icews-temporal-workbench
commit：`80dcbc4`
Docker Compose：docker-compose.v2.yml
模型：本流程为确定性规则映射，未调用 Ollama/MiniMax
```

## 复跑命令

```bash
docker compose -f docker-compose.v2.yml up -d --build
docker compose -f docker-compose.v2.yml exec backend python scripts/reset_temporal_runtime.py --dry-run
# 首次清理前确认输出只包含 build_mode=temporal_pipeline / mode=temporal 对象
docker compose -f docker-compose.v2.yml exec backend python scripts/reset_temporal_runtime.py --apply --confirm ICEWS-TEMPORAL
# 管理员登录后调用安装接口，或在“时序数据”页面安装
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v2/temporal/sources/icews_2023_demo/install
```

## 结果记录

| 项目 | 预期/实际 |
|---|---:|
| 原始事件行 | 3,155 / 3,155 |
| 有效事件行 | 3,155 / 3,155 |
| 问题行 | 0 / 0 |
| InteractionEvent 节点 | 3,155 |
| Actor 节点 | 1,224 |
| EventCategory 节点 | 118 |
| Country 节点 | 136 |
| Location 节点 | 583 |
| FalkorDB 节点总数 | 5,216 |
| FalkorDB 边总数 | 13,813 |
| EvidenceRef | 16,968（3,155 节点 + 13,813 边） |
| 构建耗时 | 约 1.05 秒（本地 Celery worker） |

本次成功 Run：`c6c7c1a7-66a3-4798-ba75-d730e8090760`；Dataset：`8c0c8f56-2e0b-4c25-a45f-b1fd3cbace59`；DatasetVersion：`d41a276c-97d7-4198-9ecd-d9ca2225ba8d`。存储对象为 `s3://raw-datasets/datasets/8c0c8f56-2e0b-4c25-a45f-b1fd3cbace59/v1/20230106-icews-events.tab`。

## 快捷筛选校验

| 场景 | 预期事件数 | 实际 |
|---|---:|---:|
| 全部事件 | 3,155 | 3,155 |
| 俄乌 | 527 | 527 |
| 朝韩 | 176 | 176 |
| 负向强度 | 1,074 | 1,074 |

## 页面验收清单

- [x] 用户主动选择 ICEWS 数据源，未自动选择第一项。
- [x] 数据预览显示真实字段与事件值。
- [x] Instant/day 和 `Event Date` 显示正确，未生成时间或时区。
- [x] 五步向导可创建 ConstructionRun 并显示阶段进度。
- [x] FalkorDB 实例图显示事件节点、参与者和关系，而不只是 Schema。
- [x] 时间轴在 2023-01-01、2023-01-02、2023-01-03 间切换时快照计数变化。
- [x] Actor 投影和事件证据视图可切换。
- [x] 节点详情能定位 Event ID、Story ID、Publisher、原始行号和 EvidenceRef。
- [x] 日期窗口 diff 使用“出现/未出现”措辞。
- [x] 相同配置再次运行返回 `reused=true`；最终运行库仅保留一个成功 Run，图计数保持 5,216/13,813。
- [x] FalkorDB 不可用时明确错误，不误称 SQLite 为实例数据。
- [x] 清理脚本 dry-run/apply 只删除时序运行对象，C-MAPSS 常规数据和模型未改变（C-MAPSS 图 10,251 节点/10,249 边）。

## 边界

本验收不包含 ICEWS14s 链接预测、YAGO11k Interval、GDELT/DBpedia/TGB 大规模导入、并发压力、高可用和生产部署。所有当前数值均应以本地实测为准。
