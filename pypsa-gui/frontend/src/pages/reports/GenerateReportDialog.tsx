// The "Generate report…" dialog (WP7b, plan §WP7).
//
// Collects what `POST /{name}/reports/generate` takes — a title, the
// language (`en` by default, any free text the model can follow), an optional
// subset of sections, an optional instruction — and hands it to `onGenerate`
// (the panel's `useReportJob().start`). The backend's refusal kinds become
// the toast copy promised in the plan; the dialog stays open on a refusal so
// the user can change the input, and closes on success.
//
// Section choices: the panel passes the section ids + headings of the newest
// evidence-only document when one exists (that is the skeleton the generator
// writes into); otherwise the fixed catalogue below — the 12 Energy Hub
// sections in `REPORT_SECTIONS` order with their `SECTION_TITLES` from
// `services/reports/docx_writer.py`, preceded by the executive summary
// (`services/reports/assemble.py::EXECUTIVE_SUMMARY_ID`). Nothing checked
// means "all sections" (the backend's default set).
//
// WP11 — template: with a `project`, the dialog lists the Project's
// `report_template` uploads (plain `listUploads`, no query client needed
// here) after "Built-in default" and sends the choice as `template_file_id`.
// The template's detected language is known only once a report has been
// bound to it (`GET …/{id}/template`), so the caller passes what it knows
// as `templateLanguages` (file_id → language) and the dialog pre-fills the
// language field from it when a template is picked; the field stays
// editable, and switching back to the default keeps what is typed.
import { useEffect, useId, useState, type FormEvent } from 'react'
import { Sparkles } from 'lucide-react'
import toast from 'react-hot-toast'
import { reportJobErrorMessage, type GenerateReportOptions } from '../../api/reports'
import { listUploads, type UploadMeta } from '../../api/uploads'
import { Dialog } from '../../components/Dialog'
import { Btn } from '../../components/PageKit'

export interface SectionChoice { id: string; title: string }

/** Mirrors `docx_writer.SECTION_TITLES` in `REPORT_SECTIONS` order, plus the summary. */
export const DEFAULT_SECTION_CHOICES: readonly SectionChoice[] = [
  { id: 'executive_summary', title: 'Executive summary' },
  { id: 'target', title: 'Availability target and achieved adequacy' },
  { id: 'certification', title: 'Certification (sequential Monte Carlo)' },
  { id: 'cost', title: 'Cost at target' },
  { id: 'frontier', title: 'Cost-vs-availability frontier' },
  { id: 'sizing', title: 'Sizing of the least-cost plan' },
  { id: 'redundancy', title: 'Redundancy options' },
  { id: 'levers', title: 'Optimisation levers' },
  { id: 'dtc', title: 'Critical-load (DtC) stress' },
  { id: 'fmea_top', title: 'Residual failure modes (FMEA top-N)' },
  { id: 'tea', title: 'Techno-economic summary (LCOE / LCOH)' },
  { id: 'gates', title: 'Dynamics gates' },
  { id: 'multi_energy', title: 'Multi-energy adequacy' },
]

const INPUT = 'w-full px-2 py-1 text-[12px] border border-border rounded bg-bg text-text'

function formatTemplateOption(t: UploadMeta): string {
  const d = new Date(t.uploaded_at * 1000)
  return Number.isNaN(d.getTime()) ? t.filename : `${t.filename} · ${d.toLocaleDateString()}`
}

