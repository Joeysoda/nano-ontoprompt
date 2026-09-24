import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  await page.setContent('<!doctype html><title>playwright-preflight</title><p>ok</p>')
  const title = await page.title()
  if (title !== 'playwright-preflight') {
    throw new Error(`unexpected browser title: ${title}`)
  }
  console.log(`Playwright Chromium preflight passed (${chromium.executablePath()})`)
} finally {
  await browser.close()
}
