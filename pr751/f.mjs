import { open, OUT } from './lib.mjs'
const tag = process.argv[2]
const dict = "{'error_kind': 'invalid_edit_value', 'message': \"Storage 'Battery': Storage energy part is priced but has no lifetime\"}"
const msg = "Storage 'Battery': Storage energy part is priced but has no lifetime"
for (const mode of ['guided', 'expert']) {
  const { b, page } = await open(mode)
  await page.keyboard.press('Control+j')
  await page.waitForTimeout(800)
  const content = `✗ preview_project_changes — invalid_edit_value: ${tag === 'before' ? dict : msg}`
  await page.evaluate(async c => { const m = await import('/src/store/chatStore.ts'); m.useChatStore.getState().appendMessage({ role: 'tool', content: c, tool_use_id: 'tu1', tool_name: 'preview_project_changes' }) }, content)
  await page.waitForTimeout(800)
  const el = page.getByText(/priced but has no lifetime|error_kind|Could not/).first()
  await el.scrollIntoViewIfNeeded().catch(() => {})
  const r = await el.boundingBox().catch(() => null)
  console.log(mode, JSON.stringify(r))
  await page.screenshot({ path: `${OUT}/${mode}-f-toolerror-${tag}.png`, clip: r ? { x: Math.max(0, r.x - 20), y: Math.max(0, r.y - 40), width: 440, height: 110 } : undefined })
  await b.close()
}
