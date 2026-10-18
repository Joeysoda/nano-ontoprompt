import { chromium } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const output = fileURLToPath(new URL('../../docs/demo-scenario-studies/', import.meta.url))
const manifest = JSON.parse(await readFile(`${output}/rehearsal.json`, 'utf8'))
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 })
async function note(locator, label) {
  const box = await locator.boundingBox()
  if (!box || box.y < 0 || box.y > 1000) return
  await page.evaluate(({ box, label }) => {
    const element = document.createElement('span'); element.className = 'demo-note'; element.textContent = label
    Object.assign(element.style, { position: 'fixed', top: `${Math.max(0, box.y - 26)}px`, left: `${box.x}px`, font: 'bold 13px sans-serif', background: '#b91c1c', color: 'white', padding: '4px 8px', zIndex: 10000, pointerEvents: 'none' }); document.body.appendChild(element)
  }, { box, label })
}
async function capture(name) {
  await page.screenshot({ path: `${output}/screenshots/${name}.png` })
  await page.locator('.demo-note').evaluateAll(elements => elements.forEach(element => element.remove()))
}
try {
  for (const [index, study] of manifest.studies.entries()) {
    const prefix = index === 0 ? 'a' : 'b'
    await page.goto(study.url)
    await page.getByRole('heading', { name: '研究定义', exact: true }).waitFor()
    await page.waitForTimeout(350)
    await note(page.getByRole('tab', { name: '模型配置' }), '① 模型配置：查看逻辑与连线')
    await note(page.getByLabel('选择研究'), '② 切换研究')
    await capture(`${prefix}-02-model`)
    await page.getByRole('tab', { name: '情景模拟' }).click()
    await page.getByLabel('情景输入输出对比').waitFor()
    await note(page.getByRole('tab', { name: '情景模拟' }), '① 情景模拟')
    await note(page.getByLabel('情景输入输出对比'), '② 输入、输出、相对基线差值')
    await capture(`${prefix}-04-comparison`)
    const candidate = study.cases[1]
    const row = page.getByRole('row').filter({ has: page.getByText(candidate.name, { exact: true }) }).first()
    await row.getByRole('button', { name: '编辑', exact: true }).click()
    await page.getByLabel('编辑 Case 参数').scrollIntoViewIfNeeded()
    await note(page.getByLabel('编辑 Case 参数'), '① 修改候选输入；只改需要变化的参数')
    await note(page.getByRole('button', { name: '保存', exact: true }), '② 保存')
    await note(row.getByRole('button', { name: '运行', exact: true }), '③ 保存后再运行')
    await capture(`${prefix}-06-edit-case`)
    await page.getByRole('button', { name: '取消', exact: true }).click()
    if (index === 1) {
      for (const [caseIndex, screenshotName] of [[1, 'b-03-late-order'], [2, 'b-05-recovered-schedule']]) {
        const record = study.cases[caseIndex]
        await page.goto(record.runs.at(-1).url)
        await page.getByRole('tab', { name: '情景模拟' }).click()
        await page.getByText('节点计算结果 · schedule', { exact: true }).click()
        const result = page.locator('section[aria-live="polite"]')
        await result.evaluate(element => element.scrollIntoView({ block: 'start' }))
        await note(page.getByLabel('工序排程明细'), caseIndex === 1 ? '① 产线1：OP-40 于180分钟完工，晚于150分钟交期' : '① 产线2：OP-40 于120分钟完工，早于150分钟交期')
        await capture(screenshotName)
      }
    }
  }
  console.log('Refreshed real UI screenshots, including exact edit/save/run controls.')
} finally { await browser.close() }
