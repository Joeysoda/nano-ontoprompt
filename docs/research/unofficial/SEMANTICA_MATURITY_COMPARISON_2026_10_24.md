# Semantica：项目成熟度与可用性对比

> 日期：2026-10-24 · 来源：https://github.com/Hawksight-AI/semantica ，clone 于 commit `22109ee0`（2026-10-23，v0.7.0）· 证据等级：第三方（开源项目源码，本项目实测统计）；判断性结论标“本项目推断”
> 原件：[`evidence/semantica-source-2026-10-24/`](evidence/semantica-source-2026-10-24/)（不进 git，见 [`../ORIGINALS.md`](../ORIGINALS.md)）
> 功能定位不同（它是给 AI 智能体用的上下文图/决策溯源库，我们是本体驱动的平台应用），本笔记只比**工程成熟度和上手可用性**。

## 一句话结论

Semantica 的成熟度主要体现在**工程外壳**：CI、版本发布、一条命令安装、自检命令、文档和示例、部署模板。代码本身并不比我们规整（有 6000 行的单文件，也出过“命令行调用不存在的接口”这种问题）。我们差的主要是**CI、版本号与更新记录、一键启动和自检、上手文档**，这些补起来成本低、见效快；它的“大而全”不该学。

## 对方怎么做的（源码实测）

| 维度 | Semantica | 我们（nano-ontoprompt-merged） | 依据 |
|---|---|---|---|
| 项目年龄 / 提交数 | 16 个月 / 3179 | 5 个月 / 184 | `git log`（实测） |
| 近 90 天提交 | 1118 | 99 | `git log --since`（实测） |
| 贡献者 | 161（前两名是同一人的两个账号，约 2000 次） | 5 | `git shortlog`（实测） |
| 业务代码 | Python 22.7 万行 + Explorer 前端 3.6 万行 | 后端 Python 7.5 万行（含测试）+ 前端 TS 2.9 万行 | `wc -l`（实测） |
| 测试 | 458 个测试文件，17.8 万行（与业务代码约 0.8:1） | 148 个测试文件 | 实测 |
| CI | 11 个 GitHub Actions：全量测试、多 Python 版本安装矩阵、CodeQL、容器扫描、Scorecard、依赖钉版本检查、文档、发布 | 无 | `.github/workflows/`（实测） |
| 版本与发布 | 语义化版本，tag 到 v0.7.0，PyPI 发布，CHANGELOG 2173 行（Keep a Changelog 格式，每条写清问题、修法、新增测试） | 无 tag、无版本号、无 CHANGELOG | 实测 |
| 安装 / 启动 | `pip install semantica`；按需装 extras（约 40 个，如 `llm-openai`、`db-snowflake`）；`uv.lock` 锁依赖 | 6 个 docker-compose 文件；后端只能在 Docker 里跑测试 | `pyproject.toml`、本仓库 |
| 自检 | `semantica doctor` 一条命令检查安装 | 无 | README |
| 入口 | 5 个命令：`semantica`、`-server`、`-worker`、`-explorer`、`-mcp` | Web 应用为主 | `pyproject.toml [project.scripts]` |
| MCP（让 AI 助手调用工具的标准协议） | 内置 MCP 服务：16 个工具 + 3 个资源 | 有按应用范围开放的 MCP（本分支） | `semantica/mcp_server/__init__.py` |
| 文档 | 文档站 31 页 + 40 个 notebook 教程（cookbook）+ CONTRIBUTING / 行为准则 / 引用格式 / 安全策略 | README 262 行；docs 以计划和调研为主 | 实测 |
| 部署 | Dockerfile + Helm / K8s / Fly / GCP / Azure / Railway / Render 模板 | docker-compose | `deploy/` |
| 许可证 | MIT | 无（实验室项目，公开展示走 OntoAct 仓库） | 实测 |

## 源码里看到的问题（它没那么成熟的地方）

- **广度先于质量**：CHANGELOG 自己记录了 `semantica pipeline` 整组命令（init/run/status/stop）调用的是**不存在的接口**，一跑就报错；`validate` 对任何输入都报“合法”（#2012）；`backup create` 没备份任何数据也报成功（#1820）。说明功能列表铺得比测试覆盖快。证据：`CHANGELOG.md`。
- **超大单文件**：`cli.py` 6357 行，`context/context_graph.py` 5927 行。README 示例用的 `find_similar_decisions`、`check_decision_rules` 只是“Easy way”包装，转调另一个同义方法，同一能力两套 API。
- **文档有过期**：`SECURITY.md` 支持版本表停在 0.2.x，当前已是 0.7.0；仓库地址在 Hawksight-AI 和 semantica-agi 之间不一致。
- 132 处 TODO / NotImplementedError / placeholder（含向量库、推理器等基础类的抽象方法，不全是缺功能）。
- 宣传口径偏大（“The Open Source Palantir for AI Agents”），功能上是 Python 库，没有对象类型 / Action / 应用搭建这一层（本项目推断）。

## 和我们现状的差距

1. **没有 CI**：测试只在本机 Docker 手动跑，回归靠人记得跑。这是最大差距。
2. **没有版本和更新记录**：外部看不出项目在进步、改了什么；我们的可复用查询已经有版本号，但项目整体没有。
3. **上手路径长**：没有“一条命令起来 + 一条命令自检”，新人要在 6 个 compose 文件里挑。
4. **依赖钉在旧版 Semantica**：`backend/requirements-reasoning.txt` 钉的 commit `3a69721`（v0.6.8 之后 27 次提交，2026-09-07）已落后 308 次提交，期间有推理、溯源相关修复。

## 可以借鉴（按优先级）

| 优先级 | 做法 | 对应模块 | 成本 |
|---|---|---|---|
| P0 | GitHub Actions：用现有 `scenario-validation-backend` 镜像跑后端 pytest + 前端 build/test；PR 必须通过 | 全仓库 | 低 |
| P0 | 一个默认启动方式 + `scripts/doctor` 自检（服务连通、FalkorDB、MinIO、LLM 配置、样例数据是否就绪） | 部署、README | 低 |
| P1 | 语义化版本 + CHANGELOG（Keep a Changelog），每条写“问题—修法—测试” | 发布流程 | 低 |
| P1 | README 改成“5 分钟上手”：一条命令启动 → 打开哪个页面 → 看到什么结果 | 文档 | 低 |
| P1 | 评估升级 Semantica 到 v0.7.0（先跑我们用到的 Reasoner / ContextGraph / CausalChainAnalyzer 路径的真实数据测试） | 推理工作台 | 中 |
| P2 | 依赖更新机器人（dependabot）+ CodeQL 安全扫描 | 全仓库 | 低 |

## 不该照搬 / 存疑

- 不学“约 40 个 extras + 几十个数据源连接器”的铺量：维护面大，已经出现命令不能用的情况。我们按真实场景（Azure PdM、DataCo）做深。
- 不学 6000 行单文件和同一能力两套 API。
- 提交数、贡献者数不可直接比：对方单人高频提交，且大量是 dependabot 和小修。
- 本次**未实际安装运行** Semantica（执行外部代码被权限拦截），“可用性”结论来自源码、配置和 CHANGELOG，不是实测运行结果。
