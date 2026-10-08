import { test, expect, type Page } from '@playwright/test'

async function mount(page: Page, component: string) {
  page.on('pageerror', error => console.log('Harness error:', error.message))
  page.on('console', message => { if (message.type() === 'error') console.log('Harness console:', message.text()) })
  await page.goto('/login')
  await page.addScriptTag({ type: 'module', content: `
    import {mount} from '/src/test/whatifHarness.tsx';
    import Component from '/src/pages/ontologies/detail/tabs/${component}.tsx';
    mount(Component);
  ` })
  await expect(page.locator('#repair-test')).toBeAttached()
}

test('empty object types do not start a disabled query or stay loading', async ({ page }) => {
  let loads = 0
  await page.route('**/api/v2/ontologies/repair/entities', route => route.fulfill({ json: { entities: [] } }))
  await page.route('**/object-query/load', route => { loads++; return route.fulfill({ json: {} }) })
  await mount(page, 'ObjectQueryTab')
  await expect(page.getByText('暂无可查询的对象类型。')).toBeVisible()
  await expect(page.getByText('正在读取对象')).toHaveCount(0)
  expect(loads).toBe(0)
})

test('object dependency failure retries and partial live data cannot paginate', async ({ page }) => {
  let loads = 0
  await page.route('**/api/v2/ontologies/repair/entities', route => route.fulfill({ json: { entities: [{ id: 'T', api_name: 'T' }] } }))
  await page.route('**/object-query/load', route => {
    loads++
    return loads === 1 ? route.fulfill({ status: 503, json: { detail: { code: 'graph_unavailable' } } }) :
      route.fulfill({ json: { objects: [{ object_id: 'one', object_type: 'T', properties: {} }], page: { has_more: true, next_page_token: null }, status: 'ok', completeness: 'partial' } })
  })
  await mount(page, 'ObjectQueryTab')
  await expect(page.getByRole('alert')).toContainText('对象查询失败')
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByText('one', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled()
  await expect(page.getByText('当前结果不能作为完整推理输入', { exact: false })).toBeVisible()
})

test('graph search failure is not reported as an empty result', async ({ page }) => {
  await page.route('**/api/v2/ontologies/repair/graph?*', route => route.fulfill({ json: { nodes: [], edges: [], logic_rules: [], summary: {} } }))
  await page.route('**/api/v2/ontologies/repair/search?*', route => route.fulfill({ status: 503, json: { detail: 'offline' } }))
  await mount(page, 'GraphTabV2')
  await page.locator('#repair-test input').fill('machine')
  await expect(page.getByRole('alert')).toContainText('搜索失败')
  await expect(page.getByText('无匹配结果', { exact: true })).toHaveCount(0)
})

test('changing approved agent evidence invalidates the proposal', async ({ page }) => {
  await page.route('**/api/v2/ontologies/repair/reasoning/runs', route => route.fulfill({ json: [{ run_id: 'r1', status: 'applied' }] }))
  await page.route('**/agent/context-drafts', route => route.fulfill({ json: { run_id: 'agent1', context: { objects: [{ id: 'machine' }], reasoning_run: { id: 'r1' }, conclusions: [{ conclusion: 'CHECK(machine)' }], rules: [] } } }))
  await page.route('**/agent/runs/agent1/context', route => route.fulfill({ json: {} }))
  await page.route('**/agent/runs/agent1/generate', route => route.fulfill({ json: { proposal: { outcome: 'inspect machine', reasoning: 'grounded' } } }))
  await mount(page, 'AgentDecisionTab')
  await page.getByRole('button', { name: '分析问题并选择上下文' }).click()
  await page.getByRole('button', { name: '批准上下文并生成建议' }).click()
  await expect(page.getByRole('button', { name: '确认并保存决策' })).toBeVisible()
  await page.getByRole('checkbox').first().uncheck()
  await expect(page.getByRole('button', { name: '确认并保存决策' })).toHaveCount(0)
})

test('shared client preserves null, envelopes, and forbidden status without logout', async ({ page }) => {
  await page.route('**/api/v2/repair-null', route => route.fulfill({ json: null }))
  await page.route('**/api/v2/repair-envelope', route => route.fulfill({ json: { data: [] } }))
  await page.route('**/api/v2/repair-forbidden', route => route.fulfill({ status: 403, json: { detail: 'Editor role required' } }))
  await page.goto('/login')
  await page.evaluate(() => localStorage.setItem('token', 'retained'))
  await page.addScriptTag({ type: 'module', content: `
    import {apiClientV2} from '/src/api/client.ts';
    const values = [await apiClientV2.get('/repair-null'), await apiClientV2.get('/repair-envelope')];
    try { await apiClientV2.get('/repair-forbidden'); } catch(error) { values.push(error.status); }
    values.push(localStorage.getItem('token'));
    const output = document.createElement('pre'); output.id='client-result'; output.textContent=JSON.stringify(values); document.body.append(output);
  ` })
  await expect(page.locator('#client-result')).toHaveText('[null,[],403,"retained"]')
})
