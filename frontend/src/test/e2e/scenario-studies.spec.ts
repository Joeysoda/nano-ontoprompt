import { expect, test } from '@playwright/test'

test('custom manufacturing Study can publish, run and compare isolated Cases', async ({ page }) => {
  const ontologyId = 'factory-ontology'
  const studyId = 'study-cycle-time'
  const cycleTemplate = {
    spec_version: 'scenario-study/v1', context_policy: { scenario_mode: 'pinned_on_run', time_required: false },
    parameters: [{ key: 'setup_seconds', label: '换线准备时间', type: 'decimal', default: 30 }, { key: 'unit_seconds', label: '单件加工时间', type: 'decimal', default: 2 }, { key: 'quantity', label: '批量', type: 'integer', default: 10 }],
    nodes: [{ key: 'cycle', kind: 'function', implementation: { key: 'cycle_time_v1', version: '1.0.0' }, inputs: { setup_seconds: { parameter: 'setup_seconds' }, unit_seconds: { parameter: 'unit_seconds' }, quantity: { parameter: 'quantity' } }, outputs: { minutes: 'total_minutes' } }],
    edges: [], metrics: [{ key: 'cycle_minutes', label: '批次周期', type: 'decimal', direction: 'minimize', unit: 'min', source: 'cycle.minutes' }], constraints: [], budgets: {},
  }
  type MockCase = { id: string; name: string; case_kind: 'baseline' | 'candidate'; scenario_id: string; study_version: number; parameter_overrides: Record<string, unknown>; etag: number; status: string; source_case_id?: string }
  const caseRows: MockCase[] = []
  const runs = new Map<string, Record<string, unknown>>()
  const scenarios: Array<Record<string, unknown>> = []
  type MockStudy = { id: string; ontology_id: string; name: string; description: string; status: string; draft_spec: Record<string, unknown>; latest_version: number; etag: number }
  let study: MockStudy | null = null
  let etag = 1
  let currentVersion = 0
  let currentRun = 0

  await page.addInitScript(() => {
    localStorage.setItem('token', 'scenario-study-browser-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'scenario-study-browser-test', user: { id: 'test-user', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route('**/api/v2/ontologies/factory-ontology/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v2/ontologies/factory-ontology', '')
    const method = request.method()
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    if (path === '/scenario-study-definitions' && method === 'GET') return json({ items: study ? [study] : [] })
    if (path === '/scenario-study-templates' && method === 'GET') return json({ items: [{ template_id: 'cycle_time', name: '工艺节拍变化', description: '节拍样例', spec: cycleTemplate }] })
    if (path === '/scenario-study-definitions' && method === 'POST') {
      const body = request.postDataJSON() as { name: string; description: string; draft_spec: Record<string, unknown> }
      study = { id: studyId, ontology_id: ontologyId, name: String(body.name), description: String(body.description), status: 'draft', draft_spec: body.draft_spec as Record<string, unknown>, latest_version: 0, etag }
      return json(study, 201)
    }
    if (path === `/scenario-study-definitions/${studyId}` && method === 'GET') return json({ ...study, versions: currentVersion ? [{ version: currentVersion, compatibility: 'compatible', spec_hash: 'sha256-test', change_note: 'initial', spec: study?.draft_spec }] : [] })
    if (path === `/scenario-study-definitions/${studyId}` && method === 'PATCH') {
      const body = request.postDataJSON() as { name: string; draft_spec: Record<string, unknown> }
      if (!study) return json({ detail: 'Study was not created' }, 404)
      etag += 1
      study = { ...study, name: body.name, draft_spec: body.draft_spec, etag }
      return json(study)
    }
    if (path === `/scenario-study-definitions/${studyId}:validate` && method === 'POST') return json({ valid: true, spec_hash: 'sha256-test', topological_order: ['cycle', 'summarize'] })
    if (path === `/scenario-study-definitions/${studyId}:publish` && method === 'POST') {
      if (!study) return json({ detail: 'Study was not created' }, 404)
      currentVersion += 1
      study = { ...study, status: 'published', latest_version: currentVersion }
      return json({ id: 'version-1', study_id: studyId, version: currentVersion, spec_hash: 'sha256-test', compatibility: 'compatible', change_note: 'initial' }, 201)
    }
    if (path === `/scenario-study-definitions/${studyId}/cases` && method === 'GET') {
      const includeArchived = new URL(request.url()).searchParams.get('include_archived') === 'true'
      return json({ items: caseRows.filter(row => includeArchived || row.status === 'active') })
    }
    if (path === `/scenario-study-definitions/${studyId}/cases` && method === 'POST') {
      const body = request.postDataJSON()
      const row = { id: `case-${caseRows.length + 1}`, name: body.name, case_kind: body.case_kind, scenario_id: body.scenario_id, study_version: body.study_version, parameter_overrides: body.parameter_overrides, etag: 1, status: 'active' }
      if (body.source_case_id && !caseRows.some(existing => existing.id === body.source_case_id)) throw new Error('candidate source case was not found')
      caseRows.push(row)
      return json(row, 201)
    }
    if (/\/scenario-study-definitions\/study-cycle-time\/cases\/case-\d+$/.test(path) && method === 'PATCH') {
      const target = caseRows.find(row => path.endsWith(`/${row.id}`))
      const body = request.postDataJSON()
      if (!target) return json({ detail: 'Case was not found' }, 404)
      Object.assign(target, { name: body.name, parameter_overrides: body.parameter_overrides, etag: target.etag + 1 })
      return json(target)
    }
    if (path.endsWith(':archive') && method === 'POST') {
      const caseId = path.split('/').at(-1)?.replace(':archive', '')
      const target = caseRows.find(row => row.id === caseId)
      if (!target) return json({ detail: 'Case was not found' }, 404)
      target.status = 'archived'
      target.etag += 1
      return json(target)
    }
    if (path.endsWith(':compile') && method === 'POST') return json({ id: 'plan-1', plan_hash: 'sha256-plan-hash', topological_order: ['cycle', 'summarize'] }, 201)
    if (/\/cases\/case-\d+\/runs$/.test(path) && method === 'POST') {
      const id = `run-${++currentRun}`
      const target = caseRows.find(row => path.includes(`/${row.id}/`))
      const value = target?.case_kind === 'baseline' ? 0.8333333333 : 1.1666666667
      const queued = currentRun === 1
      const result = { id, case_id: target?.id, status: queued ? 'queued' : 'completed', progress: queued ? 0 : 100, current_stage: queued ? 'queued' : 'complete', result_manifest: { metrics: [{ key: 'cycle_minutes', label: '批次周期', value, unit: 'min', direction: 'minimize' }], provenance: { run_plan_id: 'plan-1', metadata_digest: 'metadata-test' } }, stages: [{ key: 'node_000', status: queued ? 'pending' : 'completed' }, { key: 'node_001', status: queued ? 'pending' : 'completed' }] }
      runs.set(id, result)
      return json(result, 202)
    }
    if (path.startsWith('/scenario-study-runs/') && path.endsWith(':cancel') && method === 'POST') {
      const id = path.split('/').at(-1)?.replace(':cancel', '') ?? ''
      const result = { ...runs.get(id), status: 'cancelled', current_stage: 'cancelled' }
      runs.set(id, result)
      return json(result)
    }
    if (path.startsWith('/scenario-study-runs/') && path.endsWith(':retry') && method === 'POST') {
      const previousId = path.split('/').at(-1)?.replace(':retry', '') ?? ''
      const previous = runs.get(previousId)
      const id = `run-${++currentRun}`
      const result = { ...previous, id, status: 'completed', progress: 100, current_stage: 'complete', retry_of_run_id: previousId }
      runs.set(id, result)
      return json(result, 202)
    }
    if (path.startsWith('/scenario-study-runs/') && method === 'GET') return json(runs.get(path.split('/').at(-1) ?? '') ?? {})
    if (path === `/scenario-study-definitions/${studyId}/versions/${currentVersion}/compare` && method === 'GET') {
      const columns = caseRows.filter(row => row.status === 'active').map((row, index) => {
        const value = row.case_kind === 'baseline' ? 0.8333333333 : 1.1666666667
        return { case: row, run_id: `run-${index + 1}`, run_status: 'completed', metrics: [{ key: 'cycle_minutes', label: '批次周期', value, unit: 'min' }], deltas_from_baseline: row.case_kind === 'baseline' ? {} : { cycle_minutes: { absolute: 0.3333333334, unit: 'min' } } }
      })
      return json({ study_id: studyId, study_version: currentVersion, cases: columns })
    }
    if (path === '/scenarios' && method === 'GET') return json({ scenarios })
    if (path === '/scenarios:from-live' && method === 'POST') {
      const scenario = { id: `scenario-${scenarios.length + 1}`, name: request.postDataJSON().name, head_revision: 0, status: 'active' }
      scenarios.push(scenario)
      return json(scenario, 201)
    }
    if (path === '/logic-assets' && method === 'GET') return json({ assets: [] })
    if (path === '/logic-assets/seed' && method === 'POST') return json({ assets: [] })
    return json({ detail: { code: 'unmocked_test_request', message: `${method} ${path}` } }, 500)
  })

  await page.goto(`/ontologies/${ontologyId}/scenario-studies`)
  await expect(page.getByTestId('scenario-studies-workbench')).toBeVisible()
  await page.getByText('研究库与模板 · 选择研究').click()
  await page.getByLabel('新研究名称').fill('产线节拍情景研究')
  await page.getByRole('button', { name: '＋ 创建自定义研究' }).click()
  await expect(page.getByRole('heading', { name: '研究定义' })).toBeVisible()
  await page.getByRole('button', { name: '载入模板并注册计算逻辑' }).click()
  await page.getByText('高级 JSON 定义').click()
  const editor = page.getByLabel('Study definition JSON')
  await expect(editor).toContainText('cycle_time_v1')

  const studySpec = JSON.parse(await editor.inputValue())
  studySpec.nodes.push({ key: 'summarize', kind: 'aggregate_metric', inputs: { minutes: { from: 'cycle.minutes' } }, outputs: { total: 'number' }, config: { operation: 'sum' } })
  studySpec.edges = [{ source: 'cycle.minutes', target: 'summarize.minutes' }]
  studySpec.metrics[0].source = 'summarize.total'
  await editor.fill(JSON.stringify(studySpec, null, 2))
  await page.getByRole('button', { name: '验证定义' }).click()
  await expect(page.getByRole('status')).toContainText('cycle → summarize')
  await page.getByRole('button', { name: '发布版本' }).click()
  await expect(page.getByRole('status')).toContainText('不可变版本 v1')

  await page.getByRole('button', { name: '＋ 从当前主本体创建情景' }).click()
  await page.getByRole('button', { name: '创建 Case' }).click()
  await expect(page.getByRole('row', { name: /基线方案 active/ })).toBeVisible()
  await page.getByRole('button', { name: '运行', exact: true }).first().click()
  await expect(page.getByRole('button', { name: '取消运行' })).toBeVisible()
  await page.getByRole('button', { name: '取消运行' }).click()
  await expect(page.getByText('cancelled · 0%')).toBeVisible()
  await page.getByRole('button', { name: '重试运行' }).click()
  await expect(page.getByText('completed · 100%')).toBeVisible()
  await expect(page).toHaveURL(/case=case-1/)
  await expect(page).toHaveURL(/run=run-2/)
  await expect(page.getByLabel('情景输入输出对比').getByText('0.8333 min', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '复制为候选' }).first().click()
  await expect(page.getByLabel('隔离情景')).toHaveValue('')
  await expect(page.getByRole('button', { name: '创建 Case', exact: true })).toBeDisabled()
  await page.getByLabel('Case 名称').fill('延长批量方案')
  await page.getByText('高级参数覆盖 JSON', { exact: true }).click()
  await page.getByLabel('Case parameter overrides').fill('{"quantity":20}')
  await page.getByRole('button', { name: '＋ 从当前主本体创建情景' }).click()
  await page.getByRole('button', { name: '创建 Case' }).click()
  await expect(page.getByRole('row', { name: /延长批量方案 active/ })).toBeVisible()
  await page.getByRole('button', { name: '运行', exact: true }).last().click()
  await expect(page.getByLabel('情景输入输出对比').getByText('1.1667 min', { exact: true })).toBeVisible()
  await expect(page.getByText(/cycle_minutes: \+0\.3333 min/).first()).toBeVisible()
  await page.getByRole('button', { name: '查看结果', exact: true }).first().click()
  await expect(page).toHaveURL(/case=case-1/)
  await expect(page).toHaveURL(/run=run-1/)
  await page.getByRole('button', { name: '编辑' }).last().click()
  await page.getByLabel('编辑 Case 名称').fill('延长批量方案修订')
  await page.getByRole('button', { name: '保存' }).last().click()
  await expect(page.getByRole('row', { name: /延长批量方案修订/ })).toBeVisible()
  await page.getByRole('button', { name: '归档' }).last().click()
  await expect(page.getByRole('row', { name: /延长批量方案修订/ })).toHaveCount(0)
  await page.getByLabel('Show archived cases').check()
  await expect(page.getByRole('row', { name: /延长批量方案修订 archived/ })).toBeVisible()
})

test('graph edits persist dependencies, rename references and remove input bindings', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1100 })
  const spec = {
    spec_version: 'scenario-study/v1', context_policy: {}, parameters: [],
    nodes: [
      { key: 'source', kind: 'aggregate_metric', inputs: { value: 5 }, outputs: { total: 'number' }, config: { operation: 'sum' } },
      { key: 'target', kind: 'aggregate_metric', inputs: { value: 0 }, outputs: { total: 'number' }, config: { operation: 'sum' } },
    ], edges: [] as Array<{ source: string; target: string }>, metrics: [{ key: 'total', label: '合计', type: 'decimal', direction: 'neutral', source: 'source.total' }], constraints: [], budgets: {},
  }
  let saved = structuredClone(spec)
  let etag = 1
  await page.addInitScript(() => {
    localStorage.setItem('token', 'graph-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'graph-test', user: { id: 'test-user', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route('**/api/v2/ontologies/graph-test/**', route => {
    const path = new URL(route.request().url()).pathname.split('/graph-test')[1]
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    const study = { id: 'graph-study', name: '图形测试', draft_spec: saved, latest_version: 0, etag, status: 'draft' }
    if (path === '/scenario-study-definitions') return json({ items: [study] })
    if (path === '/scenario-study-definitions/graph-study' && route.request().method() === 'PATCH') { saved = route.request().postDataJSON().draft_spec; etag++; return json({ ...study, etag, draft_spec: saved }) }
    if (path === '/scenario-study-definitions/graph-study') return json({ ...study, versions: [] })
    if (path === '/scenario-study-templates') return json({ items: [] })
    if (path === '/scenarios') return json({ scenarios: [] })
    if (path === '/logic-assets') return json({ assets: [] })
    return json({ items: [] })
  })
  await page.goto('/ontologies/graph-test/scenario-studies?study=graph-study')
  const source = page.locator('.react-flow__node[data-id="source"]')
  const target = page.locator('.react-flow__node[data-id="target"]')
  await expect(source).toBeVisible()
  await page.waitForTimeout(400)
  const connect = async (output: import('@playwright/test').Locator, input: import('@playwright/test').Locator) => {
    const start = await output.boundingBox(), end = await input.boundingBox()
    if (!start || !end) throw new Error('Ports are not visible')
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
    await page.mouse.down()
    await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 15 })
    await page.mouse.up()
  }
  await connect(source.locator('.source'), target.locator('.target'))
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect.poll(() => saved.edges).toEqual([{ source: 'source.total', target: 'target.value' }])
  await expect(page.getByRole('button', { name: '保存草稿' })).toBeEnabled()
  await page.waitForTimeout(300)
  await connect(target.locator('.source'), source.locator('.target'))
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await source.click()
  await page.getByLabel('节点键', { exact: true }).fill('renamed')
  await page.getByLabel('节点键', { exact: true }).press('Tab')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect.poll(() => saved.edges[0].source).toBe('renamed.total')
  expect(saved.metrics[0].source).toBe('renamed.total')
  expect(saved.nodes[1].inputs.value).toEqual({ from: 'renamed.total' })
  const renamed = page.locator('.react-flow__node[data-id="renamed"]')
  await page.waitForTimeout(300)
  const before = await renamed.boundingBox()
  if (!before) throw new Error('Node is not visible')
  await page.mouse.move(before.x + before.width / 2, before.y + 25)
  await page.mouse.down()
  await page.mouse.move(before.x + before.width / 2 + 50, before.y + 95, { steps: 10 })
  await page.mouse.up()
  await expect.poll(() => page.evaluate(() => localStorage.getItem('study-graph-layout:graph-test:graph-study'))).toContain('renamed')
  await page.getByText('依赖列表 · 1 条连线').click()
  await page.getByRole('button', { name: '删除连线' }).click()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect.poll(() => saved.edges).toEqual([])
  expect(saved.nodes[1].inputs.value).toBeNull()
  await page.getByRole('button', { name: '＋ 添加计算节点' }).click()
  await expect(page.getByLabel('Logic Asset', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Logic Asset', { exact: true })).toHaveValue('')
  await page.getByLabel('节点类型', { exact: true }).selectOption('aggregate_metric')
  await expect(page.getByLabel('Logic Asset', { exact: true })).toHaveCount(0)
  await expect(page.getByText('对输入计数、求和、求平均或取最大最小值。使用内置计算，无需 Logic Asset。')).toBeVisible()
})
