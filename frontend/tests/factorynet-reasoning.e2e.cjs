const { chromium, expect } = require('@playwright/test');
(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox','--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const oid = 'e69354c8-6db7-4da1-b551-33b41f4c0315';
  await page.goto(`http://127.0.0.1:5173/ontologies/${oid}?tab=reasoning`);
  await page.getByRole('button',{name:'加载当前图事实与规则',exact:true}).click();
  await expect(page.getByLabel('Facts',{exact:true})).toHaveValue(/IN_PHASE\(/,{timeout:30000});
  await page.getByLabel('Rules',{exact:true}).fill('IF IN_PHASE(?observation, ?phase) AND HAS_TOOL_CONDITION(?observation, ?condition) THEN PHASE_TOOL_STATE(?phase, ?condition)');
  await page.getByRole('button',{name:'运行推理',exact:true}).click();
  await expect(page.getByText('派生事实 3',{exact:true})).toBeVisible({timeout:30000});
  await expect(page.getByTestId('graph-counts')).toContainText('预览 3 条');
  await page.getByRole('button',{name:'保存结果',exact:true}).click();
  await expect(page.getByTestId('run-status')).toHaveText('已保存并投影',{timeout:30000});
  await expect(page.getByTestId('graph-counts')).toContainText('已保存 3 条');
  console.log(JSON.stringify({passed:true,ontology_id:oid,derived:3,rule:'PHASE_TOOL_STATE',source:'FactoryNet cnc_000.parquet'}));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
