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
        await page.evaluate(() => window.scrollTo(0, 0))
        geometry = await page.evaluate(() => {
          const rect = element => {
            const box = element.getBoundingClientRect()
            return { left: box.left, right: box.right, width: box.width, top: box.top, bottom: box.bottom }
          }
          return { viewport: window.innerWidth, height: window.innerHeight,
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
        assert(geometry.scroll <= geometry.client, `Page overflow: ${geometry.scroll} > ${geometry.client}`)
        assert.equal(geometry.savedWidth, '700', 'Responsive fit must preserve the requested desktop width')
        if (state.startsWith('dock-') || state === 'native-denial' || state === 'desktop-restored') {
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
      await page.getByTestId('assistant-dock-collapse').click()
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await record('workbench-companion-closed')
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
