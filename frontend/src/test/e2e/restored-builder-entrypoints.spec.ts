import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route(url => new URL(url).pathname.startsWith('/api/v'), route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data: [] }),
  }))
})

test('ontology creation route opens the ontology builder', async ({ page }) => {
  await page.goto('/ontologies/new')

  await expect(page.getByRole('heading', { name: '新建本体' })).toBeVisible()
  await expect(page.getByText('简易 LLM 提取')).toBeVisible()
  await expect(page.getByText('数据管道映射')).toBeVisible()
})

test('data workspace shows only the three requested tools', async ({ page }) => {
  await page.goto('/data')

  const cards = page.locator('main .wb-choice-card')
  await expect(cards).toHaveCount(3)
  for (const title of ['时序数据构建', '多模态数据构建', '动态数据构建']) {
    await expect(page.getByRole('button', { name: new RegExp(title) })).toBeVisible()
  }
  await expect(page.getByText('常规数据导入', { exact: true })).toHaveCount(0)
  await expect(page.getByText('结构化数据集', { exact: true })).toHaveCount(0)
  await expect(page.getByText('数据管道与映射', { exact: true })).toHaveCount(0)
})

test('temporal entry starts at the raw-data construction wizard', async ({ page }) => {
  await page.goto('/data/temporal')

  await expect(page.getByRole('heading', { name: '时序数据构建' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入本地时序文件' })).toBeVisible()
})

test('multimodal entry starts at the raw-data construction wizard', async ({ page }) => {
  await page.goto('/data/multimodal')

  await expect(page.getByRole('heading', { name: '多模态数据构建' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入 ZIP' })).toBeVisible()
})

test('dynamic entry opens the event-stream demo', async ({ page }) => {
  await page.goto('/data/dynamic?new=1')

  await expect(page.getByRole('heading', { name: 'FactoryNet 实时演示' })).toBeVisible()
  await expect(page.getByRole('button', { name: '创建演示' })).toBeVisible()
})