export function GenerateReportDialog({
  open, onClose, onGenerate, sectionChoices, defaultTitle, project, templateLanguages,
}: {
  open: boolean
  onClose: () => void
  /** `useReportJob().start` — resolves once the job is accepted. */
  onGenerate: (opts: GenerateReportOptions) => Promise<unknown>
  sectionChoices?: readonly SectionChoice[] | null
  defaultTitle?: string
  /** WP11: the Project whose `report_template` uploads the template select lists. */
  project?: string | null
  /** WP11: template `file_id` → detected language, when the caller knows it. */
  templateLanguages?: Record<string, string> | null
}) {
  const ids = useId()
  const [title, setTitle] = useState(defaultTitle ?? '')
  const [language, setLanguage] = useState('en')
  const [picked, setPicked] = useState<string[]>([])
  const [instruction, setInstruction] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [templates, setTemplates] = useState<UploadMeta[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset the form on every open so a second report starts clean.
  useEffect(() => {
    if (!open) return
    setTitle(defaultTitle ?? '')
    setLanguage('en')
    setPicked([])
    setInstruction('')
    setTemplateId('')
    setError(null)
    setPending(false)
  }, [open, defaultTitle])

  // The Project's templates, read on every open; a failed read leaves the
  // built-in default as the only choice (the dialog still generates).
  useEffect(() => {
    if (!open || !project) { setTemplates([]); return }
    let cancelled = false
    listUploads(project, 'report_template')
      .then(list => { if (!cancelled) setTemplates(list) })
      .catch(() => { if (!cancelled) setTemplates([]) })
    return () => { cancelled = true }
  }, [open, project])

  function pickTemplate(id: string) {
    setTemplateId(id)
    const known = id ? templateLanguages?.[id] : undefined
    if (known) setLanguage(known)
  }

  const choices = sectionChoices && sectionChoices.length > 0 ? sectionChoices : DEFAULT_SECTION_CHOICES

  const toggle = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]))

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (pending) return
    const lang = language.trim()
    if (!lang) {
      setError('A language is required (for example "en").')
      return
    }
    const opts: GenerateReportOptions = { language: lang }
    if (title.trim()) opts.title = title.trim()
    // Keep the document's order, not the click order.
    const ordered = choices.map(c => c.id).filter(id => picked.includes(id))
    if (ordered.length > 0) opts.sections = ordered
    if (instruction.trim()) opts.instruction = instruction.trim()
    if (templateId) opts.template_file_id = templateId
    setPending(true)
    setError(null)
    try {
      await onGenerate(opts)
      toast.success('Report generation started')
      onClose()
    } catch (err) {
      const msg = reportJobErrorMessage(err, 'Could not start the report')
      setError(msg)
      toast.error(`Could not start the report: ${msg}`)
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => { if (!pending) onClose() }}
      title="Generate report"
      dismissOnBackdrop={!pending}
      panelClassName="bg-bg rounded-xl shadow-2xl w-[520px] max-w-[95vw] overflow-hidden"
    >
      <form onSubmit={submit} className="p-4 flex flex-col gap-3" data-testid="generate-report-form">
        <h2 className="text-sm font-semibold text-text inline-flex items-center gap-2">
          <Sparkles size={14} aria-hidden="true" /> Generate report
        </h2>
        <p className="text-[11.5px] text-muted">
          The active LLM profile writes prose for each section from the evidence the
          session's studies produced. Tables and figures stay computed; every number in
          the prose is checked against the evidence and flagged when it is not found.
        </p>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-title`} className="text-[10.5px] font-medium text-muted">Title</label>
          <input
            id={`${ids}-title`}
            className={INPUT}
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Study report — <project> (default)"
            disabled={pending}
          />
        </div>

        {project && (
          <div className="flex flex-col gap-1">
            <label htmlFor={`${ids}-template`} className="text-[10.5px] font-medium text-muted">Template</label>
            <select
              id={`${ids}-template`}
              className={INPUT}
              value={templateId}
              onChange={e => pickTemplate(e.target.value)}
              disabled={pending}
              title="The Word file the report renders into; upload templates from an open report's viewer"
            >
              <option value="">Built-in default</option>
              {templates.map(t => (
                <option key={t.file_id} value={t.file_id}>{formatTemplateOption(t)}</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-language`} className="text-[10.5px] font-medium text-muted">Language</label>
          <input
            id={`${ids}-language`}
            className={INPUT}
            value={language}
            onChange={e => setLanguage(e.target.value)}
            placeholder="en"
            disabled={pending}
            list={`${ids}-languages`}
          />
          <datalist id={`${ids}-languages`}>
            <option value="en" />
            <option value="de" />
            <option value="fr" />
            <option value="es" />
            <option value="nl" />
          </datalist>
        </div>

        <fieldset className="flex flex-col gap-1 min-w-0" disabled={pending}>
          <legend className="text-[10.5px] font-medium text-muted">
            Sections <span className="font-normal">(none checked = all)</span>
          </legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-0.5 max-h-[180px] overflow-y-auto pr-1">
            {choices.map(c => (
              <label key={c.id} className="inline-flex items-start gap-1.5 text-[11.5px] text-text cursor-pointer">
                <input
                  type="checkbox"
                  value={c.id}
                  checked={picked.includes(c.id)}
                  onChange={() => toggle(c.id)}
                  className="mt-0.5"
                />
                <span className="truncate" title={c.title}>{c.title}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-instruction`} className="text-[10.5px] font-medium text-muted">Instruction</label>
          <input
            id={`${ids}-instruction`}
            className={INPUT}
            value={instruction}
            onChange={e => setInstruction(e.target.value)}
            placeholder="Optional — e.g. address the board, keep each section under 150 words"
            disabled={pending}
          />
        </div>

        {error && (
          <p className="text-[11.5px] text-danger" data-testid="generate-error" role="alert">{error}</p>
        )}

        <div className="flex justify-end gap-2 mt-1">
          <Btn type="button" onClick={onClose} disabled={pending}>Cancel</Btn>
          <Btn type="submit" variant="primary" disabled={pending} data-testid="generate-submit">
            <Sparkles size={12} /> {pending ? 'Starting…' : 'Generate'}
          </Btn>
        </div>
      </form>
    </Dialog>
  )
}
