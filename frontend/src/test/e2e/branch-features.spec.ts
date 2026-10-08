import { test as base, expect, type Page, type BrowserContext } from '@playwright/test'

const test = base.extend<object, { authState: Awaited<ReturnType<BrowserContext['storageState']>> }>({
  authState: [async ({ browser }, provideState) => {
    const page = await browser.newPage({ baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173' })
    await login(page)
    const state = await page.context().storageState()
    await page.close()
    await provideState(state)
  }, { scope: 'worker' }],
  storageState: async ({ authState }, provideState) => { await provideState(authState) },
})
const pageErrors = new WeakMap<Page, string[]>()
const diagnostics = new WeakMap<Page, string[]>()

const oid = 'frepple-d7eb98078882b234c395fd05'
const root = `/api/v2/ontologies/${oid}`
const detail = `/ontologies/${oid}`

async function login(page: Page) {
  await page.goto('/login')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/overview$/)
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  pageErrors.set(page, errors)
  const logs: string[] = []
  page.on('console', message => { if (message.type() === 'error') logs.push(`console: ${message.text()}`) })
  page.on('requestfailed', request => logs.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`))
  page.on('response', response => { if (response.status() >= 400) logs.push(`HTTP ${response.status()}: ${response.url()}`) })
  diagnostics.set(page, logs)
})

test.afterEach(async ({ page }, info) => {
  await info.attach('browser-diagnostics', {
    body: JSON.stringify({ pageErrors: pageErrors.get(page), logs: diagnostics.get(page) }, null, 2),
    contentType: 'application/json',
  })
  expect(pageErrors.get(page)).toEqual([])
})

test('New ontology: both feature tabs show explicit empty states', async ({ page }) => {
  await page.goto(`${detail}?tab=logic-assets`)
  const headers = { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` }
  const response = await page.request.post('/api/v1/ontologies', { headers, data: { name: `Branch acceptance empty ${Date.now()}`, domain: '制造' } })
  expect(response.status()).toBe(201)
  const emptyId = (await response.json()).data.id as string
  await page.goto(`/ontologies/${emptyId}?tab=logic-assets`)
  await expect(page.getByText('尚未注册逻辑资产', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '业务数据', exact: true }).click()
  await expect(page.getByText('当前本体没有 frePPLe 业务数据导入记录', { exact: false })).toBeVisible()
})

test('Logic Assets: real tab entry, execution, invalid input and persisted runs', async ({ page }, info) => {
  await page.goto(`${detail}?tab=logic`)
  await page.getByRole('button', { name: '打开逻辑绑定', exact: true }).click()
  await expect(page).toHaveURL(/tab=logic-assets/)
  await expect(page.getByRole('heading', { name: '本地逻辑绑定' })).toBeVisible()
  await page.getByRole('button', { name: '初始化六类逻辑' }).click()
  await page.getByRole('button', { name: /周期时间计算/ }).click()
  await page.getByRole('button', { name: '执行本地逻辑' }).click()
  await expect(page.getByText('结构化输出', { exact: true })).toBeVisible()
  await expect(page.locator('pre').last()).toContainText('"total_seconds": 300')
  const headers = { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` }
  const saved = await page.request.get(`${root}/logic-assets/runs`, { headers })
  expect(saved.status()).toBe(200)
  const runs = (await saved.json()).runs as { id: string }[]
  expect(runs.length).toBeGreaterThan(0)
  await page.locator('textarea').fill('{}')
  await page.getByRole('button', { name: '执行本地逻辑' }).click()
  await expect(page.locator('pre')).toContainText('contract validation failed')
  await expect(page.getByText('结构化输出', { exact: true })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: /周期时间计算/ })).toBeVisible()
  const restored = await page.request.get(`${root}/logic-assets/runs`, { headers })
  expect((await restored.json()).runs.some((run: { id: string }) => run.id === runs[0].id)).toBe(true)
  await page.goBack()
  await expect(page).toHaveURL(/tab=logic$/)
  await expect(page.getByRole('button', { name: '打开逻辑绑定' })).toBeVisible()
  await page.goForward()
  await expect(page.getByRole('heading', { name: '本地逻辑绑定' })).toBeVisible()
  await page.screenshot({ path: info.outputPath('logic-assets.png'), fullPage: true })
})

test('Manufacturing Data: real API fixture, filtering, empty result, evidence and reload', async ({ page }, info) => {
  await page.goto(`${detail}?tab=logic-assets`)
  const response = page.waitForResponse(response => response.url().endsWith(`${root}/manufacturing-data`))
  await page.getByRole('button', { name: '业务数据', exact: true }).click()
  const report = await (await response).json()
  expect(report.sha256).toBe('d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef')
  expect(report.nodes).toHaveLength(363)
  await expect(page.getByTestId('manufacturing-counts')).toContainText('关系 903')
  await page.getByLabel('搜索业务对象').fill('no-such-demand')
  await expect(page.getByText('匹配 0 个对象', { exact: false })).toBeVisible()
  await page.getByLabel('搜索业务对象').fill('Demand 01')
  await page.getByRole('button', { name: 'Demand 01 · 待处理', exact: true }).click()
  await expect(page.getByRole('heading', { name: '来源证据', exact: true })).toBeVisible()
  await page.getByText(/JSON index .*查看原始记录/).click()
  await expect(page.locator('pre')).toContainText('input.demand')
  await expect(page.getByRole('heading', { name: '结构依赖上下文' })).toBeVisible()
  await page.screenshot({ path: info.outputPath('manufacturing-data.png'), fullPage: true })
  await page.getByLabel('对象类型').selectOption('Operation')
  await page.getByLabel('搜索业务对象').fill('Assemble chair')
  await page.getByRole('button', { name: 'Assemble chair', exact: true }).first().click()
  await expect(page.getByText('单位加工时间（秒/产品单位）', { exact: true })).toBeVisible()
  const headers = { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` }
  expect((await page.request.get(`${root}/manufacturing-data?object_id=foreign`, { headers })).status()).toBe(404)
  await page.reload()
  await expect(page.getByTestId('manufacturing-counts')).toContainText('业务对象 363')
})

