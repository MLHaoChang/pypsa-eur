// Run progress slice 3: the notice stack (UX 2 GO 3–5, GK B3 N3) and the
// browser-notification opt-in (UX 2 GO 1, 2; spec §3).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { solveQueueApi } from '../../api/solveQueue'
import { useUIStore } from '../../store/uiStore'
import { RunNoticeStack } from './RunNotices'
import { FINISHED_NOTICE_MS, resetRunNotices, useRunNotices, type RunNotice } from './runNotices'
import { BLOCKED, BrowserNotifyPref, NotifyOptIn, PRIVACY_OFF, PRIVACY_ON, useNotifyPermission } from './NotifyOptIn'
import * as actions from './noticeActions'
import * as results from './openResults'

const notice = (id: string, kind: RunNotice['kind'], over: Partial<RunNotice> = {}): RunNotice => ({
  id, kind, text: `${id} ${kind}: Campus A`, project: 'Campus A', jobId: id, runName: 'Campus study',
  reason: kind === 'failed' ? 'The solver found no feasible plan.' : null, lines: [], ...over,
})

function renderStack(ui: ReactNode = <RunNoticeStack />) {
  const qc = new QueryClient()
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  resetRunNotices()
  useUIStore.setState({ uiMode: 'guided', currentProject: 'Campus A', browserNotify: false })
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('RunNoticeStack', () => {
  it('each notice is a status line with a named close control, and nothing takes focus', () => {
    useRunNotices.getState().push(notice('a', 'failed'))
    const before = document.activeElement
    renderStack()
    expect(screen.getByRole('status').textContent).toContain('a failed: Campus A')
    expect(document.activeElement).toBe(before)
    fireEvent.click(screen.getByRole('button', { name: 'Close: a failed: Campus A' }))
    expect(useRunNotices.getState().notices).toEqual([])
  })

  it('shows three and folds the rest behind "(N more) Show all"', () => {
    for (const id of ['a', 'b', 'c', 'd']) useRunNotices.getState().push(notice(id, 'failed'))
    renderStack()
    expect(screen.getAllByRole('status')).toHaveLength(3)
    const more = screen.getByTestId('run-notices-more')
    expect(more.textContent).toContain('▸ (1 more) Show all')
    fireEvent.click(more)
    expect(screen.getAllByRole('status')).toHaveLength(4)
    expect(more.getAttribute('aria-expanded')).toBe('true')
  })

  it('a finished notice goes by itself; failed and interrupted stay', () => {
    vi.useFakeTimers()
    useRunNotices.getState().push(notice('a', 'completed'))
    useRunNotices.getState().push(notice('b', 'failed'))
    useRunNotices.getState().push(notice('c', 'interrupted'))
    act(() => { vi.advanceTimersByTime(FINISHED_NOTICE_MS + 1) })
    expect(useRunNotices.getState().notices.map(n => n.id)).toEqual(['c', 'b'])
  })

  it('Open results lands through openResults and the notice goes', () => {
    const open = vi.spyOn(results, 'openResults').mockImplementation(() => {})
    useRunNotices.getState().push(notice('a', 'completed'))
    renderStack()
    fireEvent.click(screen.getByTestId('run-notice-open'))
    expect(open).toHaveBeenCalledTimes(1)
    expect(useRunNotices.getState().notices).toEqual([])
  })

  it("another project's notice switches to it first", () => {
    vi.spyOn(results, 'openResults').mockImplementation(() => {})
    const set = vi.fn()
    useUIStore.setState({ currentProject: 'Other', setCurrentProject: set })
    useRunNotices.getState().push(notice('a', 'completed'))
    renderStack()
    fireEvent.click(screen.getByTestId('run-notice-open'))
    expect(set).toHaveBeenCalledWith('Campus A')
  })

  it('See why shows the reason in place in Guided, and opens the Solve queue in Expert', () => {
    useRunNotices.getState().push(notice('a', 'failed'))
    const { unmount } = renderStack()
    fireEvent.click(screen.getByTestId('run-notice-why'))
    expect(screen.getByTestId('run-notice-reason').textContent).toContain('The solver found no feasible plan.')
    unmount()

    const why = vi.spyOn(actions, 'openNoticeWhy')
    useUIStore.setState({ uiMode: 'expert' })
    renderStack()
    fireEvent.click(screen.getByTestId('run-notice-why'))
    expect(why).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('run-notice-reason')).toBeNull()
  })

  it('the away summary shows its runs on Show and stays until closed', () => {
    useRunNotices.getState().push(notice('away', 'away', {
      text: '2 runs finished while you were away', lines: ['Campus study finished: A', 'Optimisation failed: B'],
    }))
    renderStack()
    fireEvent.click(screen.getByTestId('run-notice-show'))
    expect(screen.getByTestId('run-notice-lines').textContent).toContain('Optimisation failed: B')
    expect(useRunNotices.getState().notices).toHaveLength(1)
  })
})

