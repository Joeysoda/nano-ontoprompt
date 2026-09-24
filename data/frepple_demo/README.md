# frePPLe 制造业务数据接入

## 来源与范围

- 官方仓库： https://github.com/frePPLe/frepple
- 固定提交：`73e5be3d1573db043209111325dd921d68cf4b88`
- 文件：`freppledb/input/fixtures/manufacturing_demo.json`
- SHA-256：`d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef`
- 官方许可原文随附 `COPYING.frepple`；没有引入 frePPLe 运行时或商业模块。
- 这是官方合成的桌椅制造示例，不是真实生产企业的数据。

384 条源记录：363 个业务对象，21 条系统参数/界面配置。
17 类业务对象，903 条源外键关系，1266 条对象/关系证据。
220 条需求中 204 条 closed 历史记录、16 条 open。
源计划日期是 2021-01-01，不平移到今天，不推断时区。

## 使用

打开 http://localhost:15173/ontologies/frepple-d7eb98078882b234c395fd05?tab=manufacturing

1. 在“业务数据”查看统计、原始计划日期与缺失信息。
2. 搜索 `Demand 01`，点击该待处理需求。
3. 查看交期、数量、客户和产品，展开“来源证据”核对原始 JSON。
4. 点击关联对象，浏览结构依赖。依赖范围包括共享资源配置，不是订单独占资源，也不是因果预测。
5. 切换到工序定义，选择 `Assemble chair`，查看固定时间和单位加工时间。
6. 原有“本体”“实体”页面仍可浏览导入的类型与实例。

业务页面和 Agent 的 `manufacturing_context` 工具读取固定导入快照，明确返回 snapshot_only。
它们不把后来编辑的实时图和旧来源混为一谈。要提供实时 What-if 上下文需下一阶段的场景读取契约。

## 重现（已有后端依赖与数据库环境）

```sh
PYTHONPATH=backend python scripts/import_frepple_demo.py data/frepple_demo/manufacturing_demo.json
PYTHONPATH=backend python scripts/verify_frepple_demo.py data/frepple_demo/manufacturing_demo.json
PYTHONPATH=backend python -m unittest discover -s backend/tests -p test_manufacturing_data.py -v
```

导入复用 Dataset、DatasetVersion、OntologyProject、Entity、EntityInstance、Relation、ConstructionRun、EvidenceRef 和既有图投影。
不新增数据库表。每条外键关系以源 JSON 下标和原字段追溯；关联记录保留为对象，避免丢失数量、优先级、日期。
使用内容寻址 ID，重复导入不增加重复对象/关系/证据。仅刷新该专用样例的元数据与图投影，不应用于编辑过的生产本体。
图投影失败会明确记录 failed，可以重试；并非跨 SQL 与图数据库的原子事务。

运行中的验证环境使用原有 SQLite 状态卷与 FalkorDB；未迁移为 PostgreSQL。
当前 MinIO 不可用，原始文件使用既有本地存储回退。重建容器时必须保留/挂载 storage 目录或重新运行幂等导入。

## 未实现 / 不应宣称

- 没有排程、Scenario 修改、仿真、成本计算、任务到订单的自动分配。
- 未补造产品单位、货币、替代设备能力、换型或维修时间。
- 未把 FactoryNet 与此样例假装关联为同一家真实工厂。
- Agent 工具已加入，但本次没有付费模型调用或验证自然语言自动规划；原有决策流程仍要求规则推理依据。
- 页面缺失项是该固定版本的审阅清单，不是适用于任意 ERP 的自动数据质量引擎。

## 验证

测试覆盖源字段无损、所有外键、悬空引用拒绝、重复 ID 拒绝、未知模型拒绝、固定校验和、需求状态、路线子步骤、权限隔离、失败导入拒绝。
浏览器脚本：`frontend/tests/manufacturing-data.e2e.cjs`，检查需求筛选、证据展开、工序单位、刷新恢复、错误对象 404 和页面异常。
截图：`manufacturing-demand.png`。
