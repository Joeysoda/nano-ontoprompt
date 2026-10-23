# Palantir：可复用的查询（Functions）怎样被页面、API、MCP 共用

> 日期：2026-10-23 · 证据等级：官方文档（下列链接 2026-10-23 读取；本地 PDF `evidence/palantir-docs/functions_overview.pdf`）；“对我们的含义”为本项目推断
> 起因：要把 Workshop 里各应用自带的数据源，沉淀成本体级的可复用“本体查询”，并通过 API 和 MCP 开放。

## 一句话结论

Palantir 里可复用的计算是**函数**：发布后的版本不可变，使用方按**版本范围**引用（同一主版本内自动跟随）；Workshop 组件按函数**返回结果的形状**绑定（比如二维、三维聚合），函数的输入可以接页面变量；外部智能体通过 **Ontology MCP** 使用它，范围由“应用”圈定，实际权限取应用范围与用户权限的交集。

## 官方怎么说

- **函数在页面里的用法**（[Functions overview](https://www.palantir.com/docs/foundry/functions/overview/)，本地 PDF）：给 Workshop 返回对象集或变量值、作为表格里由函数计算的列、给 Workshop 图表做聚合、给 Quiver 算自定义指标；在服务端隔离环境里执行。
- **版本**（[Versioning](https://www.palantir.com/docs/foundry/functions/functions-versioning/)）：语义化版本 X.Y.Z，“chosen by their publishers and are immutable after creation”；使用方按范围引用，例如 `>=1.2.3 <2.0.0`。新增必填输入、改变输出类型、删除函数、删掉或调换输入都属于不兼容改动，要升主版本；新增可选输入、修 bug 只升次版本或修订号。发布前做兼容性检查并警告发布者。主版本 0 表示初始开发阶段，随时可能变。
- **页面绑定**（[Use functions](https://www.palantir.com/docs/foundry/functions/use-functions)、[Chart XY](https://www.palantir.com/docs/foundry/workshop/widgets-chart)、[Variables](https://www.palantir.com/docs/foundry/workshop/concepts-variables)、[Functions in Workshop](https://www.palantir.com/docs/foundry/workshop/functions-use)）：大多数变量可以由函数计算，依赖的变量变化时重算；Chart XY 的一层数据可以选 “Function aggregation”，要求函数返回二维或三维聚合，“useful if you want to have aggregation data be based on user selection”。
- **Ontology MCP**（[Overview](https://www.palantir.com/docs/foundry/ontology-mcp/overview)、[Authentication and authorization](https://www.palantir.com/docs/foundry/ontology-mcp/authentication-and-authorization)、[Application scopes](https://www.palantir.com/docs/foundry/ontology-sdk/third_party_app_scopes)）：开启后，Developer Console 里的一个**应用**本身成为 MCP 服务，开放这个应用范围内的对象类型、Action 类型和查询函数。没有单独的认证体系，沿用该应用的 OAuth（代表用户的授权码模式，或服务账号的客户端凭证模式）；实际权限是应用范围与用户（或服务账号）权限的交集；不带范围的令牌没有任何权限。另有面向本体搭建者的 Palantir MCP（改结构，不写数据），与本题无关。

## 对我们的含义（本项目推断）

| 设计点 | 原计划 | 按官方调整为 |
|---|---|---|
| 版本 | 整数版本，应用默认跟随最新 | 语义化版本；应用默认跟随**同一主版本**的最新；删列、改列类型、新增必填参数要升主版本，发布时自动检查并提示 |
| 绑定依据 | 按输出列的角色对应 | 先按**结果形状**（明细表 / 二维聚合 / 三维聚合 / 单行指标）决定能接哪些组件，再按列角色填字段 |
| 参数 | 时间范围、条数、等于筛选 | 同上，另外参数可以**绑定页面变量**，变量变化时重算 |
| MCP | 一个“开放范围”列出可用查询 | 同上；令牌属于某个开放范围，并以某个用户的身份调用，实际权限取交集；不带范围的令牌没有权限 |

## 存疑 / 暂不照搬

- 完整的 OAuth 授权流程（每个用户单独授权）第一版不做，用“开放范围 + 绑定到某个用户的访问令牌”代替，并在界面上说明这一点。
- 官方没有说明 MCP 服务的地址格式和传输方式，我们按 MCP 协议的通用做法实现（可流式的 HTTP）。
