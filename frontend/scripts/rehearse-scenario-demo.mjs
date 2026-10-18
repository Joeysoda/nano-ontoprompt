import { chromium } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'

const base = process.env.DEMO_BASE_URL || 'http://127.0.0.1:25173'
const ontology = '480856e2-9375-40c4-b504-e3395600f4c5'
const output = fileURLToPath(new URL('../../docs/demo-scenario-studies/', import.meta.url))
await mkdir(`${output}/screenshots`, { recursive: true })
await mkdir(`${output}/inputs`, { recursive: true })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 })
page.setDefaultTimeout(15000)
const report = { date: '2026-10-12', base_url: base, ontology_id: ontology, backend: 'real local PostgreSQL/FalkorDB/Redis/Celery; no request mocking', studies: [], errors: [] }
page.on('pageerror', error => report.errors.push(error.message))
const root = `/ontologies/${ontology}/scenario-studies`
const apiRoot = `/api/v2/ontologies/${ontology}`
const checkpoint = () => writeFile(`${output}/rehearsal.json`, JSON.stringify(report, null, 2))
const log = message => console.log(message)
async function screenshot(name, labels = []) {
  for (const [locator, caption] of labels) {
    const box = await locator.boundingBox()
    if (box && box.y >= 0 && box.y < 1000) await page.evaluate(({ box, caption }) => {
      const note = document.createElement('div'); note.className = 'demo-annotation'
      note.textContent = caption
      Object.assign(note.style, { position: 'fixed', left: `${Math.min(box.x, innerWidth - 260)}px`, top: `${Math.max(0, box.y - 28)}px`, background: '#b91c1c', color: 'white', padding: '4px 8px', borderRadius: '3px', font: 'bold 13px sans-serif', zIndex: '10000', pointerEvents: 'none' })
      document.body.appendChild(note)
    }, { box, caption })
  }
  await page.screenshot({ path: `${output}/screenshots/${name}.png` })
  await page.locator('.demo-annotation').evaluateAll(elements => elements.forEach(element => element.remove()))
}
async function readApi(path) {
  const response = await page.request.get(`${base}${apiRoot}${path}`)
  if (!response.ok()) throw new Error(`GET ${path}: ${response.status()} ${await response.text()}`)
  return response.json()
}
async function closeNotice() {
  const button = page.getByRole('status').getByRole('button', { name: '关闭' })
  if (await button.count()) await button.click()
}
async function createStudy(template, name, customize, prefix) {
  const existing = (await readApi('/scenario-study-definitions')).items.find(item => item.name === name)
  if (existing?.latest_version) {
    const detail = await readApi(`/scenario-study-definitions/${existing.id}`)
    let published = detail.versions.find(version => version.version === existing.latest_version)
    const catalog = await readApi('/object-query/catalog')
    if (published.ontology_metadata_digest !== catalog.metadata_digest) {
      // Preserve the failed rehearsal's immutable version. A bounded demo
      // budget makes this a new specification rather than rewriting history.
      await page.goto(`${base}${root}?study=${existing.id}`)
      await page.getByText('高级 JSON 定义', { exact: true }).click()
      const replacement = structuredClone(published.spec)
      replacement.budgets.max_wall_seconds = 600
      await page.getByLabel('Study definition JSON').fill(JSON.stringify(replacement, null, 2))
      await page.getByText('高级 JSON 定义', { exact: true }).click()
      await page.getByRole('button', { name: '验证定义' }).click()
      await page.getByRole('status').filter({ hasText: '定义有效' }).waitFor()
      const publicationPromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(':publish'))
      await page.getByRole('button', { name: '发布版本' }).click()
      const publication = await publicationPromise
      assert.equal(publication.status(), 201, await publication.text())
      published = await publication.json()
      assert.equal(published.ontology_metadata_digest, catalog.metadata_digest)
      report.repaired_legacy_demo_version = { study_id: existing.id, previous_version: existing.latest_version, new_version: published.version, reason: 'Semantic revision digest was previously compared with a distinct Object Query catalog digest. Old immutable version retained.' }
    }
    const study = { id: existing.id, name, version: published.version, url: `${base}${root}?study=${existing.id}&version=${published.version}`, spec: published.spec, cases: [] }
    report.studies.push(study)
    await writeFile(`${output}/inputs/${prefix}-study-spec.json`, JSON.stringify(published.spec, null, 2))
    await page.goto(study.url)
    await page.getByRole('heading', { name: '研究定义', exact: true }).waitFor()
    await page.getByRole('tab', { name: '情景模拟' }).click()
    await checkpoint()
    log(`Reusing demo study ${name}: ${existing.id}`)
    return study
  }
  await page.goto(`${base}${root}`)
  await page.getByText('研究库与模板 · 选择研究', { exact: true }).click()
  await page.getByLabel('制造业模板').selectOption(template)
  await page.getByRole('button', { name: '载入模板并注册计算逻辑' }).click()
  await page.getByRole('status').filter({ hasText: '已载入' }).waitFor()
  await page.getByLabel('新研究名称').fill(name)
  await page.getByLabel('新研究名称').scrollIntoViewIfNeeded()
  await screenshot(`${prefix}-01-template`, [[page.getByLabel('制造业模板'), '① 选择模板'], [page.getByLabel('新研究名称'), '② 研究名称'], [page.getByRole('button', { name: '＋ 创建自定义研究' }), '③ 创建研究']])
  const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/scenario-study-definitions'))
  await page.getByRole('button', { name: '＋ 创建自定义研究' }).click()
  const response = await responsePromise
  assert.equal(response.status(), 201, await response.text())
  const created = await response.json()
  await page.getByRole('heading', { name: '研究定义', exact: true }).waitFor()
  const library = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: '研究库与模板' }) }).first()
  if (await library.evaluate(element => element.open)) await library.locator('summary').first().click()
  await page.getByText('高级 JSON 定义', { exact: true }).click()
  const editor = page.getByLabel('Study definition JSON')
  const spec = JSON.parse(await editor.inputValue())
  customize(spec)
  await editor.fill(JSON.stringify(spec, null, 2))
  await writeFile(`${output}/inputs/${prefix}-study-spec.json`, JSON.stringify(spec, null, 2))
  await page.getByText('高级 JSON 定义', { exact: true }).click()
  await page.getByRole('button', { name: '验证定义', exact: true }).click()
  await page.getByRole('status').filter({ hasText: '定义有效' }).waitFor()
  await page.getByRole('button', { name: '验证定义' }).scrollIntoViewIfNeeded()
  await screenshot(`${prefix}-02-model`, [[page.getByRole('button', { name: '验证定义' }), '① 验证计算图'], [page.getByRole('button', { name: '发布版本' }), '② 发布固定版本']])
  const publicationPromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(':publish'))
  await page.getByRole('button', { name: '发布版本' }).click()
  const publication = await publicationPromise
  assert.equal(publication.status(), 201, await publication.text())
  const version = await publication.json()
  await page.waitForFunction(() => [...document.querySelectorAll('[role="tab"]')].some(tab => tab.textContent === '情景模拟' && tab.getAttribute('aria-selected') === 'true'))
  const study = { id: created.id, name, version: version.version, url: `${base}${root}?study=${created.id}&version=${version.version}`, spec, cases: [] }
  report.studies.push(study)
  await checkpoint()
  await closeNotice()
  log(`Created and published ${name}: ${created.id} v${version.version}`)
  return study
}
async function makeCase(study, name, kind, overrides, copyFrom) {
  if (copyFrom) {
    const row = page.getByRole('row').filter({ has: page.getByText(copyFrom.name, { exact: true }) }).first()
    await row.getByRole('button', { name: '复制为候选' }).click()
  }
  await page.getByLabel('Case 名称', { exact: true }).fill(name)
  await page.getByLabel('方案类型').selectOption(kind)
  for (const parameter of study.spec.parameters) {
    if (!Object.hasOwn(overrides, parameter.key)) continue
    const input = page.getByLabel(`覆盖 ${parameter.label || parameter.key}`, { exact: true })
    await input.fill(typeof overrides[parameter.key] === 'object' ? JSON.stringify(overrides[parameter.key], null, 2) : String(overrides[parameter.key]))
    await input.press('Tab')
  }
  const existing = (await readApi(`/scenario-study-definitions/${study.id}/cases?study_version=${study.version}`)).items.find(item => item.name === name)
  if (existing) {
    assert.equal(existing.case_kind, kind)
    for (const [key, value] of Object.entries(overrides)) assert.deepEqual(existing.parameter_overrides[key], value)
    const record = { id: existing.id, name, kind, scenario_id: existing.scenario_id, overrides: existing.parameter_overrides, runs: [] }
    study.cases.push(record); await checkpoint(); log(`Reusing demo case ${name}: ${existing.id}`)
    return record
  }
  const scenarioPromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/scenarios:from-live'))
  await page.getByRole('button', { name: '＋ 从当前主本体创建情景' }).click()
  const scenarioResponse = await scenarioPromise
  assert.equal(scenarioResponse.status(), 201, await scenarioResponse.text())
  const scenario = await scenarioResponse.json()
  await page.waitForFunction(id => document.querySelector('[aria-label="隔离情景"]')?.value === id, scenario.id)
  const casePromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/scenario-study-definitions/${study.id}/cases`))
  await page.getByRole('button', { name: '创建 Case', exact: true }).click()
  const caseResponse = await casePromise
  assert.equal(caseResponse.status(), 201, await caseResponse.text())
  const created = await caseResponse.json()
  const record = { id: created.id, name, kind, scenario_id: scenario.id, overrides: created.parameter_overrides, runs: [] }
  study.cases.push(record)
  await checkpoint()
  await closeNotice()
  log(`Created ${name}: ${created.id}`)
  return record
}
async function runCase(study, record, expected) {
  const row = page.getByRole('row').filter({ has: page.getByText(record.name, { exact: true }) }).first()
  const runPromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/cases/${record.id}/runs`))
  const start = Date.now()
  await row.getByRole('button', { name: '运行', exact: true }).click()
  const response = await runPromise
  assert.equal(response.status(), 202, await response.text())
  const queued = await response.json()
  let result
  for (let attempt = 0; attempt < 100; attempt++) {
    result = await readApi(`/scenario-study-runs/${queued.id}`)
    if (['completed', 'failed', 'cancelled'].includes(result.status)) break
    await page.waitForTimeout(500)
  }
  assert.equal(result.status, 'completed', JSON.stringify(result.error))
  const metrics = Object.fromEntries(result.result_manifest.metrics.map(metric => [metric.key, metric.value]))
  for (const [key, value] of Object.entries(expected)) assert.ok(Math.abs(metrics[key] - value) < 1e-8, `${key}: expected ${value}, got ${metrics[key]}`)
  assert.ok(result.result_manifest.provenance.run_plan_id)
  assert.ok(result.result_manifest.provenance.data_view_id)
  record.runs.push({ id: queued.id, status: result.status, elapsed_ms: Date.now() - start, url: `${study.url}&case=${record.id}&run=${queued.id}`, metrics, result_manifest: result.result_manifest, stages: result.stages })
  await page.getByRole('heading', { name: 'completed · 100%', exact: true }).waitFor({ timeout: 10000 })
  await closeNotice()
  await checkpoint()
  log(`Completed ${record.name}: ${JSON.stringify(metrics)} in ${record.runs.at(-1).elapsed_ms}ms`)
  return result
}

