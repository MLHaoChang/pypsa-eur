import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import AssistantResultCard from './AssistantResultCard'
afterEach(cleanup)
const task = { task_id: '0ba037fe-bc46-43d3-a9c2-9b587c9cd50e', title: 'Refine grid', status: 'ready',
  completed_steps: 2, total_steps: 4, next_step: { tool: 'wait_for_job', status: 'pending' } }
it('shows durable progress and sends ordinary resume/cancel requests', () => {
  const onRequest = vi.fn()
  render(<AssistantResultCard result={{ _task: task }} disabled={false} onRequest={onRequest} />)
  expect(screen.getByText('ready · 2 / 4 steps')).toBeTruthy()
  fireEvent.click(screen.getByText('Resume task'))
  expect(onRequest.mock.calls[0][0]).toContain(`resume_task with task_id ${task.task_id}`)
  fireEvent.click(screen.getByText('Cancel task'))
  expect(onRequest.mock.calls[1][0]).toContain('leave running jobs alone')
})
it('requires review for uncertain writes and disables controls during streaming', () => {
  const onRequest = vi.fn()
  render(<AssistantResultCard result={{ ...task, status: 'needs_review' }} disabled onRequest={onRequest} />)
  fireEvent.click(screen.getByText('Review task'))
  expect(onRequest).not.toHaveBeenCalled()
  expect(screen.getByText(/effects may already exist/)).toBeTruthy()
})
it('offers only authenticated project blob links', () => {
  const url = '/api/projects/project-id/uploads/0123456789abcdef/blob'
  const { rerender } = render(<AssistantResultCard result={{ download_url: url, filename: 'chart.png' }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('assistant-file-download').getAttribute('href')).toBe(url)
  rerender(<AssistantResultCard result={{ download_url: 'javascript:alert(1)', filename: 'evil' }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.queryByTestId('assistant-file-download')).toBeNull()
})
it('ignores ordinary results and malformed IDs', () => {
  const { container } = render(<AssistantResultCard result={{ task_id: '<script>', status: 'ready' }} disabled={false} onRequest={vi.fn()} />)
  expect(container.textContent).toBe('')
})

it('renders old/new engineering values before application confirmation', () => {
  render(<AssistantResultCard result={{ preview_id: 'preview', valid: true, changes_total: 1, diff: [
    { name: 'G1', attribute: 'marginal_cost', before: 10, after: 20, unit: 'currency/MWh' },
  ] }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByRole('table').textContent).toContain('G1marginal_cost1020currency/MWh')
  expect(screen.getByText(/No network changes applied/)).toBeTruthy()
})