describe('Run again', () => {
  it('posts one requeue however often it is clicked, says "Starting…", then goes', async () => {
    let resolve!: () => void
    const requeue = vi.spyOn(solveQueueApi, 'requeue').mockImplementation(
      () => new Promise(r => { resolve = () => r({} as never) }),
    )
    useRunNotices.getState().push(notice('a', 'interrupted'))
    renderStack()
    const btn = screen.getByTestId('run-notice-again')
    expect(btn.textContent).toContain('Run again: Campus study')
    // Both clicks land before React re-renders the button disabled, so only
    // the in-flight guard can stop the second request.
    act(() => { btn.click(); btn.click() })
    fireEvent.click(btn)
    expect(requeue).toHaveBeenCalledTimes(1)
    expect(btn.textContent).toContain('Starting…')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { resolve() })
    expect(useRunNotices.getState().notices).toEqual([])
  })

  it('a locked project is named in plain words and the notice stays', async () => {
    vi.spyOn(solveQueueApi, 'requeue').mockRejectedValue({
      response: { status: 409, data: { detail: { error_kind: 'project_locked', lock: { holder_email: 'ana@example.com' } } } },
    })
    useRunNotices.getState().push(notice('a', 'interrupted'))
    renderStack()
    await act(async () => { fireEvent.click(screen.getByTestId('run-notice-again')) })
    expect(screen.getByTestId('run-notice-error').textContent).toContain("Can't run it again: ana@example.com is editing Campus A.")
    expect(screen.getByTestId('run-notice-again').textContent).toContain('Run again: Campus study')
    expect(useRunNotices.getState().notices).toHaveLength(1)
  })
})

describe('browser notification opt-in', () => {
  afterEach(() => vi.unstubAllGlobals())

  function stubPermission(p: NotificationPermission, answer: NotificationPermission = p) {
    const requestPermission = vi.fn(async () => answer)
    vi.stubGlobal('Notification', { permission: p, requestPermission })
    useNotifyPermission.setState({ permission: p })
    return requestPermission
  }

  it('asks only from a click, then the link goes and the setting is on', async () => {
    const ask = stubPermission('default', 'granted')
    render(<><NotifyOptIn /><BrowserNotifyPref /></>)
    expect(ask).not.toHaveBeenCalled()
    expect(screen.getByTestId('run-notify-optin').textContent).toContain(PRIVACY_OFF)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Notify me when it finishes' })) })
    expect(ask).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('run-notify-optin')).toBeNull()
    expect(useUIStore.getState().browserNotify).toBe(true)
    expect(screen.getByTestId('browser-notify-note').textContent).toContain(PRIVACY_ON)
  })

  it('after a denial: no link, the toggle disabled and the block named as visible text', async () => {
    stubPermission('default', 'denied')
    render(<><NotifyOptIn /><BrowserNotifyPref /></>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Notify me when it finishes' })) })
    expect(screen.queryByTestId('run-notify-optin')).toBeNull()
    expect((screen.getByRole('button', { name: /Browser notifications/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('browser-notify-note').textContent).toContain(BLOCKED)
    expect(useUIStore.getState().browserNotify).toBe(false)
  })

  it('the footer toggle switches it off and on again without asking twice', () => {
    const ask = stubPermission('granted')
    useUIStore.setState({ browserNotify: true })
    render(<BrowserNotifyPref />)
    const toggle = screen.getByRole('button', { name: /Browser notifications/ })
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(toggle)
    expect(useUIStore.getState().browserNotify).toBe(false)
    expect(screen.getByTestId('browser-notify-note').textContent).toContain(PRIVACY_OFF)
    fireEvent.click(toggle)
    expect(useUIStore.getState().browserNotify).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })

  it('renders nothing where the browser has no notifications', () => {
    useNotifyPermission.setState({ permission: null })
    const { container } = render(<><NotifyOptIn /><BrowserNotifyPref /></>)
    expect(container.innerHTML).toBe('')
  })
})