try {
  const cycle = await createStudy('cycle_time', '导师演示｜批量与周期', spec => {
    spec.nodes[0].outputs.seconds = 'total_seconds'
    spec.metrics.unshift({ key: 'cycle_seconds', label: '批次周期（秒）', type: 'decimal', direction: 'minimize', unit: 's', source: 'cycle.seconds' })
  }, 'a')
  const cycleBaseline = await makeCase(cycle, 'A 基线10件', 'baseline', {})
  await runCase(cycle, cycleBaseline, { cycle_seconds: 50, cycle_minutes: 50 / 60 })
  const cycleCandidate = await makeCase(cycle, 'A 批量20件', 'candidate', { quantity: 20 }, cycleBaseline)
  await page.getByLabel('覆盖 批量', { exact: true }).scrollIntoViewIfNeeded()
  await screenshot('a-03-inputs', [[page.getByLabel('覆盖 批量', { exact: true }), '① 批量改为20'], [page.getByRole('button', { name: '＋ 从当前主本体创建情景' }), '② 每个方案使用独立情景']])
  await runCase(cycle, cycleCandidate, { cycle_seconds: 70, cycle_minutes: 70 / 60 })
  await page.getByLabel('情景输入输出对比').scrollIntoViewIfNeeded()
  await screenshot('a-04-comparison', [[page.getByLabel('情景输入输出对比'), '① 看两个方案的输入、周期和差值']])
  await page.getByText('运行步骤与来源记录', { exact: true }).click()
  await page.getByText('运行步骤与来源记录', { exact: true }).scrollIntoViewIfNeeded()
  await screenshot('a-05-provenance', [[page.getByText('运行步骤与来源记录', { exact: true }), '① 展开：每次运行保留来源记录']])

  const tasks = [
    { id: 'OP-10', duration: 45, due_at: 50, due_date: '2026-11-01', resource: 'line-1' },
    { id: 'OP-20', duration: 75, due_at: 130, due_date: '2026-11-01', resource: 'line-1' },
    { id: 'OP-30', duration: 60, due_at: 65, due_date: '2026-11-01', resource: 'line-2' },
  ]
  const capacity = await createStudy('capacity_replan', '导师演示｜订单与产能', spec => {
    spec.parameters.find(parameter => parameter.key === 'tasks').default = tasks
    spec.parameters.find(parameter => parameter.key === 'capacity').label = '排程观察窗口'
    spec.nodes.find(node => node.key === 'schedule').outputs.schedule = 'schedule'
    spec.metrics.find(metric => metric.key === 'makespan').label = '整体完工时间'
    spec.constraints.push({ key: 'orders_on_time', expression_ref: 'late_count.count', severity: 'hard', config: { operator: 'lte', value: 0 } })
  }, 'b')
  const capacityBaseline = await makeCase(capacity, 'B 基线排程', 'baseline', {})
  await runCase(capacity, capacityBaseline, { late_orders: 0, makespan: 120 })
  const urgentTasks = [...tasks, { id: 'OP-40', duration: 60, due_at: 150, due_date: '2026-11-01', resource: 'line-1' }]
  await writeFile(`${output}/inputs/b-urgent-tasks.json`, JSON.stringify(urgentTasks, null, 2))
  const urgent = await makeCase(capacity, 'B 新增紧急订单', 'candidate', { tasks: urgentTasks }, capacityBaseline)
  await runCase(capacity, urgent, { late_orders: 1, makespan: 180 })
  const urgentRun = urgent.runs.at(-1)
  assert.deepEqual(urgentRun.result_manifest.nodes.schedule.outputs.late_tasks, ['OP-40'])
  assert.equal(urgentRun.result_manifest.constraints.find(item => item.key === 'orders_on_time').satisfied, false)
  assert.equal(urgentRun.result_manifest.constraints.find(item => item.key === 'capacity_feasible').satisfied, true)
  await page.getByText('节点计算结果 · schedule', { exact: true }).click()
  await page.getByLabel('工序排程明细').scrollIntoViewIfNeeded()
  await screenshot('b-03-late-order', [[page.getByLabel('工序排程明细'), '① OP-40 在产线1排队，180分钟完工'], [page.getByText('OP-40', { exact: true }).last(), '② 逾期工序：OP-40']])
  const recoveredTasks = urgentTasks.map(task => task.id === 'OP-40' ? { ...task, resource: 'line-2' } : task)
  await writeFile(`${output}/inputs/b-recovered-tasks.json`, JSON.stringify(recoveredTasks, null, 2))
  const recovery = await makeCase(capacity, 'B 转到产线2', 'candidate', { tasks: recoveredTasks }, urgent)
  await runCase(capacity, recovery, { late_orders: 0, makespan: 120 })
  assert.equal(recovery.runs.at(-1).result_manifest.constraints.find(item => item.key === 'orders_on_time').satisfied, true)
  await page.getByLabel('情景输入输出对比').scrollIntoViewIfNeeded()
  await screenshot('b-04-comparison', [[page.getByLabel('情景输入输出对比'), '① 比较：0 → 1 → 0个逾期工序']])
  await page.getByText('节点计算结果 · schedule', { exact: true }).click()
  await page.getByLabel('工序排程明细').scrollIntoViewIfNeeded()
  await screenshot('b-05-recovered-schedule', [[page.getByLabel('工序排程明细'), '① OP-40 改到产线2，120分钟完工']])
  await writeFile(`${output}/inputs/a-double-batch-overrides.json`, JSON.stringify({ quantity: 20 }, null, 2))
  await writeFile(`${output}/inputs/b-urgent-overrides.json`, JSON.stringify({ tasks: urgentTasks }, null, 2))
  await writeFile(`${output}/inputs/b-recovered-overrides.json`, JSON.stringify({ tasks: recoveredTasks }, null, 2))
  assert.equal(report.errors.length, 0, report.errors.join('\n'))
  report.acceptance = 'PASS: all five real runs completed; expected metrics, late-task identity, constraints and provenance verified; no browser page errors'
  await checkpoint()
  log(report.acceptance)
} catch (error) {
  report.failure = String(error.stack || error)
  await checkpoint()
  await screenshot('failure').catch(() => {})
  throw error
} finally {
  await browser.close()
}
