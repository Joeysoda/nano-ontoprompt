const { chromium, expect } = require('@playwright/test');
(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage();
    await page.goto('http://127.0.0.1:5173/ontologies/e69354c8-6db7-4da1-b551-33b41f4c0315?tab=agent');
    await page.getByRole('button', { name: '分析问题并选择上下文' }).click();
    await expect(page.getByRole('heading', { name: '这些材料为什么值得看？' })).toBeVisible();
    await expect(page.getByText('如果同一条观测记录', { exact: false })).toBeVisible();
    await expect(page.getByText('为什么相关：', { exact: false })).toHaveCount(0);
    await expect(page.getByText('下面是规则在人话中的意思', { exact: false })).toHaveCount(0);
    const visible = await page.locator('body').innerText();
    expect(visible).not.toContain('_instance_id');
    expect(visible).not.toContain('ctx_process_phase');
    expect(visible).toContain('重新定位：调整加工位置');
    expect(visible).toContain('第 2 层向下加工');
    expect(visible).toContain('第 3 层向上加工');
    expect(visible).toContain('不能支持“已发现刀具磨损”');
    await page.locator('input[type="checkbox"]').first().uncheck();
    await expect(page.getByText('本次选出的 2 条记录', { exact: false })).toBeVisible();
    await page.getByText('查看技术详情', { exact: true }).first().click();
    expect(await page.locator('body').innerText()).toContain('_instance_id');
    console.log('PASS: real FactoryNet readable rule, three phase meanings, hidden identifiers, live selection summary, expandable audit details; no decision saved');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
