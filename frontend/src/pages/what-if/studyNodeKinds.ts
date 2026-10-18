export const nodeKinds: Record<string, { label: string; description: string; logic: boolean }> = {
  object_query: { label: '对象查询', description: '从本体读取对象与属性，例如设备、订单和库存。使用内置查询，无需 Logic Asset。', logic: false },
  function: { label: '函数模型', description: '运行已发布的 Logic Asset，例如缺料分析或节拍计算。必须指定逻辑及版本。', logic: true },
  aggregate_metric: { label: '指标汇总', description: '对输入计数、求和、求平均或取最大最小值。使用内置计算，无需 Logic Asset。', logic: false },
  compare: { label: '数值比较', description: '比较两个输入，计算差值、比值或是否相等。使用内置计算，无需 Logic Asset。', logic: false },
  time_series: { label: '时序计算', description: '通过已发布的 Logic Asset 计算随时间变化的数据。必须指定逻辑及版本。', logic: true },
  action_preview: { label: '动作预览', description: '预览指定 Action 的影响。绑定的是 Action ID 和版本，不是 Logic Asset。', logic: true },
}
