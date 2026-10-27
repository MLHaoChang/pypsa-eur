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

const waiting = { ...task, status: 'waiting_job',
  next_step: { id: 'run', tool: 'run_simulation', status: 'waiting', job_id: 'fg:0123abcd' } }

it('LC-12: a waiting plan shows elapsed time and Stop waiting, not Resume', () => {
  const onStop = vi.fn()
  render(<AssistantResultCard result={{ _task: waiting }} disabled={false} onRequest={vi.fn()}
    continuation={{ state: 'waiting', onStop }} />)
  expect(screen.getByTestId('task-continuation-line').textContent).toMatch(/^Waiting for the solve to finish · 0:00/)
  expect(screen.queryByText('Resume task')).toBeNull()
  fireEvent.click(screen.getByText('Stop waiting'))
  expect(onStop).toHaveBeenCalledTimes(1)
  expect(screen.getByText('Cancel task')).toBeTruthy()
})

it('LC-12: a held continuation offers Continue now; other states are plain lines', () => {
  const onContinueNow = vi.fn()
  const { rerender } = render(<AssistantResultCard result={{ _task: waiting }} disabled={false} onRequest={vi.fn()}
    continuation={{ state: 'held', onContinueNow }} />)
  expect(screen.getByTestId('task-continuation-line').textContent).toContain("I'll continue when you're ready")
  fireEvent.click(screen.getByText('Continue now'))
  expect(onContinueNow).toHaveBeenCalledTimes(1)
  rerender(<AssistantResultCard result={{ _task: waiting }} disabled={false} onRequest={vi.fn()}
    continuation={{ state: 'elsewhere', project: 'oj-grid' }} />)
  expect(screen.getByTestId('task-continuation-line').textContent).toBe("This plan's solve is in oj-grid: open it to continue")
  rerender(<AssistantResultCard result={{ _task: waiting }} disabled={false} onRequest={vi.fn()}
    continuation={{ state: 'continued' }} />)
  expect(screen.getByTestId('task-continuation-line').textContent).toBe('The solve finished · continued below')
  rerender(<AssistantResultCard result={{ _task: task }} disabled={false} onRequest={vi.fn()}
    continuation={{ state: 'continued' }} />)
  expect(screen.queryByTestId('task-continuation-line')).toBeNull()
})
it('LC-02: lists the plan steps with their states, the applied changes, and the waiting job ID', () => {
  const plan = { ...task, status: 'waiting_job', completed_steps: 1, total_steps: 3,
    steps: [{ id: 'cap', tool: 'set_component_attribute', status: 'completed' },
      { id: 'solve', tool: 'solve_network', status: 'waiting' }, { id: 'chart', tool: 'create_chart', status: 'pending' }],
    next_step: { id: 'solve', tool: 'solve_network', status: 'waiting', job_id: 'job-42' },
    changes: ['Set G1 marginal cost 10 → 20 currency/MWh'] }
  render(<AssistantResultCard result={{ _task: plan }} disabled={false} onRequest={vi.fn()} />)
  const steps = screen.getAllByTestId('assistant-task-step')
  expect(steps.map(s => [s.dataset.status, s.textContent])).toEqual([
    ['completed', 'set_component_attribute · done'], ['waiting', 'solve_network · waiting for job job-42'],
    ['pending', 'create_chart · to do']])
  expect(steps[1].getAttribute('aria-current')).toBe('step')
  expect(screen.getByTestId('assistant-task-applied').textContent).toBe('Applied: Set G1 marginal cost 10 → 20 currency/MWh')
})
it('LC-02: names steps in words when given, shows the brief phase and says when steps are left out', () => {
  const plan = { ...task, steps: [{ id: 'a', tool: 'generate_report', status: 'failed' }], step_details_truncated: true,
    next_step: { id: 'a', tool: 'generate_report', status: 'failed' },
    brief: { objective: 'Size the battery', phase: { card: 'capacity', index: 1, of: 2 } } }
  render(<AssistantResultCard result={{ _task: plan }} disabled={false} onRequest={vi.fn()}
    toolWords={(t) => t === 'generate_report' ? 'start writing the project report' : t} />)
  expect(screen.getByTestId('assistant-task-step').textContent).toBe('start writing the project report · failed')
  expect(screen.getByTestId('assistant-task-phase').textContent).toBe('Size the battery · phase 1 of 2: capacity')
  expect(screen.getByText('Some steps are not listed.')).toBeTruthy()
  expect(screen.queryByTestId('assistant-task-applied')).toBeNull()
})
