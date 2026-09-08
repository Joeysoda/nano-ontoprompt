const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({executablePath: '/usr/bin/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 1440, height: 1080}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const origin = process.env.TEST_ORIGIN || 'http://127.0.0.1:5173';
  const oid = 'joey-bts-54f03b63ed1ebdb9e23a653d';
  const url = `${origin}/ontologies/${oid}?tab=reasoning`;
  try {
    await page.goto(url);
    await expect(page.getByLabel('Facts', {exact: true})).toHaveValue(/STREAM_OF/, {timeout: 30000});
    await expect(page.getByLabel('Rules', {exact: true})).toHaveValue(/MEASURES_EQUIPMENT/);
    await expect(page.getByTestId('graph-counts')).toContainText('184 个实例');
    await page.getByRole('button', {name: '加载当前图事实与规则', exact: true}).click();
    await expect(page.getByRole('status')).toContainText('337 条原始图事实');
    await page.getByRole('button', {name: '运行推理', exact: true}).click();
    await expect(page.getByTestId('run-status')).toHaveText('预览（未写图）', {timeout: 30000});
    await expect(page.getByText('派生事实 110', {exact: true})).toBeVisible();
    await expect(page.getByTestId('graph-counts')).toContainText('预览 110 条');
    await page.getByLabel('搜索派生事实').fill('MEASURES_EQUIPMENT');
    await page.getByRole('button', {name: /MEASURES_EQUIPMENT.*查看证据/}).first().click();
    await expect(page.getByTestId('proof-panel')).toContainText('data/bts_demo/observations.csv');
    await expect(page.getByTestId('proof-panel')).toContainText('data/bts_demo/Site_B.ttl');
    await expect(page.getByTestId('proof-panel')).toContainText('点击追溯上一步');
    await page.getByRole('button', {name: '保存结果', exact: true}).click();
    await expect(page.getByTestId('run-status')).toHaveText('已保存并投影');
    await expect(page.getByTestId('graph-counts')).toContainText('已保存 110 条');
    const saved = await page.getByLabel('历史运行').inputValue();
    await page.reload();
    await expect(page.getByTestId('run-status')).toHaveText('已保存并投影', {timeout: 30000});
    await expect(page.getByLabel('历史运行')).toHaveValue(saved);
    await expect(page.getByTestId('graph-counts')).toContainText('已保存 110 条');
    await page.getByLabel('仅派生关系').check();
    await expect(page.locator('[data-testid="instance-graph"] canvas').first()).toBeVisible();
    await page.getByTestId('instance-graph').scrollIntoViewIfNeeded();
    await page.screenshot({path: 'test-results/bts-reasoning-graph.png', fullPage: true});
    await page.getByLabel('搜索派生事实').fill('MEASURES_EQUIPMENT');
    await page.getByRole('button', {name: /MEASURES_EQUIPMENT.*查看证据/}).first().click();
    await page.getByTestId('proof-panel').scrollIntoViewIfNeeded();
    await page.screenshot({path: 'test-results/bts-reasoning-evidence.png', fullPage: true});
    await page.goto(`${origin}/ontologies/${oid}?tab=graph`);
    await expect(page.getByText('尚未发布实体类型与关系', {exact: true})).toHaveCount(0);
    await expect(page.locator('canvas').first()).toBeVisible();
    if(errors.length) throw new Error(errors.join('\n'));
    console.log(JSON.stringify({passed: true, run_id: saved, checks: ['load Joey facts and rules', '110 conclusions', 'CSV + TTL evidence', 'save', 'refresh restores run', '184 instances and 110 derived edges', 'schema graph nonempty', 'no browser JS exceptions']}));
  } catch(e) {
    await page.screenshot({path: 'test-results/bts-reasoning-failure.png', fullPage: true});
    throw e;
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
