import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  downloadProjectBundle, forgetBundleLocation,
  formatRelativeTime, nextUntitledName, slugify,
} from './projectActions'
import { projectsApi } from '../api/projects'
import { useSimulationStore } from '../store/simulationStore'

vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, downloadBundle: vi.fn() } }
})

// The RETURN VALUE is this helper's contract, and its three callers branch on
// it: `AppHeader` and `Sidebar` render "— updated saved file" for 'reused' and
// "(server only)" for 'cancelled'. Pinning it from a component test cannot
// work — `OverviewPanel` treats every non-'cancelled' result identically, so a
// helper returning 'reused' from the anchor fallback passed all 183 tests
// while making the other two callers claim the user's chosen file was updated
// when the bytes went to the Downloads folder.
describe('downloadProjectBundle — the result each caller branches on', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.downloadBundle).mockReset()
    vi.mocked(projectsApi.downloadBundle).mockResolvedValue(new Blob(['zip']))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    useSimulationStore.setState({ logLines: [] })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    forgetBundleLocation('Demo')
    delete (window as unknown as Record<string, unknown>).showSaveFilePicker
  })

  it("reports 'download' when the browser has no picker at all", async () => {
    // Firefox, Safari, and the pywebview WKWebView shell.
    expect(await downloadProjectBundle('Demo')).toBe('download')
  })

  it("reports 'cancelled' when the user dismisses the picker", async () => {
    ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('abort'), { name: 'AbortError' }))

    expect(await downloadProjectBundle('Demo')).toBe('cancelled')
  })

  it("reports 'download', NOT 'reused', when the picker cannot be shown", async () => {
    // `SecurityError` — transient user activation expired during the fetch.
    // 'reused' here would make AppHeader and Sidebar tell the user their
    // chosen file was updated.
    ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('gesture'), { name: 'SecurityError' }))

    expect(await downloadProjectBundle('Demo')).toBe('download')
  })

  it('leaves a record when it silently changes the destination', async () => {
    // The fallback puts the file somewhere other than where the user would
    // have chosen. The toast only says "— downloaded"; the Log tab is where
    // that becomes diagnosable.
    ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('gesture'), { name: 'SecurityError' }))

    await downloadProjectBundle('Demo')

    expect(
      useSimulationStore.getState().logLines.some(l => l.includes('Save dialog unavailable')),
    ).toBe(true)
  })

  it('lets a failed WRITE reach the caller instead of downloading a second copy', async () => {
    ;(window as unknown as Record<string, unknown>).showSaveFilePicker = vi
      .fn()
      .mockResolvedValue({
        createWritable: async () => ({
          write: async () => {
            throw Object.assign(new Error('full'), { name: 'QuotaExceededError' })
          },
          close: async () => {},
        }),
      })

    await expect(downloadProjectBundle('Demo')).rejects.toThrow('full')
  })
})

describe('slugify', () => {
  it('lowercases and collapses runs of non-alphanumerics to a single _', () => {
    expect(slugify('My Project')).toBe('my_project')
    expect(slugify('Wind Farm #1')).toBe('wind_farm_1')
    expect(slugify('(draft) v2')).toBe('draft_v2')
  })

  it('trims underscores from BOTH ends', () => {
    // Regression: the trim was /^_|_$/ with no `g` flag, so the alternation
    // replaced only the first match and a title with junk at both ends kept
    // its trailing underscore.
    expect(slugify('!My Project!')).toBe('my_project')
    expect(slugify('  spaced  ')).toBe('spaced')
    expect(slugify('___a___')).toBe('a')
  })

  it('falls back to "network" when nothing survives', () => {
    expect(slugify('!!!')).toBe('network')
    expect(slugify('')).toBe('network')
    expect(slugify('   ')).toBe('network')
  })

  it('is idempotent — re-slugifying a slug is a no-op', () => {
    for (const s of ['My Project', '!My Project!', 'Wind Farm #1', '  spaced  ']) {
      expect(slugify(slugify(s))).toBe(slugify(s))
    }
  })
})

describe('nextUntitledName', () => {
  it('uses the bare name when it is free', () => {
    expect(nextUntitledName([])).toBe('Untitled')
    expect(nextUntitledName(['Something Else'])).toBe('Untitled')
  })

  it('starts numbering at 2 and skips every taken name', () => {
    expect(nextUntitledName(['Untitled'])).toBe('Untitled 2')
    expect(nextUntitledName(['Untitled', 'Untitled 2'])).toBe('Untitled 3')
    expect(nextUntitledName(['Untitled', 'Untitled 2', 'Untitled 4'])).toBe('Untitled 3')
  })

  it('never returns a name already in the list', () => {
    const taken = ['Untitled', ...Array.from({ length: 50 }, (_, i) => `Untitled ${i + 2}`)]
    expect(taken).not.toContain(nextUntitledName(taken))
  })
})

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-07-25T12:00:00Z')
  const ago = (ms: number) => new Date(now - ms).toISOString()

  it('returns null for missing or unparseable input', () => {
    expect(formatRelativeTime(null)).toBeNull()
    expect(formatRelativeTime(undefined)).toBeNull()
    expect(formatRelativeTime('')).toBeNull()
    expect(formatRelativeTime('not a date')).toBeNull()
  })

  it('crosses each unit boundary at the right place', () => {
    expect(formatRelativeTime(ago(2_000), now)).toBe('just now')
    expect(formatRelativeTime(ago(30_000), now)).toBe('30s ago')
    expect(formatRelativeTime(ago(5 * 60_000), now)).toBe('5m ago')
    expect(formatRelativeTime(ago(3 * 3_600_000), now)).toBe('3h ago')
    expect(formatRelativeTime(ago(3 * 86_400_000), now)).toBe('3d ago')
  })

  it('switches to an absolute ISO-style date beyond a week', () => {
    // Deliberately not toLocaleDateString: Chrome follows the OS regional
    // setting, which is the same locale quirk documented for
    // <input type="datetime-local"> in CLAUDE.md.
    const out = formatRelativeTime(ago(30 * 86_400_000), now)
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('clamps future timestamps to "just now" instead of going negative', () => {
    const future = new Date(now + 60_000).toISOString()
    expect(formatRelativeTime(future, now)).toBe('just now')
  })
})
