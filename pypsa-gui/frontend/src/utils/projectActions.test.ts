import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  acquireProjectLock, downloadProjectBundle, forgetBundleLocation,
  formatRelativeTime, nextUntitledName, saveProjectQuietly, slugify, stopLockHeartbeat,
} from './projectActions'
import { projectsApi } from '../api/projects'
import { useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import type { LockInfo } from './lockState'

vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      downloadBundle: vi.fn(),
      acquireLock: vi.fn(),
      heartbeatLock: vi.fn(),
      save: vi.fn(),
    },
  }
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

// ── Lock-loss recovery (Task 7) ─────────────────────────────────────────────
//
// `startLockHeartbeat` is module-private; drive it the same way production
// code does — via `acquireProjectLock`, which starts the heartbeat on a
// successful acquire — then advance the fake clock one tick to fire it.
describe('lock-loss recovery', () => {
  const mine: LockInfo = { holder_email: 'me@example.com', yours: true }
  const theirs: LockInfo = { holder_email: 'other@example.com', yours: false }

  beforeEach(() => {
    vi.mocked(projectsApi.acquireLock).mockReset()
    vi.mocked(projectsApi.heartbeatLock).mockReset()
    vi.mocked(projectsApi.save).mockReset()
    useUIStore.setState({
      readOnly: false, lockReadOnly: false, lockHolderEmail: null, readOnlyReason: 'writable',
    })
    useSimulationStore.setState({ status: 'idle' })
  })

  afterEach(() => {
    stopLockHeartbeat()
    vi.useRealTimers()
  })

  it('heartbeat 409 re-acquires when the lock merely expired', async () => {
    // Fake timers must be active BEFORE `acquireProjectLock` starts the
    // heartbeat `setInterval`, or the interval is scheduled against the
    // real clock and `advanceTimersByTimeAsync` below never fires it.
    vi.useFakeTimers()
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-1')
    expect(useUIStore.getState().readOnly).toBe(false)

    vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({ response: { status: 409 } })
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })

    await vi.advanceTimersByTimeAsync(45_000) // LOCK_HEARTBEAT_MS
    // advanceTimersByTimeAsync flushes microtasks between ticks, but the
    // re-acquire's own `.then` runs one more microtask turn after that.
    await Promise.resolve()
    await Promise.resolve()

    expect(projectsApi.acquireLock).toHaveBeenCalledWith('proj-1')
    expect(projectsApi.acquireLock).toHaveBeenCalledTimes(2) // initial + re-acquire
    expect(useUIStore.getState().readOnly).toBe(false) // did NOT fall read-only
  })

  it('heartbeat 409 falls read-only when re-acquire is refused', async () => {
    vi.useFakeTimers()
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-1')
    expect(useUIStore.getState().readOnly).toBe(false)

    vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({
      response: { status: 409, data: { detail: { lock: theirs } } },
    })
    vi.mocked(projectsApi.acquireLock).mockRejectedValueOnce({ response: { status: 409 } })

    await vi.advanceTimersByTimeAsync(45_000) // LOCK_HEARTBEAT_MS
    await Promise.resolve()
    await Promise.resolve()

    expect(useUIStore.getState().readOnly).toBe(true)
  })

  // Regression (code-review Critical finding on the initial Task 7 patch): the
  // heartbeat 409's re-acquire chain checked `_heartbeatProject === projectId`
  // only ONCE, before starting the async `acquireLock` call — not again after
  // it resolves. If the user switches projects while that re-acquire is still
  // in flight (release outgoing, acquire incoming — done by `switchToProject`
  // via `stopLockHeartbeat` + `acquireProjectLock`), the stale promise's
  // `.then`/`.catch` fires LATER and unconditionally calls `_applyLock` /
  // `stopLockHeartbeat` — clobbering the INCOMING project's just-established
  // writable state with the OUTGOING project's stale outcome.
  it('a stale re-acquire SUCCESS for the outgoing project must not clobber the incoming project state', async () => {
    vi.useFakeTimers()
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-A')
    expect(useUIStore.getState().readOnly).toBe(false)

    vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({ response: { status: 409 } })
    // Controllable re-acquire for A — stays pending until we resolve it below,
    // simulating a slow request that outlives the user's project switch.
    let resolveReacquireA!: (v: { lock: LockInfo | null }) => void
    const reacquireA = new Promise<{ lock: LockInfo | null }>((resolve) => { resolveReacquireA = resolve })
    vi.mocked(projectsApi.acquireLock).mockImplementationOnce(() => reacquireA)

    await vi.advanceTimersByTimeAsync(45_000) // fires the tick: heartbeatLock 409s, catch starts the re-acquire
    await Promise.resolve() // let the 409 handler reach `projectsApi.acquireLock(projectId)`

    // The user switches to project B WHILE A's re-acquire is still pending —
    // exactly what `switchToProject` does: release A (stop its heartbeat),
    // then acquire B (start its own heartbeat).
    stopLockHeartbeat()
    // B's own acquire reports `theirs` as the (co-)holder — deliberately
    // DIFFERENT from A's `mine`, so a clobber is distinguishable from a
    // coincidental match: `_applyLock({ok:true, lock:X})` always sets
    // readOnly=false regardless of X, so readOnly alone can't catch this —
    // holderEmail is the tell.
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: theirs })
    await acquireProjectLock('proj-B')
    expect(useUIStore.getState().readOnly).toBe(false) // B is writable
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')

    // NOW the stale A re-acquire resolves — it must be a no-op.
    resolveReacquireA({ lock: mine })
    await Promise.resolve()
    await Promise.resolve()

    expect(useUIStore.getState().readOnly).toBe(false) // B's state must survive untouched
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com') // NOT clobbered by A's stale resolve
  })

  it('a stale re-acquire REFUSAL for the outgoing project must not kill the incoming project heartbeat', async () => {
    vi.useFakeTimers()
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-A')

    vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({ response: { status: 409 } })
    let rejectReacquireA!: (e: unknown) => void
    const reacquireA = new Promise<{ lock: LockInfo | null }>((_resolve, reject) => { rejectReacquireA = reject })
    vi.mocked(projectsApi.acquireLock).mockImplementationOnce(() => reacquireA)

    await vi.advanceTimersByTimeAsync(45_000)
    await Promise.resolve()

    stopLockHeartbeat()
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-B')
    expect(useUIStore.getState().readOnly).toBe(false)

    // The stale re-acquire for A is refused — must NOT flip B read-only and
    // must NOT stop B's heartbeat.
    rejectReacquireA({ response: { status: 409 } })
    await Promise.resolve()
    await Promise.resolve()
    expect(useUIStore.getState().readOnly).toBe(false)

    // Prove B's heartbeat is still alive: advancing one more tick must still
    // ping heartbeatLock for B. If the stale catch had called
    // `stopLockHeartbeat()`, this interval would be dead and the call below
    // would never happen.
    vi.mocked(projectsApi.heartbeatLock).mockClear()
    vi.mocked(projectsApi.heartbeatLock).mockResolvedValueOnce({ lock: mine })
    await vi.advanceTimersByTimeAsync(45_000)
    expect(projectsApi.heartbeatLock).toHaveBeenCalledWith('proj-B')
  })

  it('a save refused with error_kind "project_locked" applies the lock banner and stops the heartbeat', async () => {
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-1')
    expect(useUIStore.getState().readOnly).toBe(false)

    vi.mocked(projectsApi.save).mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: { error_kind: 'project_locked', message: 'locked', lock: theirs } },
      },
    })

    const ok = await saveProjectQuietly('proj-1')

    expect(ok).toBe(false)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')

    // The heartbeat must be stopped — advancing well past a tick must not
    // produce another heartbeatLock call that could resurrect a writable state.
    vi.useFakeTimers()
    vi.mocked(projectsApi.heartbeatLock).mockClear()
    await vi.advanceTimersByTimeAsync(45_000)
    expect(projectsApi.heartbeatLock).not.toHaveBeenCalled()
  })

  it("applies the banner from the MIDDLEWARE's payload shape too (I1)", async () => {
    // The write middleware refuses /api/network/* and /api/simulation/* under a
    // foreign lock. Its 409 used to carry `detail` as a bare prose string plus a
    // top-level `code`, so `_lockFromErrorDetail` found no `lock` and the banner
    // could not name the holder. It now sends the SAME detail object the route
    // edges do; this pins that the reader accepts it, top-level `code` and all.
    vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: mine })
    await acquireProjectLock('proj-1')

    vi.mocked(projectsApi.save).mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          code: 'project_locked',
          detail: {
            error_kind: 'project_locked',
            message: 'This project is being edited by another user.',
            lock: theirs,
          },
        },
      },
    })

    const ok = await saveProjectQuietly('proj-1')

    expect(ok).toBe(false)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')
  })

  it('a save refused WITHOUT error_kind "project_locked" leaves the lock state untouched', async () => {
    // beforeEach already seeded the writable default state.
    vi.mocked(projectsApi.save).mockRejectedValueOnce(new Error('network blip'))

    const ok = await saveProjectQuietly('proj-1')

    expect(ok).toBe(false)
    expect(useUIStore.getState().readOnly).toBe(false)
  })
})
