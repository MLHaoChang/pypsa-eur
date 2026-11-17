import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

// THEME-LIGHT-1: the opening theme is the stored explicit choice, else the OS
// preference, else light. The pre-paint boot scripts in spa.html and
// index.html make the same decision before React mounts, so they are run here
// against the same cases; a drift would flash the wrong theme on load.

const THEME_KEY = 'network-diagram:theme'
const SCHEMA_KEY = 'network-diagram:theme-schema'

function mockOsDark(dark: boolean | null) {
  const impl = dark === null
    ? undefined
    : (q: string) => ({ matches: q === '(prefers-color-scheme: dark)' && dark, media: q }) as MediaQueryList
  vi.stubGlobal('matchMedia', impl)
}

async function freshTheme(seed: Record<string, string>, osDark: boolean | null) {
  vi.resetModules()
  localStorage.clear()
  for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v)
  mockOsDark(osDark)
  const mod = await import('./uiStore')
  return mod.useUIStore.getState().theme
}

const here = resolve(__dirname, '../..')
const bootScript = (file: string) => {
  const html = readFileSync(resolve(here, file), 'utf8')
  const m = html.match(/(;\(function pypsaBootTheme\(\)[\s\S]*?\}\)\(\))\s*<\/script>/)
  if (!m) throw new Error(`pypsaBootTheme script missing from ${file}`)
  return m[1]
}

function runBoot(file: string, seed: Record<string, string>, osDark: boolean | null) {
  localStorage.clear()
  for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v)
  mockOsDark(osDark)
  delete document.documentElement.dataset.theme
  new Function(bootScript(file))()
  return document.documentElement.dataset.theme
}

const cases: ReadonlyArray<[string, Record<string, string>, boolean | null, 'light' | 'dark']> = [
  ['stored dark beats a light OS', { [SCHEMA_KEY]: '2', [THEME_KEY]: 'dark' }, false, 'dark'],
  ['stored light beats a dark OS', { [SCHEMA_KEY]: '2', [THEME_KEY]: 'light' }, true, 'light'],
  ['no choice, dark OS → dark', { [SCHEMA_KEY]: '2' }, true, 'dark'],
  ['no choice, light OS → light', { [SCHEMA_KEY]: '2' }, false, 'light'],
  ['no choice, no matchMedia → light', {}, null, 'light'],
  ['a junk stored value is ignored', { [SCHEMA_KEY]: '2', [THEME_KEY]: 'sepia' }, false, 'light'],
]

afterEach(() => {
  vi.unstubAllGlobals()
  delete document.documentElement.dataset.theme
})

describe('opening theme: stored choice → OS preference → light', () => {
  it.each(cases)('uiStore: %s', async (_name, seed, osDark, expected) => {
    expect(await freshTheme(seed, osDark)).toBe(expected)
  })

  it('keeps an explicit choice across the load (no schema reset)', async () => {
    await freshTheme({ [SCHEMA_KEY]: '2', [THEME_KEY]: 'dark' }, false)
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
  })

  for (const file of ['spa.html', 'index.html']) {
    it.each(cases)(`${file} boot script: %s`, (_name, seed, osDark, expected) => {
      expect(runBoot(file, seed, osDark)).toBe(expected)
    })
  }
})
