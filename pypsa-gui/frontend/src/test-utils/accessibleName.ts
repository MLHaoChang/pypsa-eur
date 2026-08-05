// Runtime accessible-name checks for rendered output.
//
// A static scan cannot answer this question. `<button>{label}</button>` has a
// perfectly good name when `label` is text and none when it is an icon, and
// the source is identical either way — a scan of this frontend produced 62
// findings of which zero were real defects. The accessible name is a property
// of the rendered DOM, so it can only be computed after a render.
//
// This deliberately goes through Testing Library's `name` option rather than
// importing `computeAccessibleName` from `dom-accessibility-api` directly.
// That package is a dependency of @testing-library/dom, not of this app —
// importing it here would mean depending on a transitive package that a lockfile
// refresh could move or hoist elsewhere. The `name` option calls the same
// implementation, so this costs nothing and stays on declared dependencies.
import { within } from '@testing-library/react'

/**
 * Buttons in `container` whose computed accessible name is empty.
 *
 * Only counts buttons exposed to the accessibility tree: Testing Library's
 * role queries skip anything hidden (`display: none`, `aria-hidden`), and a
 * button a screen reader cannot reach does not need a name.
 */
export function unnamedButtons(container: HTMLElement): HTMLElement[] {
  // An exact-match empty string matches only an empty computed name.
  return within(container).queryAllByRole('button', { name: '' })
}

/** Compact `<button class="...">` for a failure message. */
function describeButton(el: HTMLElement): string {
  const cls = el.getAttribute('class')
  const testid = el.getAttribute('data-testid')
  const bits = [testid && `data-testid="${testid}"`, cls && `class="${cls.slice(0, 60)}"`]
  return `<button ${bits.filter(Boolean).join(' ')}>`.replace(/\s+>/, '>')
}

/**
 * Throw unless every visible button in `container` has an accessible name.
 *
 * Call it at the end of a component test that has already rendered the states
 * worth covering — it asserts on whatever is currently mounted, so its reach
 * is exactly the reach of the test it sits in.
 */
export function expectAllButtonsNamed(container: HTMLElement): void {
  const unnamed = unnamedButtons(container)
  if (unnamed.length === 0) return
  const listed = unnamed.map(el => `  - ${describeButton(el)}`).join('\n')
  throw new Error(
    `${unnamed.length} button(s) have no accessible name. A screen reader ` +
      `announces these as just "button".\n${listed}\n\n` +
      'Fix by giving the button visible text, or aria-label if it is icon-only.',
  )
}
