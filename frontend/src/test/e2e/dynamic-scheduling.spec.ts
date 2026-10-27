import { expect, test } from '@playwright/test'

test('drag a generic schedule task, clear validation and save through a Scenario Action', async ({ page }) => {
  const ontologyId = 'schedule-ontology', appId = 'schedule-app'
  const previews: Array<Record<string, unknown>> = [], saves: Array<Record<string, unknown>> = [], recommendationRuns: Array<Record<string, unknown>> = []
  const definition = {
    schema: 'workshop-app/v1', settings: { time_range: 'all', refresh_minutes: 0 },
    variables: [{ id: 'scenario', label: 'Scenario', default: 's1' }],
    data_sources: [{ id: 'tasks', kind: 'object_set', label: 'Factory tasks', object_type: 'FlexibleTask' }],
    pages: [{ id: 'schedule', title: '排程', template: 'free', layout: [{ i: 'gantt', x: 0, y: 0, w: 24, h: 14 }], widgets: [{
      id: 'gantt', type: 'schedule_gantt', title: '生产排程', source: 'tasks', path: 'rows', on_select: null, region: null,
      props: { id_field: 'id', label_field: 'title', resource_field: 'line', start_field: 'start', end_field: 'end', dependency_field: 'predecessors', layer_field: 'layer', object_type_field: 'object_type', object_id_field: 'object_id',
        resources: [{ id: 'line-a', label: 'Line A' }, { id: 'line-b', label: 'Line B' }, { id: 'line-c', label: 'Line C' }], layers: [{ id: 'production', label: 'Production', style: 'standard', color: '#2563eb' }, { id: 'maintenance', label: 'Maintenance', style: 'standard', color: '#7c3aed' }], snap_minutes: 15, allocation_behavior: 'no_overlaps', resource_cardinality: 'many', resource_drop_behavior: 'replace', enforce_rules: true,
        operating_start_minute: 0, operating_end_minute: 1440, scenario_variable: 'scenario', action_type_id: 'move-task', target_parameter: 'task', start_parameter: 'starts_at', end_parameter: 'ends_at', resource_parameter: 'line',
        recommendation_asset_id: 'search-asset', validation_asset_id: 'validation-asset', enforce_business_rules: true, create_action_type_id: 'create-task', create_id_parameter: 'id', create_label_parameter: 'title', create_start_parameter: 'starts_at', create_end_parameter: 'ends_at', create_resource_parameter: 'line', create_default_label: 'Draft task', create_layer: 'production' },
    }]}],
  }
  const rows = [
    { id: 'task-1', title: 'Cut', line: 'line-a', layer: 'production', start: '2026-01-01T08:00:00Z', end: '2026-01-01T10:00:00Z', object_type: 'FlexibleTask', object_id: 'task-1' },
    { id: 'task-2', title: 'Polish', line: ['line-a', 'line-b'], layer: 'maintenance', start: '2026-01-01T09:00:00Z', end: '2026-01-01T11:00:00Z', object_type: 'FlexibleTask', object_id: 'task-2' },
    { id: 'task-3', title: 'Inspect', line: [], layer: 'production', start: null, end: null, predecessors: ['task-2'], object_type: 'FlexibleTask', object_id: 'task-3' },
    { id: 'task-4', title: 'Pack', line: 'line-b', layer: 'maintenance', start: '2026-01-01T11:00:00Z', end: '2026-01-01T12:00:00Z', predecessors: ['task-2'], object_type: 'FlexibleTask', object_id: 'task-4' },
  ]
  await page.addInitScript(() => localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'schedule', user: { id: 'u1', role: 'editor' } }, version: 0 })))
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith(`/workshop-apps/${appId}`)) return json({ id: appId, name: 'Scheduling', description: '', head_version: 1, head: { version: 1, definition } })
    if (path.endsWith(`/workshop-apps/${appId}/sources/tasks`)) return json({ source_id: 'tasks', kind: 'object_set', status: 'completed', completed_at: null, outputs: { count: rows.length, rows } })
    if (path.endsWith('/queries') && request.method() === 'GET') return json({ queries: [] })
    if (path.endsWith('/logic-assets') && request.method() === 'GET') return json({ assets: [
      { id: 'search-asset', asset_key: 'find_slots', name: '可行时间推荐', version: '1.0.0', status: 'published', side_effect: false, output_schema: { properties: { schema: { const: 'schedule-search/v1' } } } },
      { id: 'validation-asset', asset_key: 'validate_schedule', name: '生产约束校验', version: '2.0.0', status: 'published', side_effect: false, output_schema: { properties: { schema: { const: 'schedule-validation/v1' } } } },
      { id: 'wrong-asset', asset_key: 'other', name: '不兼容能力', version: '1.0.0', status: 'published', side_effect: false, output_schema: { properties: { schema: { const: 'other/v1' } } } },
    ] })
    if (path.endsWith('/scenarios/s1') && request.method() === 'GET') return json({ id: 's1', etag: 7, head_revision: 2 })
    if (path.endsWith('/logic-assets/search-asset/run')) { recommendationRuns.push(request.postDataJSON()); return json({ status: 'completed', output: { schema: 'schedule-search/v1', row_group: { title: '可行替代方案', container_ids: ['line-b'], highlights: [
      { type: 'SLOT', container_id: 'line-c', domain: { start: '2026-01-01T08:00:00Z', end: '2026-01-01T10:00:00Z' }, comment: 'Line C 可用' },
      { type: 'PUCK', schedulable_object_primary_key: 'task-2', comment: '可联动调整' },
    ] }, source_puck_ids: ['task-1'] } }) }
    if (path.endsWith('/logic-assets/validation-asset/run')) return json({ status: 'completed', output: { schema: 'schedule-validation/v1', violations: [] } })
    if (path.endsWith('/scenarios/s1/actions:preview')) { previews.push(request.postDataJSON()); return json({ edit_count: 3, edits: [] }) }
    if (path.endsWith('/scenarios/s1/actions') && request.method() === 'PUT') { saves.push(request.postDataJSON()); return json({ revision: 3 }) }
    return json({})
  })

  await page.goto(`/apps/workshop/${appId}?ontology=${ontologyId}`)
  const gantt = page.getByTestId('schedule-gantt')
  await expect(gantt).toContainText('1 项排程校验问题')
  await expect(gantt).toContainText('未排任务（1）')
  await expect(gantt.getByRole('button', { name: /Polish/ })).toHaveCount(2)
  await expect(gantt.getByLabel('排程层图例')).toContainText('Production · standard')
  await expect(gantt.getByLabel('任务依赖箭头')).toBeVisible()
  await expect(gantt.getByRole('button', { name: '检查 Scenario 变更' })).toBeDisabled()

  await gantt.getByRole('button', { name: /Cut/ }).click()
  await gantt.getByRole('button', { name: '为所选任务查找推荐' }).click()
  await expect(gantt).toContainText('可行替代方案 · 1 个时间段 · 1 个任务')
  await expect(gantt.locator('[title="Line C 可用"]')).toHaveCount(1)
  expect(recommendationRuns).toHaveLength(1)
  expect(((recommendationRuns[0].inputs as Record<string, unknown>).trigger as Record<string, unknown>).schedulable_object_primary_key).toBe('task-1')
  expect(((recommendationRuns[0].inputs as Record<string, unknown>).schedule_objects as Array<Record<string, unknown>>)[0]).toMatchObject({ id: 'task-1', container_id: 'line-a', attributes: { title: 'Cut' } })

  await gantt.getByRole('button', { name: /Cut/ }).dragTo(gantt.locator('[data-resource="line-c"]'))
  const createRow = gantt.locator('[data-resource="line-c"]'), box = await createRow.boundingBox()
  expect(box).not.toBeNull()
  await page.keyboard.down('Shift'); await page.mouse.move(box!.x + box!.width * 0.05, box!.y + 20); await page.mouse.down(); await page.mouse.move(box!.x + box!.width * 0.2, box!.y + 20); await page.mouse.up(); await page.keyboard.up('Shift')
  await expect(gantt).toContainText('Draft task')
  await expect(gantt).toContainText('2 待保存')
  await expect(gantt.getByText('1 项排程校验问题')).toHaveCount(0)
  await expect(gantt.getByRole('button', { name: '检查 Scenario 变更' })).toBeDisabled()
  await gantt.getByRole('button', { name: '运行业务校验' }).click()
  await expect(gantt).toContainText('业务校验通过')
  await gantt.getByRole('button', { name: '检查 Scenario 变更' }).click()
  await expect(gantt).toContainText('Action 预览：3 项本体修改')
  await gantt.getByRole('button', { name: '保存到 Scenario' }).click()
  await expect(gantt).toContainText('已保存为 Scenario 修订')
  expect(previews).toHaveLength(1); expect(saves).toHaveLength(1)
  expect((previews[0].actions as Array<Record<string, unknown>>)[0]).toMatchObject({ action_type_id: 'move-task', parameters: { task: { object_type: 'FlexibleTask', object_id: 'task-1' }, line: 'line-c' } })
  expect((saves[0].actions as Array<Record<string, unknown>>)[1]).toMatchObject({ action_type_id: 'create-task', parameters: { title: 'Draft task', line: ['line-c'] } })
  expect(previews[0].expected_etag).toBe(7)

  await gantt.getByRole('button', { name: 'Inspect' }).click()
  await gantt.getByLabel('所选任务资源').selectOption('line-c')
  await expect(gantt).toContainText('1 待保存')
  await expect(gantt).toContainText('Inspect 必须在 Polish 完成后开始')
  await expect(gantt.getByRole('button', { name: '提前一步' })).toBeEnabled()

  await page.goto(`/apps/workshop/${appId}/edit?ontology=${ontologyId}`)
  await page.getByRole('button', { name: '编辑组件 生产排程' }).click()
  await expect(page.getByLabel('推荐推理能力')).toHaveValue('search-asset')
  await expect(page.getByLabel('推荐推理能力').locator('option')).toHaveText(['— 不启用 —', '可行时间推荐 · find_slots@1.0.0'])
  await expect(page.getByLabel('业务校验能力')).toHaveValue('validation-asset')
})
