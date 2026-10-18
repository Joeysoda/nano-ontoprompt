# Object Explorer 视觉复评（2026-10-18）

承接 `plan_b_object_explorer_visual_review_2026-10-05.md`，只复评当时判为 **Partial** 的三项。官方参考图不变；当前截图由 `object-explorer-visual-evidence.spec.ts` 重新生成，统一保存为 `*_2026-10-18.png`，10-05 的原截图保留不动。

| 状态 | 参考 | 当前 | 结论 | 说明 |
|---|---|---|---|---|
| 嵌套 AND/OR/NOT | `palantir_object_explorer_nested_filters_reference.png` | `plan_b_current_nested_filters_2026-10-18.png` | **Pass** | 规则改为浅灰色的行，运算符是蓝色小块；规则之间的组合方式是橙色小块，并用竖线连接；嵌套组放在带边框的卡片里；"非 / + 条件 / + 条件组"移到分组底部作为工具条。只改了 className 和 CSS，条件 AST 和 URL 往返没有变化。 |
| 结果表与列控制 | `palantir_object_explorer_results_reference.png` | `plan_b_current_results_columns_2026-10-18.png` | **Pass** | 勾选框列和 Object 列固定冻结；其他列可以在列配置面板里冻结，冻结后排到前面，用 sticky 加累计偏移实现，最后一个冻结列带分隔阴影。表头带拖拽手柄，可以直接拖动换位置；列右边缘可以拖动调宽（80–480px）。上移/下移按钮保留，作为键盘可用的替代方式。 |
| 单对象面板 | `palantir_object_explorer_results_preview_reference.png` | `plan_b_current_results_multiselect_2026-10-18.png` | **Pass** | 面板宽度从固定 340px 改为占工作区的 38%（380–600px）；头部加了类型图标块；前 4 个普通属性用大号值显示在灰色摘要卡里，其余属性用紧凑的两列表格展示；空值显示为灰色斜体"无值"。配置过的 Object View 标签页不受影响。 |

仍然有的偏离（有意保留）：中文控件文案；动作预览这一安全步骤；参考图里的 Overview/Properties 标签页只在配置过 Object View 的对象上出现；标准视图没有自动生成标签页。

自动化断言：`object-explorer-workflow.spec.ts` 新增一个用例，验证冻结列为 sticky 且排到最前、拖动表头能换列、拖动边缘能变宽、摘要卡里显示"无值"。Explorer 相关的 e2e 一共 28/28 通过。
