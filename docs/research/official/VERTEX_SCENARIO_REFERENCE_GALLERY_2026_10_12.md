# Vertex 功能截图与参考资料

调研日期：2026-10-12。以下是公开资料中的真实截图；年代较早的界面用于固定视觉参考，不代表当前产品的所有功能。截图中的案例数据不作为本项目真实业务验证数据。

## 新增：AWS 合作案例，两个模型串联

![AWS Vertex Simulation](evidence/vertex-scenario-research-2026-10-12/images/09-aws-vertex-simulation.png)

[原文：AWS / Palantir 联合作者，2021-09-15](https://aws.amazon.com/blogs/apn/how-palantir-foundry-helps-customers-build-and-deploy-ai-powered-decision-making-applications/)。图 5 展示对象网络、左侧图层列表，以及右侧两个模型的输入输出表。覆盖值、基线与情景结果、模型之间的箭头都可见。适合参考多模型 Simulation 面板。本文是合作方公开案例，属于直接产品展示。

## A：主布局——对象关系图＋Simulation

![对象图与比较表](evidence/vertex-scenario-research-2026-10-12/images/01-scenario-comparison.jpg)

[官方来源](https://www.palantir.com/docs/foundry/vertex/scenarios-getting-started)。观察重点：对象节点展示模拟值；右侧按模型组织输入输出；方案按列比较。用户已选择以此布局为主。

## B：工厂流程图

![工厂流程图](evidence/vertex-scenario-research-2026-10-12/images/02-production-diagram.jpg)

[官方来源](https://www.palantir.com/docs/foundry/vertex/example-use-cases)。观察重点：设备图标、区域分组、固定工艺布局。作为同一研究的可切换视图。

## C：关系图＋时间序列

![关系图与时间序列](evidence/vertex-scenario-research-2026-10-12/images/03-system-graph.jpg)

[官方来源](https://www.palantir.com/docs/foundry/vertex/example-use-cases)。观察重点：关系边标签、客户分组、底部 Series。作为同一研究的可切换视图。

## Action 配置与时间选择

![添加 Action](evidence/vertex-scenario-research-2026-10-12/images/04-actions.jpg)

![配置 Action](evidence/vertex-scenario-research-2026-10-12/images/05-action-configuration.jpg)

![时间选择](evidence/vertex-scenario-research-2026-10-12/images/06-time-selection.jpg)

[Action 来源](https://www.palantir.com/docs/foundry/vertex/scenarios-getting-started)，[时间与选项来源](https://www.palantir.com/docs/foundry/vertex/scenarios-options)。这些是配置过程的截图，不只是最终结果页面。

## 工业案例：油气情景分析

![油气情景分析](evidence/vertex-scenario-research-2026-10-12/images/08-oil-gas-scenarios.png)

[公开白皮书](https://www.palantir.com/assets/xrfr7uokpv1b/4iJcF4I6pdyGbzGbLXFcN9/181518cf67af2106e87bfa4be4c9601e/Whitepaper_-_Vertex_for_Oil___Gas.pdf)，PDF 第 11 页。可参考配置、分析、机会监控之间的业务流程；小图不适合逐像素复刻。

## 官方文档以外的资料：哪些值得用

| 资料 | 能提供什么 | 使用边界 |
| --- | --- | --- |
| [AWS 合作案例](https://aws.amazon.com/blogs/apn/how-palantir-foundry-helps-customers-build-and-deploy-ai-powered-decision-making-applications/) | 真实多模型 Vertex 功能截图 | 2021 年历史界面；直接展示证据 |
| [Gal Levinshtein，2024-12-22](https://www.linkedin.com/pulse/technical-majesty-palantir-foundry-os-deep-dive-gal-levinshtein-a9bee) | 含 Vertex 用例图片和其他 Foundry 界面 | Vertex 用例图片明确标注来自 Palantir，多为转载，不能当作新增独立功能证据；文中的示例代码不作为 API 合约 |
| [开发者关于 Vertex 模型配置的实际问题](https://community.palantir.com/t/using-a-model-in-vertex/983) | 输入输出禁用、绑定配置困难的用户反馈 | 用于设计明确的禁用原因和绑定诊断；不是完整操作截图教程 |
| [第三方培训资料镜像](https://palantirthing.vercel.app/aip/2026-03-02-speedrun-your-first-aip-workflow) | Add objects、Search Around、布局、Save as template 的步骤 | 已下载核对；Vertex 段中的图是培训架构示意，不能冒充 Vertex UI 截图 |
| [Demo Day 第三方视频转录索引](https://lilys.ai/en/notes/palantir-20260127/palantir-demo-day-videos-gotham-foundry-apollo) | 定位约 20:22 的供应商减供情景演示 | 对照 [2021 原始演讲稿](https://s26.q4cdn.com/381064750/files/doc_downloads/2021/English-Transcript-Q1-2021-Palantir-Demo-Day.pdf)；网页 URL 年份不等于产品版本；本轮没有提取视频帧 |

搜索时排除了 Google Vertex AI、股票讨论和没有可核对界面的泛泛文章。第三方完整 Scenario 配置截图确实较少；目前最有价值的新增视觉证据是 AWS 图 5。没有把转载同一张图片算成多个独立功能。

## 对计划的影响

1. 主画布展示供应商、物料、工厂和订单；计算节点放在独立模型配置区。
2. Simulation 支持多个模型分段显示、跨模型绑定标识和按情景列比较。
3. 增加图层、选择、搜索、分组、布局、时间选择与 Series 面板。
4. 自定义模板分开保存计算定义和对象图展示规则。
5. 历史截图中的旧模型配置已经被当前官方文档标为 Sunset；复刻视觉交互，执行边界采用明确的函数与 Action 契约。