for (const feature of [
  { tab: 'logic-assets', endpoint: 'logic-assets', loading: '正在加载逻辑资产…' },
  { tab: 'manufacturing', endpoint: 'manufacturing-data', loading: '正在加载业务数据…' },
]) {
  for (const status of [401, 403, 404, 422, 500]) {
    test(`${feature.tab}: HTTP ${status} has a stable error state`, async ({ page }) => {
      await page.route(`**${root}/${feature.endpoint}`, route => route.fulfill({
        status, contentType: 'application/json', body: JSON.stringify({ detail: `acceptance-${status}` }),
      }))
      await page.goto(`${detail}?tab=${feature.tab}`)
      if (status === 401) {
        await expect(page).toHaveURL(/\/login$/)
        await expect(page.getByPlaceholder('用户名')).toBeVisible()
        expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull()
        const persisted = await page.evaluate(() => JSON.parse(localStorage.getItem('auth-store') ?? '{}'))
        expect(persisted.state).toMatchObject({ token: null, user: null })
        await page.goto(`${detail}?tab=${feature.tab}`)
        await expect(page).toHaveURL(/\/login$/)
      } else {
        await expect(page.getByRole('alert')).toContainText(`acceptance-${status}`, { timeout: 15000 })
        expect(await page.evaluate(() => localStorage.getItem('token'))).toBeTruthy()
        await page.unroute(`**${root}/${feature.endpoint}`)
        await page.getByRole('button', { name: '重新加载' }).click()
        await expect(page.getByRole('alert')).toHaveCount(0)
      }
    })
  }
  test(`${feature.tab}: missing-credentials 403 clears the entire session`, async ({ page }) => {
    await page.route(`**${root}/${feature.endpoint}`, route => route.fulfill({
      status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Not authenticated' }),
    }))
    await page.goto(`${detail}?tab=${feature.tab}`)
    await expect(page).toHaveURL(/\/login$/)
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull()
    const persisted = await page.evaluate(() => JSON.parse(localStorage.getItem('auth-store') ?? '{}'))
    expect(persisted.state).toMatchObject({ token: null, user: null })
  })
  test(`${feature.tab}: slow response shows loading`, async ({ page }) => {
    let release: () => void = () => {}
    const gate = new Promise<void>(resolve => { release = resolve })
    await page.route(`**${root}/${feature.endpoint}`, async route => {
      await gate
      await route.continue()
    })
    await page.goto(`${detail}?tab=${feature.tab}`)
    await expect(page.getByRole('status')).toContainText(feature.loading)
    release()
    await expect(page.getByText(feature.loading, { exact: true })).toHaveCount(0)
  })
}

for (const status of [403, 404, 422, 500]) {
  test(`Ontology detail entry: HTTP ${status} is recoverable without losing the session`, async ({ page }) => {
    await page.route(`**/api/v1/ontologies/${oid}`, route => route.fulfill({
      status, contentType: 'application/json', body: JSON.stringify({ detail: `entry-${status}` }),
    }))
    await page.goto(`${detail}?tab=logic-assets`)
    await expect(page.getByRole('alert')).toContainText(status === 404 ? '未找到本体或无访问权限' : '本体加载失败')
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeTruthy()
    await page.unroute(`**/api/v1/ontologies/${oid}`)
    await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await expect(page.getByRole('heading', { name: '本地逻辑绑定' })).toBeVisible()
  })
}

test('Logic Assets: repeated execute clicks create one run while pending', async ({ page }) => {
  await page.goto(`${detail}?tab=logic-assets`)
  await page.getByRole('button', { name: /周期时间计算/ }).click()
  let count = 0
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**${root}/logic-assets/*/run`, async route => {
    count += 1
    await gate
    await route.continue()
  })
  await page.getByRole('button', { name: '执行本地逻辑' }).dblclick()
  await expect(page.getByRole('button', { name: '运行中…' })).toBeDisabled()
  release()
  await expect(page.getByText('结构化输出', { exact: true })).toBeVisible()
  expect(count).toBe(1)
})
