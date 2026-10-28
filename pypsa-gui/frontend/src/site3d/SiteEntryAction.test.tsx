// SC-01b rule 3: the entry renders the /flow scene entry verbatim; the one
// state built here (an unsaved project) mirrors verdicts.py exactly.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SceneEntry } from '../api/flow'

const flowState: { flow: unknown; isPending: boolean } = { flow: null, isPending: false }
vi.mock('../hooks/useProjectFlow', () => ({ useProjectFlow: () => flowState }))

import { useUIStore } from '../store/uiStore'
import SiteEntryAction, { LABEL_CREATE, LABEL_OPEN, LABEL_RESUME, LABEL_SAVE_FIRST, REASON_SAVE_FIRST } from './SiteEntryAction'
import { useSiteCreation } from './siteCreation'

const verdicts = readFileSync(resolve(__dirname, '../../../backend/services/site_scene/verdicts.py'), 'utf8')
const pyConst = (name: string) => new RegExp(`^${name} = "([^"]+)"`, 'm').exec(verdicts)?.[1]

const renderEntry = () => render(<QueryClientProvider client={new QueryClient()}><SiteEntryAction /></QueryClientProvider>)
const withEntry = (entry: SceneEntry) => { flowState.flow = { scene: { entry, anchor_moved: [], actions: [] } } }

beforeEach(() => {
  flowState.flow = null
  useUIStore.setState({ currentProject: 'p', uiMode: 'guided', canvasView: 'blank', activeSlidePanel: 'hubDesign', flowReturn: null })
  useSiteCreation.setState({ open: false })
})

describe('the rule-3 entry', () => {
  it('mirrors the frozen strings of verdicts.py (no drift)', () => {
    expect(pyConst('LABEL_CREATE')).toBe(LABEL_CREATE)
    expect(pyConst('LABEL_SAVE_FIRST')).toBe(LABEL_SAVE_FIRST)
    expect(pyConst('LABEL_RESUME')).toBe(LABEL_RESUME)
    expect(pyConst('LABEL_OPEN')).toBe(LABEL_OPEN)
    expect(pyConst('REASON_SAVE_FIRST')).toBe(REASON_SAVE_FIRST)
  })

  it('unsaved project: the label and reason, a link to the header Save and no second save button', () => {
    useUIStore.setState({ currentProject: null })
    const save = document.createElement('button')
    save.setAttribute('aria-label', 'Save project')
    document.body.appendChild(save)
    renderEntry()
    expect(screen.getByTestId('site-entry-label').textContent).toBe('Save project first')
    expect(screen.queryByTestId('site-entry-action')).toBeNull()
    expect(screen.getByTestId('site-entry-reason').textContent).toContain(REASON_SAVE_FIRST)
    fireEvent.click(screen.getByText('› Save project'))
    expect(document.activeElement).toBe(save)
    save.remove()
  })

  it('no confirmed anchor: disabled, the visible reason and one › link to the coordinate field', () => {
    withEntry({ label: 'Create 3D site', enabled: false, blocker: { code: 'site_anchor_missing',
      reason: 'Add site coordinates to create a 3D site', link: '›',
      home: { guided: { section: 'project', drawer: 'site_anchor', open: 'request_then_scroll' }, expert: { panel: 'horizon' } } } })
    renderEntry()
    expect((screen.getByTestId('site-entry-action') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('site-entry-reason').textContent).toBe('Add site coordinates to create a 3D site › Site coordinates')
    fireEvent.click(screen.getByText('› Site coordinates'))
    expect(useUIStore.getState().fieldDrawer?.field).toBe('site_anchor')
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('anchor, no Site: Create opens Site 3D with the New 3D site panel and a way back', () => {
    withEntry({ label: 'Create 3D site', enabled: true, blocker: null })
    renderEntry()
    fireEvent.click(screen.getByTestId('site-entry-action'))
    const ui = useUIStore.getState()
    expect(ui.canvasView).toBe('site')
    expect(ui.sitePart).toBe('map')
    expect(useSiteCreation.getState().open).toBe(true)
    expect(ui.flowReturn).not.toBeNull()
  })

  it.each([['Resume site setup'], ['Open 3D site']])('%s opens Site 3D on Generate', label => {
    withEntry({ label, enabled: true, blocker: null })
    renderEntry()
    expect(screen.getByTestId('site-entry-action').textContent).toBe(label)
    fireEvent.click(screen.getByTestId('site-entry-action'))
    expect(useUIStore.getState().sitePart).toBe('generate')
    expect(useSiteCreation.getState().open).toBe(false)
  })
})
