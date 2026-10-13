// S8: the wizard's `decision` tab (question cards). The existing 'study' tab
// (gridspine) and 'blank' default are untouched (their own tests). The card
// degrades to a stated reason where the backend refuses the study routes
// (multi-user mode, OPEN-ITEMS 1; the flag off) and never crashes.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import NewProjectWizard from './NewProjectWizard'
import type { ProjectInfo } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { useDecisionStore } from '../pages/decision/decisionStore'
import { ERROR_COPY } from '../utils/decisionVocabulary'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
const availability = vi.hoisted(() => vi.fn())
const create = vi.hoisted(() => vi.fn())
vi.mock('../api/decisionStudies', async (orig) => {
  const actual = await orig<typeof import('../api/decisionStudies')>()
  return { ...actual, decisionStudiesApi: { ...actual.decisionStudiesApi, availability, create } }
})

const P: ProjectInfo = { id: '1', name: 'my-plant', created_at: '2026-09-01T00:00:00Z', has_solver_config: false,
  bus_count: 3, snapshot_count: 24, objective: null, parent_project: null }

function show(projects: ProjectInfo[] = [P], onClose = vi.fn(), onDecisionStart?: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}>
    <NewProjectWizard existingProjects={projects} onConfirm={() => {}} onClose={onClose} isPending={false}
      initialTab="decision" onDecisionStart={onDecisionStart} />
  </QueryClientProvider>)
  return onClose
}

beforeEach(() => {
  authMode.authEnabled = false
  availability.mockReset(); create.mockReset()
  useUIStore.setState({ currentProject: null, activeSlidePanel: null })
  useDecisionStore.setState({ active: null, draft: null, view: null, watch: null })
})
afterEach(() => cleanup())

describe('the decision tab', () => {
  it('offers the battery question and starts its intake in the decision panel', async () => {
    availability.mockResolvedValue({ available: true })
    const onClose = show()
    const start = await screen.findByRole('button', { name: /Start: Do I need a battery/ })
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(start)
    expect(useDecisionStore.getState().draft).toMatchObject({ pathProject: 'my-plant' })
    expect(useUIStore.getState().activeSlidePanel).toBe('decision')
    expect(onClose).toHaveBeenCalled()
  })

  it('is disabled with the reason in multi-user mode, without calling the backend', () => {
    authMode.authEnabled = true
    show()
    expect(screen.getByTestId('decision-reason').textContent).toContain(ERROR_COPY.decision_studies_unavailable.title)
    expect((screen.getByRole('button', { name: /Start: Do I need a battery/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(availability).not.toHaveBeenCalled()
  })

  it('is disabled with the reason when the flag is off', async () => {
    availability.mockResolvedValue({ available: false, code: 'decision_studies_disabled', message: 'off' })
    show()
    expect((await screen.findByTestId('decision-reason')).textContent).toContain('PYPSAGUI_DECISION_STUDIES=1')
  })

  it('needs a project to authorise the study, and says so', () => {
    show([])
    expect(screen.getByTestId('decision-reason').textContent).toMatch(/Create or open a project first/)
  })

  it('lets the home page follow the start into the workbench', async () => {
    availability.mockResolvedValue({ available: true })
    const onStart = vi.fn()
    show([P], vi.fn(), onStart)
    const start = await screen.findByRole('button', { name: /Start: Do I need a battery/ })
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(start)
    expect(onStart).toHaveBeenCalled()
  })
})
