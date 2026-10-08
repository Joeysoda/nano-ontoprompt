const { chromium, expect } = require('@playwright/test');
(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage();
    await page.goto('http://127.0.0.1:5173/ontologies/e69354c8-6db7-4da1-b551-33b41f4c0315?tab=logic');
    await expect(page.getByText('下一批生产放行', { exact: true })).toBeVisible();
    await expect(page.getByText('刀具异常安排点检', { exact: true })).toBeVisible();
    await expect(page.getByText('加工风险暂停设备', { exact: true })).toBeVisible();
    console.log('PASS: all three published decision policies are visible in the ontology Logic tab');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
