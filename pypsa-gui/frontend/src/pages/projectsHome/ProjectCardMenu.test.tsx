import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ProjectInfo } from '../../api/types'

const api = vi.hoisted(() => ({ rename: vi.fn(), del: vi.fn(), reset: vi.fn() }))
vi.mock('../../api/projects', () => ({ projectsApi: { delete: api.del } }))
vi.mock('../../api/client', () => ({ default: { post: api.rename }, formatApiDetail: (d: unknown, f: string) => (typeof d === 'string' ? d : f) }))
vi.mock('../../api/network', () => ({ networkApi: { resetNetwork: api.reset } }))
vi.mock('../ScenariosPanel', () => ({ describeDescendants: (_d: unknown, n: string) => `'${n}' has children. Delete it and all of them?` }))

import { useUIStore } from '../../store/uiStore'
import { cardActions, DeleteProjectDialog, InlineRename, ProjectCardMenu, renameRefusal } from './ProjectCardMenu'

const P = { id: 'p1', name: 'Alpha' } as ProjectInfo
const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)

beforeEach(() => { vi.clearAllMocks(); useUIStore.setState({ currentProject: null }) })
afterEach(cleanup)

describe('cardActions', () => {
  it('offers all four, only Delete for a missing folder, nothing for a viewer', () => {
    expect(cardActions(P)).toEqual(['open', 'rename', 'duplicate', 'delete'])
    expect(cardActions({ ...P, missing: true })).toEqual(['delete'])
    expect(cardActions({ ...P, my_role: 'viewer' } as ProjectInfo)).toEqual([])
  })
})

describe('ProjectCardMenu', () => {
  const mount = () => {
    const h = { onOpen: vi.fn(), onRename: vi.fn(), onDuplicate: vi.fn(), onDelete: vi.fn() }
    wrap(<ProjectCardMenu project={P} {...h} />)
    return h
  }
  it('opens from a button, moves with arrows, closes on Esc and returns focus', () => {
    mount()
    const btn = screen.getByTestId('project-menu-button-Alpha')
    expect(btn.getAttribute('aria-haspopup')).toBe('menu')
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(btn)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    const items = screen.getAllByRole('menuitem')
    expect(items.map(i => i.textContent)).toEqual(['Open', 'Rename', 'Duplicate', 'Delete'])
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(items[1], { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(btn)
  })
  it('closes on an outside click and runs the chosen item', () => {
    const h = mount()
    fireEvent.click(screen.getByTestId('project-menu-button-Alpha'))
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(screen.getByTestId('project-menu-button-Alpha'))
    fireEvent.click(screen.getByTestId('project-menu-duplicate'))
    expect(h.onDuplicate).toHaveBeenCalled()
  })
  it('renders no button for a viewer', () => {
    wrap(<ProjectCardMenu project={{ ...P, my_role: 'viewer' } as ProjectInfo} onOpen={vi.fn()} onRename={vi.fn()} onDuplicate={vi.fn()} onDelete={vi.fn()} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('InlineRename', () => {
  it('renames and keeps the open project in step', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    api.rename.mockResolvedValue({ data: {} })
    const done = vi.fn()
    wrap(<InlineRename project={P} onDone={done} />)
    const input = screen.getByTestId('project-rename-input')
    fireEvent.change(input, { target: { value: 'Beta' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(done).toHaveBeenCalled())
    expect(api.rename).toHaveBeenCalledWith('/projects/p1/rename', { new_name: 'Beta' }, { skipErrorToast: true })
  })
  it('a name clash reads in words with curly quotes', async () => {
    api.rename.mockRejectedValue({ response: { data: { detail: "Project 'Beta' already exists" } }, message: 'x' })
    wrap(<InlineRename project={P} onDone={vi.fn()} />)
    const input = screen.getByTestId('project-rename-input')
    fireEvent.change(input, { target: { value: 'Beta' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect((await screen.findByTestId('project-rename-error')).textContent).toBe('A project named “Beta” already exists. Pick another name.')
  })
  it('renameRefusal passes other refusals through', () => {
    expect(renameRefusal('Locked', 'x')).toBe('Locked')
  })
})

describe('DeleteProjectDialog', () => {
  it('confirms with the Scenarios text, then deletes and closes the open project', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    api.del.mockResolvedValue({ deleted: ['Alpha'], failed: [] })
    const onClose = vi.fn()
    wrap(<DeleteProjectDialog project={P} onClose={onClose} />)
    expect(screen.getByText(/Delete 'Alpha'\? This removes its files from disk\. It is the open project, so it closes now\./)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.del).toHaveBeenCalledWith('p1', false)
    expect(useUIStore.getState().currentProject).toBeNull()
  })
  it('asks again when the project has child scenarios', async () => {
    api.del.mockRejectedValueOnce({ response: { status: 409, data: { detail: {} } } })
    wrap(<DeleteProjectDialog project={P} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText(/has children/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete all' })).toBeTruthy()
  })
})
