// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import assert from 'node:assert/strict'
import path from 'node:path'

/** Real app HTTP and native decisions; only the finite model wire is scripted. */
export async function runNarrowCases({ browser, authenticated, config, results, errors, artifacts }) {
  const viewports = [
    { width: 320, height: 850, touch: false },
    { width: 320, height: 600, touch: true },
    { width: 375, height: 850, touch: true },
    { width: 420, height: 600, touch: true },
    { width: 640, height: 850, touch: false },
    { width: 768, height: 600, touch: true },
    { width: 1440, height: 1000, touch: false },
  ]
  let expected = 0
  for (const mode of ['expert', 'guided']) for (const viewport of viewports) {
    const prefix = `${mode}-${viewport.width}x${viewport.height}-${viewport.touch ? 'touch' : 'mouse'}`
    const context = await browser.newContext({ storageState: authenticated,
      viewport: { width: 1440, height: 1000 }, hasTouch: viewport.touch,
      isMobile: viewport.touch, reducedMotion: 'reduce' })
    await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
    await context.addInitScript(({ mode }) => {
      localStorage.setItem('network-diagram:current-project', 'Composer fixture')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:assistant-dock-width', '700')
      localStorage.setItem('network-diagram:assistant-dock-pinned', 'pinned')
    }, { mode })
    const page = await context.newPage()
    page.setDefaultTimeout(7000)
    page.on('pageerror', error => errors.push({ name: prefix, error: error.message }))
    const access = page.getByRole('button', { name: 'Access and approvals', exact: true })
    const checking = page.getByRole('checkbox', { name: 'Check answers', exact: true })
    const saveRule = async value => {
      const field = page.getByRole('combobox', { name: 'Your saved rule', exact: true })
      await field.selectOption(value)
      await page.getByRole('button', { name: 'Save restriction', exact: true }).click()
      await page.getByText('Restriction saved.', { exact: true }).waitFor()
      assert.equal(await field.inputValue(), value)
    }
    const record = async (state, action = async () => {}) => {
      expected++
      const name = `${prefix}-${state}`
      let geometry
      try {
        const interaction = await action()
        geometry = await page.evaluate(() => {
          const rect = element => {
            const box = element.getBoundingClientRect()
            return { left: box.left, right: box.right, width: box.width, top: box.top, bottom: box.bottom }
          }
          const shiftedAncestors = []
          for (let element = document.querySelector('[data-testid="assistant-dock"]').parentElement;
            element; element = element.parentElement) {
            if (element.scrollLeft || element.scrollTop) shiftedAncestors.push({
              tag: element.tagName, class: element.className,
              scrollLeft: element.scrollLeft, scrollTop: element.scrollTop })
          }
          return { viewport: window.innerWidth, height: window.innerHeight, scrollX: window.scrollX,
            shiftedAncestors,
            client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth,
            dock: rect(document.querySelector('[data-testid="assistant-dock"]')),
            companion: document.querySelector('[data-testid="companion-launcher"]')
              ? rect(document.querySelector('[data-testid="companion-launcher"]')) : null,
            shiftedScrollContainers: [...document.querySelectorAll('body *')]
              .filter(element => element.scrollLeft !== 0)
              .map(element => ({ tag: element.tagName, class: element.className,
                scrollLeft: element.scrollLeft, scrollWidth: element.scrollWidth,
                clientWidth: element.clientWidth, ...rect(element) })),
            savedWidth: localStorage.getItem('network-diagram:assistant-dock-width') }
        })
        assert.equal(geometry.scrollX, 0, 'The document must not shift horizontally')
        assert.deepEqual(geometry.shiftedAncestors, [], 'Dock ancestors must not scroll')
        assert(geometry.scroll <= geometry.client, `Page overflow: ${geometry.scroll} > ${geometry.client}`)
        assert.equal(geometry.savedWidth, '700', 'Responsive fit must preserve the requested desktop width')
        if (state.startsWith('dock-') || state === 'native-denial' || state === 'native-choice' || state === 'desktop-restored') {
          assert(geometry.dock.left >= 0 && geometry.dock.right <= geometry.client,
            `Dock controls outside viewport: ${JSON.stringify(geometry.dock)}`)
        }
        if (geometry.companion) {
          assert(geometry.companion.left >= 0 && geometry.companion.right <= geometry.client)
          assert(geometry.companion.top >= 0 && geometry.companion.bottom <= geometry.height)
        }
        results.push({ name, passed: true, geometry, interaction })
        console.log('PASS', name)
      } catch (error) {
        results.push({ name, passed: false, error: error.message, geometry })
        await page.screenshot({ path: path.join(artifacts, `${name}.png`) })
        console.error('FAIL', name, error.message.slice(0, 400))
      }
    }
    try {
      await page.goto(config.base + '/app')
      // Expert opens the canvas; Guided intentionally opens Hub Design.
      // View widgets must not trap viewport dialogs below the already-open
      // assistant/properties overlays. Cancel only; never reset the layout.
      if (mode === 'expert') {
        await page.getByTestId('assistant-dock-collapse').click()
        await page.getByRole('button', { name: 'Blank', exact: true }).click()
        await page.getByRole('button', { name: 'Compose a message', exact: true }).click()
        await page.getByRole('button', { name: 'Reset diagram — clear saved positions and waypoints', exact: true }).click()
        const resetDialog = page.getByRole('dialog', { name: 'Reset diagram?', exact: true })
        await resetDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
        await resetDialog.waitFor({ state: 'detached' })
      }
      await page.getByTestId('assistant-dock-collapse').click()
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await record('workbench-companion-closed')
      await record('header-controls-reachable', async () => {
        const header = page.locator('header')
        const controls = [header.getByRole('button', { name: 'Guided', exact: true }),
          header.getByRole('button', { name: 'Expert', exact: true }),
          header.getByTestId('header-settings'), header.locator('button[title="composer@example.test"]')]
        for (const control of controls) {
          await control.waitFor()
          const box = await control.boundingBox()
          assert(box && box.x >= 0 && box.x + box.width <= viewport.width,
            'Header actions must fit before focus or pointer scrolling')
          await control.click({ trial: true })
        }
        await controls[0].focus()
        await controls[0].press('Tab')
        assert(await controls[1].evaluate(element => element === document.activeElement))
        const reached = []
        for (let i = 0; i < 3; i++) {
          await page.keyboard.press('Tab')
          reached.push(await page.evaluate(() => document.activeElement.getAttribute('data-testid')
            || document.activeElement.getAttribute('title')))
        }
        assert(reached.includes('header-settings') && reached.includes('composer@example.test'),
          'Keyboard traversal must reach Settings and the account')
        await controls[3].click()
        await header.getByText('Signed in', { exact: true }).waitFor()
        const menu = await header.getByText('Signed in', { exact: true }).evaluate(element => {
          const box = element.closest('.absolute').getBoundingClientRect()
          return { left: box.left, right: box.right }
        })
        assert(menu.left >= 0 && menu.right <= viewport.width, 'The account menu must stay reachable')
        await controls[3].click()
        await header.locator('[data-project-name-editor]').focus()
        const rename = header.locator('input[maxlength="80"]')
        await rename.fill('A'.repeat(80))
        const renameBox = await rename.boundingBox()
        assert(renameBox && renameBox.x >= 0 && renameBox.x + renameBox.width <= viewport.width)
        await rename.press('Escape')
        if (mode === 'expert') {
          await header.getByRole('button', { name: 'Hide properties panel', exact: true }).click()
          await header.getByRole('button', { name: 'Show properties panel', exact: true }).click()
          const hide = page.getByTitle('Hide panel', { exact: true })
          await hide.waitFor()
          const properties = await hide.evaluate(element => {
            const box = element.closest('aside').getBoundingClientRect()
            return { left: box.left, right: box.right }
          })
          assert(properties.left >= 0 && properties.right <= viewport.width,
            'The reopened properties panel must fit its workbench')
        }
        const status = page.getByRole('region', { name: 'Project status', exact: true })
        const dimensions = await status.evaluate(element => ({ width: element.clientWidth,
          contentWidth: element.scrollWidth, height: element.clientHeight, contentHeight: element.scrollHeight }))
        assert(dimensions.contentHeight <= dimensions.height, 'Status text must not wrap out of its row')
        if (dimensions.contentWidth > dimensions.width) {
          await status.focus()
          await status.press('ArrowRight')
          await page.waitForFunction(() => document.querySelector('[aria-label="Project status"]').scrollLeft > 0)
        }
      })
      await record('companion-open', async () => {
        await page.getByRole('button', { name: 'Chat here', exact: true }).click()
        await page.getByRole('region', { name: 'In-page chat access', exact: true }).waitFor()
      })
      await record('companion-access-expanded', async () => {
        await access.click()
        await page.getByRole('table', { name: 'Confirmation rules', exact: true }).waitFor()
        await saveRule('read_only')
        await saveRule('standard')
      })
      await record('dock-open', async () => {
        await page.getByRole('button', { name: 'Close chat here', exact: true }).click()
        await page.getByRole('button', { name: 'Compose a message', exact: true }).click()
        await checking.check()
        assert(await checking.isChecked())
      })
      await record('dock-access-expanded', async () => {
        await access.click()
        await page.getByRole('table', { name: 'Confirmation rules', exact: true }).waitFor()
        await checking.uncheck()
        await saveRule('confirm_changes')
      })
      await record('native-denial', async () => {
        await access.click() // Make transcript room using the ordinary disclosure.
        await page.getByTestId('chat-input').fill('NARROW_CONFIRMATION')
        await page.getByTestId('chat-send').click()
        const card = page.getByTestId('chat-confirmation-card')
        await card.waitFor()
        assert.equal(await card.getAttribute('data-tool-name'), 'bulk_update_components')
        const details = card.getByTestId('chat-confirmation-details')
        if (await details.count() && !await details.evaluate(element => element.open)) {
          await details.locator('summary').click()
        }
        assert((await card.textContent()).includes('marginal_cost'))
        for (const id of ['chat-confirm-approve', 'chat-confirm-deny']) {
          const button = page.getByTestId(id)
          await button.scrollIntoViewIfNeeded()
          const box = await button.boundingBox()
          assert(box && box.x >= 0 && box.x + box.width <= viewport.width
            && box.y >= 0 && box.y + box.height <= viewport.height, `${id} must be reachable`)
        }
        await page.getByTestId('assistant-dock-collapse').click()
        const companion = page.getByTestId('companion-confirmation')
        await companion.waitFor()
        assert.equal(await companion.getAttribute('data-tool-name'), 'bulk_update_components')
        // Trial hit tests never decide the card. The actual denial stays below.
        await page.getByTestId('chat-confirm-approve').click({ trial: true })
        await page.getByTestId('chat-confirm-deny').click({ trial: true })
        const companionBox = await companion.boundingBox()
        assert(companionBox && companionBox.x >= 0
          && companionBox.x + companionBox.width <= viewport.width,
          'The same pending decision must fit the companion')
        await page.getByRole('button', { name: 'Compose a message', exact: true }).click()
        await card.waitFor()
        await page.getByTestId('chat-confirm-deny').click()
        await card.waitFor({ state: 'detached' })
        await page.waitForFunction(() => !document.querySelector('[data-testid="chat-input"]')?.disabled)
        const response = await context.request.get(config.base + '/api/network/generators')
        assert(response.ok())
        const generators = await response.json()
        assert.equal(generators.find(item => item.name === 'G1')?.marginal_cost, 10,
          'Native denial must leave the real network value unchanged')
        await access.click()
        await saveRule('standard')
        await access.click()
        return { companionReview: companionBox, decision: 'denied', nativeMarginalCost: 10 }
      })
      await record('native-choice', async () => {
        const waitIdle = async () => page.waitForFunction(() =>
          !document.querySelector('[data-testid="chat-input"]')?.disabled)
        const startedTurn = () => page.waitForEvent('request', { predicate: request =>
          request.url().endsWith('/api/chat/stream') })
        const ask = async () => {
          await page.getByTestId('chat-input').fill('NARROW_CHOICE')
          const finished = startedTurn()
          await page.getByTestId('chat-send').click()
          await finished
          await page.getByTestId('chat-choice-card').waitFor()
          await waitIdle()
        }
        await ask()
        await page.getByTestId('chat-choice-option').filter({ hasText: 'First result' }).click()
        await page.getByTestId('assistant-dock-collapse').click()
        await page.getByTestId('companion-launcher').getByTestId('chat-choice-card').waitFor()
        const first = page.getByTestId('chat-choice-option').filter({ hasText: 'First result' })
        const second = page.getByTestId('chat-choice-option').filter({ hasText: 'Second result' })
        assert.equal(await first.getAttribute('aria-pressed'), 'true', 'The first pick must survive the move')
        await second.click()
        await page.getByRole('button', { name: 'Compose a message', exact: true }).click()
        const back = page.getByRole('button', { name: 'Return to choices', exact: true })
        if (viewport.width < 1280) await back.click()
        assert.equal(await first.getAttribute('aria-pressed'), 'true')
        assert.equal(await second.getAttribute('aria-pressed'), 'true', 'Both picks must survive the return')
        await page.getByRole('button', { name: 'Type another answer', exact: true }).click()
        await page.getByTestId('chat-input').fill('Keep this unsent alternative.')
        if (viewport.width < 1280) await back.click()
        const pickedTurn = startedTurn()
        await page.getByTestId('chat-choice-send').click()
        await pickedTurn
        await page.getByTestId('chat-choice-card').waitFor({ state: 'detached' })
        await waitIdle()
        assert.equal(await page.getByTestId('chat-input').inputValue(), 'Keep this unsent alternative.')
        await ask()
        await page.getByTestId('assistant-dock-collapse').click()
        await page.getByTestId('companion-launcher').getByTestId('chat-choice-card').waitFor()
        await page.getByRole('button', { name: 'Type another answer', exact: true }).click()
        await page.getByTestId('chat-input').fill('A different local result')
        const freeTextTurn = startedTurn()
        await page.getByTestId('chat-send').click()
        await freeTextTurn
        await page.getByTestId('chat-choice-card').waitFor({ state: 'detached' })
        await waitIdle()
        const response = await context.request.get(config.base + '/api/chat/history?limit=200')
        assert(response.ok())
        const history = await response.json()
        const turns = history.turns.slice(-4)
        assert.deepEqual(turns.map(turn => turn.user), ['NARROW_CHOICE', 'First result; Second result',
          'NARROW_CHOICE', 'A different local result'])
        assert(turns.every(turn => !turn.interrupted
          && JSON.stringify(turn.assistant).includes('Local fixture completed; inspect the native outcome.')),
        'All four native turns must complete, not merely append the user message')
        assert.equal(await page.getByTestId('chat-error-banner').count(), 0)
        return { multiSelect: 'First result; Second result', freeText: 'A different local result',
          history: 'native', draftPreserved: true }
      })
      await record('desktop-restored', async () => {
        await page.getByTestId('chat-input').fill('Retain this draft across resizing.')
        await checking.check()
        await page.setViewportSize({ width: 1440, height: 1000 })
        await page.waitForFunction(() => document.querySelector('[data-testid="assistant-dock"]')?.getBoundingClientRect().width === 700)
        assert.equal(await page.getByTestId('chat-input').inputValue(), 'Retain this draft across resizing.')
        assert(await checking.isChecked(), 'The mounted chat must retain the checking selection')
      })
    } finally { await context.close() }
  }
  return expected
}
