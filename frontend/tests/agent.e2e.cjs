const { chromium, expect } = require('@playwright/test');

(async () => {
  const ontology = 'e69354c8-6db7-4da1-b551-33b41f4c0315';
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:5173/ontologies/${ontology}?tab=agent`);
    await expect(page.getByRole('heading', { name: /Agent 决策助手/ })).toBeVisible();
    await page.getByRole('button', { name: /分析问题并选择上下文/ }).click();
    await expect(page.getByRole('heading', { name: /Agent 上下文草案/ })).toBeVisible();
    const runSelect = page.locator('select[aria-label="上下文推理运行"]');
    await expect(runSelect.locator('option')).not.toHaveCount(1);
    await expect(page.locator('input[type="checkbox"]')).not.toHaveCount(0);
    const ruleBox = await page.getByRole('heading', {name: '命中规则', exact: true}).boundingBox();
    const runBox = await page.getByRole('heading', {name: '推理运行', exact: true}).boundingBox();
    expect(ruleBox.y).toBeLessThan(runBox.y);
    await expect(page.getByText('你的问题提到了', {exact: false})).toHaveCount(0);
    await expect(page.getByText('第 2 层向下加工阶段（Layer 2 Down）有一条记录，其刀具状态标为“未磨损（unworn）”。', {exact: true})).toBeVisible();
    await expect(page.getByText('重新定位阶段（Repositioning）有一条记录，其刀具状态标为“未磨损（unworn）”。', {exact: true})).toBeVisible();
    await page.getByRole('button', { name: /批准上下文并生成建议/ }).click();
    await expect(page.getByRole('heading', { name: /决策建议（待确认）/ })).toBeVisible();
    await expect(page.getByText(/依据解释/)).toBeVisible();
    await expect(page.getByText('建议放行进入下一批生产', {exact: false})).toBeVisible();
    await expect(page.getByText('决策规则核对', {exact: true})).toBeVisible();
    await expect(page.getByText('下一批生产放行', {exact: false}).first()).toBeVisible();
    console.log(JSON.stringify({ ontology, passed: true, checks: ['agent tab loads', 'real reasoning run selected', 'proposal generated', 'decision rule matched and displayed'] }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
