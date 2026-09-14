const {chromium, expect} = require('@playwright/test');
(async()=>{
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage();
    for (const oid of ['e69354c8-6db7-4da1-b551-33b41f4c0315','joey-bts-54f03b63ed1ebdb9e23a653d']) {
      await page.goto(`http://127.0.0.1:5173/ontologies/${oid}?tab=decisions`);
      await expect(page.getByRole('heading',{name:'决策链分析',exact:true})).toBeVisible();
      await expect(page.getByLabel('起点决策').locator('option').filter({hasText:'测试：检查加工记录'})).toHaveCount(1);
      await page.getByLabel('起点决策').selectOption({label:'测试：检查加工记录'});
      await page.getByLabel('目标决策').selectOption({label:'测试：调整检查计划'});
      for (const operation of ['chain','influenced','precedents','loops','score','network','distance']) {
        await page.getByLabel('分析类型').selectOption(operation);
        await page.getByRole('button',{name:'运行分析',exact:true}).click();
        await expect(page.getByTestId('decision-analysis-result')).toBeVisible();
        await expect(page.getByRole('alert')).toHaveCount(0);
      }
      await page.getByText('查看推理依据及证据',{exact:true}).click();
      await expect(page.locator('details').filter({has:page.getByText('查看推理依据及证据',{exact:true})})).toContainText('run_id');
      await page.reload();
      await expect(page.getByLabel('起点决策').locator('option').filter({hasText:'测试：检查加工记录'})).toHaveCount(1);
      const title=`浏览器测试决策 ${Date.now()}`;
      await page.getByLabel('决策内容',{exact:true}).fill(title);
      await page.getByLabel('决策理由',{exact:true}).fill('界面创建验证，不代表实际生产决定');
      await expect(page.getByLabel('推理运行ID').locator('option')).not.toHaveCount(1);
      await page.getByLabel('推理运行ID').selectOption({index:1});
      await expect(page.getByLabel('依据结论').locator('option')).not.toHaveCount(1);
      await page.getByLabel('依据结论').selectOption({index:1});
      await page.getByRole('button',{name:'保存决策',exact:true}).click();
      await expect(page.getByLabel('起点决策').locator('option').filter({hasText:title})).toHaveCount(1);
      await page.getByText('查看推理依据及证据',{exact:true}).click();
      await expect(page.locator('details').filter({has:page.getByText('查看推理依据及证据',{exact:true})})).toContainText('run_id');
      await expect(page.getByTestId('decision-graph').locator('canvas').first()).toBeVisible();
      await page.reload();
      await expect(page.getByLabel('起点决策').locator('option').filter({hasText:title})).toHaveCount(1);
      console.log(JSON.stringify({ontology:oid,passed:true,checks:['analysis controls','seven analyzer operations','reasoning basis','reload persistence']}));
    }
    await page.goto('http://127.0.0.1:5173/ontologies/e69354c8-6db7-4da1-b551-33b41f4c0315?tab=decisions');
    await expect(page.getByLabel('起点决策').locator('option').filter({hasText:'合成验证决策 001'})).toHaveCount(1);
    await page.getByLabel('起点决策').selectOption({label:'合成验证决策 001'});
    await page.getByLabel('目标决策').selectOption({label:'合成验证决策 010'});
    await page.getByLabel('分析类型').selectOption('distance');
    await page.getByRole('button',{name:'运行分析',exact:true}).click();
    await expect(page.getByTestId('highlight-status')).toContainText('已高亮影响链');
    await expect(page.getByTestId('decision-graph').locator('canvas').first()).toBeVisible();
    console.log(JSON.stringify({ontology:'e69354c8-6db7-4da1-b551-33b41f4c0315',passed:true,checks:['100 synthetic decisions','distance path','visible path highlight']}));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
