import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

// THEME-LIGHT-1: the opening theme is the stored explicit choice, else the OS
// preference, else light. The pre-paint boot scripts in spa.html and
// index.html make the same decision before React mounts; a drift would flash
// the wrong theme on load. This runs the REAL script text out of each HTML
// file and storedTheme() over the same matrix and checks both against the rule.

const THEME_KEY = 'network-diagram:theme'
const SCHEMA_KEY = 'network-diagram:theme-schema'

type Stored = 'light' | 'dark' | 'none' | 'garbage' | 'throws'
type Os = 'dark' | 'light' | 'no-matchMedia' | 'matchMedia-throws'

const STORED: Stored[] = ['light', 'dark', 'none', 'garbage', 'throws']
const OS: Os[] = ['dark', 'light', 'no-matchMedia', 'matchMedia-throws']

function expected(stored: Stored, os: Os): 'light' | 'dark' {
  if (stored === 'light' || stored === 'dark') return stored
  return os === 'dark' ? 'dark' : 'light'
}

function arrange(stored: Stored, os: Os) {
  localStorage.clear()
  localStorage.setItem(SCHEMA_KEY, '2')
  if (stored === 'light' || stored === 'dark') localStorage.setItem(THEME_KEY, stored)
  if (stored === 'garbage') localStorage.setItem(THEME_KEY, 'sepia')
  if (stored === 'throws') {
    for (const m of ['getItem', 'setItem', 'removeItem'] as const) {
      vi.spyOn(Storage.prototype, m).mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    }
  }
  if (os === 'no-matchMedia') vi.stubGlobal('matchMedia', undefined)
  else if (os === 'matchMedia-throws') vi.stubGlobal('matchMedia', () => { throw new Error('no media queries') })
  else vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(prefers-color-scheme: dark)' && os === 'dark', media: q }))
}

const here = resolve(__dirname, '../..')
function bootScript(file: string) {
  const html = readFileSync(resolve(here, file), 'utf8')
  const m = html.match(/(;\(function pypsaBootTheme\(\)[\s\S]*?\}\)\(\))\s*<\/script>/)
  if (!m) throw new Error(`pypsaBootTheme script missing from ${file}`)
  return m[1]
}

const matrix = STORED.flatMap(s => OS.map(o => [s, o, expected(s, o)] as const))

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  delete document.documentElement.dataset.theme
})

describe('opening theme: stored choice → OS preference → light', () => {
  it.each(matrix)('uiStore: stored %s, OS %s → %s', async (stored, os, want) => {
    vi.resetModules()
    arrange(stored, os)
    const { useUIStore } = await import('./uiStore')
    expect(useUIStore.getState().theme).toBe(want)
  })

  for (const file of ['spa.html', 'index.html', 'public/login.html']) {
    it.each(matrix)(`${file} boot script: stored %s, OS %s → %s`, (stored, os, want) => {
      arrange(stored, os)
      delete document.documentElement.dataset.theme
      new Function(bootScript(file))()
      expect(document.documentElement.dataset.theme).toBe(want)
    })
  }

  it('keeps an explicit dark choice on a light OS, with no schema reset', async () => {
    vi.resetModules()
    arrange('dark', 'light')
    const { useUIStore } = await import('./uiStore')
    expect(useUIStore.getState().theme).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(localStorage.getItem(SCHEMA_KEY)).toBe('2')
  })

  it('the pre-paint background follows the resolved theme in spa.html', () => {
    // No light flash for a dark user and no dark flash for a light one: the
    // unthemed default paints light, and data-theme="dark" (set by the boot
    // script for every dark outcome) paints dark.
    const html = readFileSync(resolve(here, 'spa.html'), 'utf8')
    expect(html).toMatch(/html \{ background: #ffffff; color-scheme: light; \}/)
    expect(html).toMatch(/html\[data-theme="dark"\] \{ background: #151112; color-scheme: dark; \}/)
    expect(html).not.toMatch(/<html[^>]*data-theme=/)
  })
})
