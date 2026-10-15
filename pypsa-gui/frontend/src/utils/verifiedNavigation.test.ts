import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { acknowledgeMountedView, type NavigationAcknowledgement } from './verifiedNavigation'
import { client } from '../api/client'

vi.mock('../api/client', () => ({ client: { post: vi.fn() } }))
const expected: NavigationAcknowledgement = {
  version: 1, action_id: 'action-1', turn_id: 'turn-1', binding_generation: 7,
  project_id: 'project-1', project_name: 'Demo', input_sha256: 'revision',
  target: { panel_id: 'results', results_tab: 'dispatch' },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
function mount(project = 'Demo', tab = 'dispatch', hidden = false) {
  const panel = document.createElement('div')
  panel.dataset.assurancePanel = 'results'
  panel.dataset.assuranceProject = project
  if (hidden) panel.style.display = 'none'
  const content = document.createElement('div')
  content.dataset.assuranceTab = tab
  panel.append(content)
  document.body.append(panel)
}

describe('mounted view acknowledgements', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(client.post).mockResolvedValue({ data: { outcome: 'completed' } })
  })
  afterEach(() => { document.body.replaceChildren() })

  it('waits for the exact mounted project and tab, then submits once', async () => {
    const verified = vi.fn()
    const stop = acknowledgeMountedView(expected, () => true, verified)
    mount('Other', 'dispatch')
    mount('Demo', 'economics')
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    mount()
    await tick()
    expect(client.post).toHaveBeenCalledTimes(1)
    expect(client.post).toHaveBeenCalledWith('/chat/ui-ack', {
      version: 1, action_id: 'action-1', turn_id: 'turn-1', binding_generation: 7,
      target: { panel_id: 'results', results_tab: 'dispatch' },
    }, { skipErrorToast: true })
    expect(verified).toHaveBeenCalledOnce()
    mount()
    await tick()
    expect(client.post).toHaveBeenCalledTimes(1)
    stop()
  })

  it.each(['superseded', 'hidden', 'aborted'])('does not acknowledge a %s view', async fault => {
    let current = true
    const stop = acknowledgeMountedView(expected, () => current)
    if (fault === 'superseded') current = false
    if (fault === 'aborted') stop()
    mount('Demo', 'dispatch', fault === 'hidden')
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    stop()
  })

  it('a rejected acknowledgement retains submitted status', async () => {
    vi.mocked(client.post).mockRejectedValue(new Error('expired'))
    const verified = vi.fn()
    mount()
    acknowledgeMountedView(expected, () => true, verified)
    await tick()
    expect(verified).not.toHaveBeenCalled()
  })

  it('a late response cannot complete a newer turn', async () => {
    let resolve: (value: unknown) => void = () => {}
    vi.mocked(client.post).mockImplementation(() => new Promise(r => { resolve = r }))
    let current = true
    const verified = vi.fn()
    mount()
    acknowledgeMountedView(expected, () => current, verified)
    current = false
    resolve({ data: { outcome: 'completed' } })
    await tick()
    expect(verified).not.toHaveBeenCalled()
  })

  it('version 2 needs displayed data with an exact source, not merely a mount', async () => {
    const binding = { version: 1 as const, project_id: 'project-1', project_name: 'Demo', scope: 'current' as const,
      data_sha256: 'a'.repeat(64), source: 'lopf' as const, window_from: 1, window_to: 2 }
    const action: NavigationAcknowledgement = { ...expected, version: 2,
      target: { panel_id: 'results', results_tab: 'loadflow' }, source_binding: binding }
    const verified = vi.fn()
    const stop = acknowledgeMountedView(action, () => true, verified)
    mount('Demo', 'loadflow')
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    const data = document.querySelector<HTMLElement>('[data-assurance-tab]')!
    data.dataset.resultTab = 'loadflow'
    data.dataset.resultBinding = JSON.stringify({ ...binding, data_sha256: 'b'.repeat(64) })
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    data.dataset.resultBinding = JSON.stringify(binding)
    await tick()
    expect(client.post).toHaveBeenCalledWith('/chat/ui-ack', {
      version: 2, action_id: 'action-1', turn_id: 'turn-1', binding_generation: 7,
      target: action.target, source_binding: binding,
    }, { skipErrorToast: true })
    expect(verified).toHaveBeenCalledOnce()
    stop()
  })
})
