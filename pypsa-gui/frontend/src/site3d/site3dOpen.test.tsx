// SC-07 S5b R6: "Opened" only on the browser ACK; pending before it; unconfirmed otherwise.
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { acknowledgeMountedView, type NavigationAcknowledgement } from '../utils/verifiedNavigation'
import { client } from '../api/client'
import { isSite3dNavigate, Site3dOpenLine, SITE3D_OPEN_WORDS } from './site3dOpen'

vi.mock('../api/client', () => ({ client: { post: vi.fn() } }))
const site: NavigationAcknowledgement = {
  version: 1, action_id: 'a1', turn_id: 't1', binding_generation: 2, project_id: 'p1', project_name: 'Demo',
  input_sha256: 'rev', target: { panel_id: 'map', canvas_view: 'site' },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
function mount(canvas: string | null, project = 'Demo') {
  const el = document.createElement('div')
  el.dataset.assurancePanel = 'map'
  if (canvas) el.dataset.assuranceCanvas = canvas
  el.dataset.assuranceProject = project
  document.body.append(el)
}

describe('Site 3D ACK (R6)', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(client.post).mockResolvedValue({ data: { outcome: 'completed' } }) })
  afterEach(() => { document.body.replaceChildren(); vi.useRealTimers() })

  it('completes only on the Site 3D marker of the same project', async () => {
    const verified = vi.fn(), unverified = vi.fn()
    acknowledgeMountedView(site, () => true, verified, unverified)
    mount(null)            // the map without the site view
    mount('site', 'Other') // another project
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    mount('site')
    await tick(); await tick()
    expect(client.post).toHaveBeenCalledTimes(1)
    expect(verified).toHaveBeenCalledTimes(1)
    expect(unverified).not.toHaveBeenCalled()
  })

  it('a panel target never completes on the Site 3D marker', async () => {
    const verified = vi.fn()
    acknowledgeMountedView({ ...site, target: { panel_id: 'map' } }, () => true, verified)
    mount('site')
    await tick()
    expect(client.post).not.toHaveBeenCalled()
  })

  it('says unconfirmed when the window ends, the turn moves on, or the server refuses', async () => {
    vi.useFakeTimers()
    const timedOut = vi.fn()
    acknowledgeMountedView(site, () => true, undefined, timedOut)
    vi.advanceTimersByTime(5000)
    expect(timedOut).toHaveBeenCalledTimes(1)
    vi.useRealTimers()

    const moved = vi.fn()
    acknowledgeMountedView(site, () => false, undefined, moved)
    expect(moved).toHaveBeenCalledTimes(1)

    vi.mocked(client.post).mockRejectedValueOnce(new Error('409'))
    const refused = vi.fn(), verified = vi.fn()
    mount('site')
    acknowledgeMountedView(site, () => true, verified, refused)
    await tick(); await tick()
    expect(refused).toHaveBeenCalledTimes(1)
    expect(verified).not.toHaveBeenCalled()
  })

  it('the stop the caller holds is not an unconfirmed answer', () => {
    const unverified = vi.fn()
    acknowledgeMountedView(site, () => true, undefined, unverified)()
    expect(unverified).not.toHaveBeenCalled()
  })

  it('names the frames that open Site 3D', () => {
    expect(isSite3dNavigate({ kind: 'navigate', panel_id: 'map', canvas_view: 'site' })).toBe(true)
    expect(isSite3dNavigate({ kind: 'navigate', panel_id: 'MapCanvas', canvas_view: 'site' })).toBe(true)
    expect(isSite3dNavigate({ kind: 'navigate', panel_id: 'map', canvas_view: 'satellite' })).toBe(false)
    expect(isSite3dNavigate({ kind: 'navigate', panel_id: 'results' })).toBe(false)
  })

  it.each(['pending', 'opened', 'unconfirmed'] as const)('the %s line is a status in words', state => {
    render(<Site3dOpenLine state={state} />)
    const line = screen.getByRole('status')
    expect(line.textContent).toBe(SITE3D_OPEN_WORDS[state])
    expect(line.textContent).not.toMatch(/[—;]|\.\.\./)
  })

  it('shows nothing without a state', () => {
    const { container } = render(<Site3dOpenLine state={undefined} />)
    expect(container.textContent).toBe('')
  })
})
