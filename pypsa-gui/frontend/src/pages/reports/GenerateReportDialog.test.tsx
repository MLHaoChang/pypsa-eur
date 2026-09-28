/**
 * `GenerateReportDialog` (WP7b) — title, language (`en` default, free
 * field), an optional section multi-select, an optional instruction; submit
 * hands the options to `onGenerate` and the backend's refusal kinds become
 * the toast copy the panel promises (`report_job_in_flight`, `no_evidence`,
 * `missing_api_key` / `sdk_not_installed`, `project_locked`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReportsError } from '../../api/reports'
import { DEFAULT_SECTION_CHOICES, GenerateReportDialog } from './GenerateReportDialog'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

function renderDialog(over: Partial<React.ComponentProps<typeof GenerateReportDialog>> = {}) {
  const onGenerate = vi.fn().mockResolvedValue({ status: 'running', report_id: 'a1b2c3d4e5f60718' })
  const onClose = vi.fn()
  render(
    <GenerateReportDialog open onClose={onClose} onGenerate={onGenerate} {...over} />,
  )
  return { onGenerate, onClose }
}

beforeEach(() => {
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => cleanup())

describe('GenerateReportDialog', () => {
  it('lists the fixed section catalogue (executive summary + the 12 EH sections) when no document is given', () => {
    renderDialog()
    const dialog = screen.getByRole('dialog')
    const boxes = within(dialog).getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.map(b => b.value)).toEqual(DEFAULT_SECTION_CHOICES.map(c => c.id))
    expect(DEFAULT_SECTION_CHOICES.map(c => c.id)).toEqual([
      'executive_summary', 'target', 'certification', 'cost', 'frontier', 'sizing', 'redundancy',
      'levers', 'dtc', 'fmea_top', 'tea', 'gates', 'multi_energy',
    ])
    expect(within(dialog).getByLabelText(/Residual failure modes/)).toBeTruthy()
    expect(within(dialog).getByLabelText(/Executive summary/)).toBeTruthy()
    // none checked → "all sections"
    expect(boxes.every(b => !b.checked)).toBe(true)
  })

  it('offers the sections of the given document instead of the catalogue', () => {
    renderDialog({ sectionChoices: [{ id: 'summary', title: 'Summary' }, { id: 'gates', title: 'Gates' }] })
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.map(b => b.value)).toEqual(['summary', 'gates'])
  })

  it('submits the defaults: language en, no title, no sections, no instruction', async () => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    expect((screen.getByLabelText('Language') as HTMLInputElement).value).toBe('en')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledTimes(1))
    expect(onGenerate).toHaveBeenCalledWith({ language: 'en' })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalled()
  })

  it('submits title, a free-form language, the picked sections and the instruction', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog()
    await user.type(screen.getByLabelText('Title'), 'Client report')
    const lang = screen.getByLabelText('Language')
    await user.clear(lang)
    await user.type(lang, 'de')
    await user.click(screen.getByLabelText(/Cost at target/))
    await user.click(screen.getByLabelText(/Residual failure modes/))
    await user.type(screen.getByLabelText('Instruction'), 'Keep it short.')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({
      title: 'Client report', language: 'de', sections: ['cost', 'fmea_top'], instruction: 'Keep it short.',
    }))
  })

  it('does not submit an empty language', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog()
    await user.clear(screen.getByLabelText('Language'))
    await user.click(screen.getByTestId('generate-submit'))
    expect(onGenerate).not.toHaveBeenCalled()
    expect(screen.getByTestId('generate-error').textContent).toMatch(/language/i)
  })

  it.each([
    ['report_job_in_flight', 409, /already being written/i],
    ['no_evidence', 400, /run a study first/i],
    ['missing_api_key', 400, /configure an LLM profile in Settings/i],
    ['sdk_not_installed', 400, /configure an LLM profile in Settings/i],
    ['project_locked', 409, /being edited by another user/i],
  ])('toasts the copy for %s and stays open', async (kind, status, copy) => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    onGenerate.mockRejectedValue(new ReportsError(
      { error_kind: kind, message: "'Demo' is being edited by another user." }, status,
    ))
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(copy)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('closes on Cancel without generating', async () => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    await user.click(screen.getByText('Cancel'))
    expect(onClose).toHaveBeenCalled()
    expect(onGenerate).not.toHaveBeenCalled()
  })
})
