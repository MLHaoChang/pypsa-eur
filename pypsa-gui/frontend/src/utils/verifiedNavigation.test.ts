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

  it('acknowledges a highlighted branch only once its row is shown and marked', async () => {
    const action = { ...expected, target: { panel_id: 'results', highlight: { component: 'Line', name: 'L1' } } }
    const stop = acknowledgeMountedView(action, () => true)
    mount()
    const row = document.createElement('tr')
    row.dataset.branchRow = ''
    row.dataset.branchClass = 'Transformer'
    row.dataset.branchName = 'L1'
    row.setAttribute('aria-current', 'true')
    document.querySelector('[data-assurance-panel]')!.append(row)
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    row.dataset.branchClass = 'Line'
    row.removeAttribute('aria-current')
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    row.setAttribute('aria-current', 'true')
    await tick()
    expect(client.post).toHaveBeenCalledOnce()
    stop()
  })

  // LC-03 LC-T09/T10: a past run's ACK certifies one exact rendered source, nothing near it.
  describe('a past run on screen (LC-03)', () => {
    const SHA = 'a'.repeat(64)
    const past = { version: 1 as const, project_id: 'project-1', project_name: 'Demo', scope: 'historical' as const,
      data_sha256: SHA, run_id: 'run-1', section: 'results_dispatch', window_from: 0, window_to: 23 }
    const action: NavigationAcknowledgement = { ...expected, version: 2,
      target: { panel_id: 'results', results_tab: 'dispatch' }, source_binding: past }
    const render = (marker: string | null, tab = 'dispatch') => {
      mount('Demo', tab)
      const data = document.querySelector<HTMLElement>('[data-assurance-tab]')!
      data.dataset.resultTab = tab
      if (marker !== null) data.dataset.resultBinding = marker
      return data
    }

    // A filter, an entity or a time selection changes the rendered data (its hash) or its window;
    // another run, section or scope is another source. None of them may certify this one.
    it.each([
      ['another filter or entity (data hash)', { data_sha256: 'b'.repeat(64) }],
      ['another time window start', { window_from: 1 }],
      ['another time window end', { window_to: 11 }],
      ['the latest run (current scope)', { scope: 'current' as const }],
      ['another run', { run_id: 'run-2' }],
      ['another kept section', { section: 'results_prices' }],
      ['another baseline run', { baseline_run_id: 'run-0' }],
      ['another project', { project_id: 'project-2' }],
    ])('is not certified by %s', async (_name, change) => {
      const verified = vi.fn()
      const stop = acknowledgeMountedView(action, () => true, verified)
      const data = render(JSON.stringify({ ...past, ...change }))
      await tick()
      expect(client.post).not.toHaveBeenCalled()
      data.dataset.resultBinding = JSON.stringify(past)
      await tick()
      expect(client.post).toHaveBeenCalledOnce()
      expect(verified).toHaveBeenCalledOnce()
      stop()
    })

    // A legacy or unrelated mount carries no usable marker for this tab.
    it.each([
      ['no marker (a bare tab mount)', null, 'dispatch'],
      ['a legacy marker without a version', JSON.stringify({ ...past, version: undefined }), 'dispatch'],
      ['an unknown marker version', JSON.stringify({ ...past, version: 2 }), 'dispatch'],
      ['a marker without a data hash', JSON.stringify({ ...past, data_sha256: undefined }), 'dispatch'],
      ['a malformed marker', '{not json', 'dispatch'],
      ['a marker of another tab', JSON.stringify(past), 'prices'],
    ])('is not certified by %s', async (_name, marker, markerTab) => {
      const verified = vi.fn()
      const stop = acknowledgeMountedView(action, () => true, verified)
      mount('Demo', 'dispatch')
      const tab = document.querySelector<HTMLElement>('[data-assurance-tab]')!
      tab.dataset.resultTab = markerTab
      if (marker !== null) tab.dataset.resultBinding = marker
      await tick()
      expect(client.post).not.toHaveBeenCalled()
      expect(verified).not.toHaveBeenCalled()
      stop()
    })

    it('a version 1 ACK on the same mount certifies the mount only, as before', async () => {
      const verified = vi.fn()
      const stop = acknowledgeMountedView({ ...expected, target: action.target }, () => true, verified)
      render(JSON.stringify({ ...past, data_sha256: 'b'.repeat(64) }))
      await tick()
      expect(client.post).toHaveBeenCalledWith('/chat/ui-ack', expect.not.objectContaining({ source_binding: expect.anything() }),
        { skipErrorToast: true })
      stop()
    })

    // A user navigation, a new turn or a project switch makes the ACK no longer current.
    it('a superseded ACK never posts when the view appears afterwards', async () => {
      let current = true
      const verified = vi.fn()
      acknowledgeMountedView(action, () => current, verified)
      current = false
      render(JSON.stringify(past))
      await tick()
      expect(client.post).not.toHaveBeenCalled()
      expect(verified).not.toHaveBeenCalled()
    })

    it('leaving the past-run view before the server answers drops the verification', async () => {
      let answer: (value: unknown) => void = () => {}
      vi.mocked(client.post).mockReturnValue(new Promise(resolve => { answer = resolve }) as never)
      const verified = vi.fn()
      acknowledgeMountedView(action, () => true, verified)
      render(JSON.stringify(past))
      await tick()
      expect(client.post).toHaveBeenCalledOnce()
      document.body.replaceChildren() // the user navigated away: nothing is mounted any more
      answer({ data: { outcome: 'completed' } })
      await tick()
      expect(verified).not.toHaveBeenCalled()
    })

    it('stopping the ACK (project switch, new turn) cancels a pending wait', async () => {
      const verified = vi.fn()
      const stop = acknowledgeMountedView(action, () => true, verified)
      stop()
      render(JSON.stringify(past))
      await tick()
      expect(client.post).not.toHaveBeenCalled()
      expect(verified).not.toHaveBeenCalled()
    })
  })
})
