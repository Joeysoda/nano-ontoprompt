# FactoryNet 时序本体构建演示手册

## 数据与入口

进入 `数据管理 → 时序数据`。页面不会自动选择数据。选择 FactoryNet CNC 官方样例后，系统显示文件、哈希、行数和许可证；也可导入 CSV、TSV、JSON、JSONL、XLSX 或 Parquet（最大 200 MB）。样例来自 [FactoryNet 数据集](https://huggingface.co/datasets/factorynet/factorynet)，本次演示文件为 `cnc_000.parquet`，25,286 行、57 列、18 个 episode，License 为 CC BY-NC-SA 4.0。

## 五步操作

1. **选择数据**：点击“选择此数据”。系统先做确定性画像，再调用已配置的 MiniMax-M3；分析失败时必须点击“重新分析”，不能跳过。
2. **筛选数据**：默认最大 5,000 行，并按 `episode_id` 均匀抽样。可改最大记录数、实体值和时间范围；页面同时显示源记录和筛选后记录。
3. **时间定义**：FactoryNet 使用 `Ordinal`，实体列为 `episode_id`，顺序列为 `time_s`。Ordinal 是相对顺序，不是日期；Instant 才是日期/时间戳；Interval 是 `[开始,结束)` 有效区间。
4. **本体映射**：可编辑字段目标属性和关系名称。默认结构为 `Machine → Episode → Observation`，Observation 连接机器、工序、刀具状态和下一条 Observation；数值信号保存在 Observation 属性中。
5. **确认构建**：创建新本体或选择已有本体后点击“开始构建”。一次请求创建本体和 ConstructionRun，后台按“读取、筛选、时间校验、规范化、节点、关系、FalkorDB、EvidenceRef、核对”执行。

## 结果调查

构建完成后进入时序图谱调查页。可选择渲染 50/100/200/500/1000 个节点或“全部（分批加载）”，切换全部节点与 Observation，隐藏标签，拖动 `event_seq/time_s` 时间轴，播放序列，并点击节点查看原始行和 EvidenceRef。重复相同配置使用稳定 ID 和 FalkorDB MERGE，不会增加节点或关系。

## 常见问题

- MiniMax 失败：检查模型页的 MiniMax-M3 状态和网络，页面不会静默回退。
- 图谱为空：查看任务状态和问题数；0 条有效记录或 0 条关系会标记失败。
- 页面白屏：路由级错误边界会显示“页面加载失败”，点击重新加载即可。
