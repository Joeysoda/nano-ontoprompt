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

test('data workspace exposes import, mapping, construction, and demo entries', async ({ page }) => {
  await page.goto('/data')

  for (const title of [
    '常规数据导入',
    '时序数据构建',
    '动态数据构建',
    '多模态数据构建',
    '数据管道与映射',
  ]) {
    await expect(page.getByRole('button', { name: new RegExp(title) })).toBeVisible()
  }
})

test('regular data route opens the import and mapping wizard', async ({ page }) => {
  await page.goto('/data/regular')

  await expect(page.getByRole('heading', { name: '常规数据构建' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入文件' })).toBeVisible()
  await expect(page.getByText('本体映射')).toBeAttached()
})
