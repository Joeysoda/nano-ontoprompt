import { test, expect, type Page } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173'

async function loginAs(page: Page, username = 'admin', password = 'admin123') {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', username)
  await page.fill('input[placeholder="密码"]', password)
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Authentication', () => {
  test.beforeEach(async ({ request }) => {
    const response = await request.get('/api/v1/auth/profile')
    test.skip(response.status() === 200, 'JWT login assertions do not apply to local_single_user mode')
    expect(response.status()).toBe(403)
  })
  test('login page renders', async ({ page }) => {
    await page.goto(`${BASE}/login`)
    await expect(page.locator('h1')).toContainText('OntoPrompt')
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('redirects unauthenticated users to login', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await expect(page).toHaveURL(/\/login/)
  })

  test('login with valid credentials', async ({ page }) => {
    await loginAs(page)
    await expect(page).toHaveURL(`${BASE}/overview`)
  })

  test('login with wrong password shows error', async ({ page }) => {
    await page.goto(`${BASE}/login`)
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'wrongpassword')
    const response = page.waitForResponse(response => response.url().endsWith('/api/v1/auth/login'))
    await page.click('button[type="submit"]')
    expect((await response).status()).toBe(401)
    await expect(page.getByRole('alert')).toHaveText('用户名或密码错误')
    await expect(page.getByPlaceholder('用户名')).toHaveValue('admin')
    await expect(page.getByPlaceholder('密码')).toHaveValue('wrongpassword')
    await expect(page).toHaveURL(`${BASE}/login`)
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull()
    // Retry in the same form after a genuine rejection; it must not reload.
    await page.getByPlaceholder('密码').fill('admin123')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(`${BASE}/overview`)
  })

  for (const failure of ['network', 'rate-limit', 'server', 'non-json-server'] as const) {
    test(`login ${failure} failure is distinct from invalid credentials`, async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', error => errors.push(error.message))
      await page.route('**/api/v1/auth/login', route => {
        if (failure === 'network') return route.abort('connectionfailed')
        if (failure === 'non-json-server') return route.fulfill({ status: 502, contentType: 'text/plain', body: 'Bad Gateway' })
        return route.fulfill({ status: failure === 'rate-limit' ? 429 : 503,
          contentType: 'application/json', body: JSON.stringify({ detail: failure }) })
      })
      await page.goto(`${BASE}/login`)
      await page.getByPlaceholder('用户名').fill('admin')
      await page.getByPlaceholder('密码').fill('admin123')
      await page.click('button[type="submit"]')
      const expected = failure === 'network' ? '网络连接失败，请确认后端服务已启动'
        : failure === 'rate-limit' ? '登录尝试过于频繁，请稍后重试'
        : '登录服务暂时不可用，请稍后重试'
      await expect(page.getByRole('alert')).toHaveText(expected)
      await expect(page).toHaveURL(`${BASE}/login`)
      await expect(page.getByPlaceholder('用户名')).toHaveValue('admin')
      await expect(page.locator('button[type="submit"]')).toBeEnabled()
      expect(errors).toEqual([])
    })
  }

  test('register page accessible', async ({ page }) => {
    await page.goto(`${BASE}/register`)
    await expect(page.locator('h1')).toContainText('注册')
  })

  test('logout redirects to login', async ({ page }) => {
    await loginAs(page)
    await page.click('button:has-text("退出")')
    await expect(page).toHaveURL(/\/login/)
  })

})
