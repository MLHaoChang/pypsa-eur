// The mapping-plan editor (WP11, plan §WP11 "an outline/plan panel with
// per-heading keep/rename/drop editing"). Untagged templates only: a tagged
// one fills itself from its `{{ tags }}`.
//
// The plan is `template_untagged.py::MappingPlan`, one entry per template
// heading: keep it (and write the chosen report sections under it), rename
// it, or drop it; plus sections inserted after a heading, placeholder
// values, and what the proposer could not place (`unmapped_sections`) with
// its `notes`. The editor holds a DRAFT of the stored plan (a keep-everything
// blank when nothing is stored yet) and a dirty flag; "Save plan" PUTs the
// draft — non-strict, so the backend keeps what it can and notes the rest —
// then invalidates the template query so the stored truth comes back and
// replaces the draft. A new stored plan (a proposal that finished, a save
// that answered) always replaces the draft: that is what the user asked for.
//
// "Propose with the assistant" is `useReportJob().proposeMapping` — the
// viewer's hook instance, passed in, so one poll serves the page and the
// viewer's `onFinished` fires once; the strip is rendered HERE for a
// `mapping` job (the viewer skips that mode) and the hook invalidates the
// template query when the job leaves `running`.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, Plus, Save, Sparkles, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  putMappingPlan,
  templateErrorMessage,
  type MappingAction,
  type MappingEntry,
  type MappingInsert,
  type MappingPlan,
  type ReportTemplateState,
  type TemplateOutline,
} from '../../api/reports'
import { Btn, PageSection, Tag } from '../../components/PageKit'
import type { SectionChoice } from './GenerateReportDialog'
import { ReportJobStrip } from './ReportJobStrip'
import { REPORT_TEMPLATE_KEY, type ReportJobApi } from './useReportJob'

const INPUT = 'px-2 py-1 text-[11.5px] border border-border rounded bg-bg text-text'
const SELECT = 'px-2 py-1 text-[11px] border border-border rounded bg-bg text-text'

const ACTIONS: Array<{ value: MappingAction; label: string }> = [
  { value: 'keep', label: 'keep' },
  { value: 'rename', label: 'rename' },
  { value: 'drop', label: 'drop' },
]

/** A keep-everything plan for an outline: what the editor starts from without a stored plan. */
export function blankPlan(outline: TemplateOutline): MappingPlan {
  return {
    entries: outline.headings.map(h => ({ heading_index: h.index, action: 'keep', new_text: null, section_ids: [] })),
    inserted: [],
    placeholders: {},
    unmapped_sections: [],
    notes: [],
  }
}

/** The draft the editor works on: the stored plan, with an entry for EVERY outline heading. */
export function draftFrom(outline: TemplateOutline, plan: MappingPlan | null): MappingPlan {
  if (!plan) return blankPlan(outline)
  const byIndex = new Map(plan.entries.map(e => [e.heading_index, e]))
  return {
    ...plan,
    entries: outline.headings.map(h => byIndex.get(h.index) ?? ({
      heading_index: h.index, action: 'keep', new_text: null, section_ids: [],
    })),
    placeholders: { ...plan.placeholders },
    inserted: plan.inserted.map(i => ({ ...i })),
  }
}

export function MappingPlanEditor({
  project, reportId, outline, plan, language, sections, job, onExport, exportPending,
}: {
  project: string
  reportId: string
  outline: TemplateOutline
  /** The stored plan from `GET …/template`; `null` when none yet. */
  plan: MappingPlan | null
  /** The template's detected language — what the proposer writes headings in. */
  language?: string | null
  /** The open document's sections (id + heading). */
  sections: readonly SectionChoice[]
  /** The viewer's `useReportJob` instance. */
  job: ReportJobApi
  /** The viewer's export path ("Export with this template"). */
  onExport?: () => void
  exportPending?: boolean
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<MappingPlan>(() => draftFrom(outline, plan))
  const [dirty, setDirty] = useState(false)
  // A finished mapping job whose outcome should stay visible (a failure).
  const [outcomeVisible, setOutcomeVisible] = useState(false)

  // A new stored plan replaces the draft (see the header comment).
  useEffect(() => {
    setDraft(draftFrom(outline, plan))
    setDirty(false)
  }, [outline, plan])

  const titles = useMemo(() => Object.fromEntries(sections.map(s => [s.id, s.title])), [sections])
  const title = (id: string) => titles[id] ?? id

  function edit(update: (d: MappingPlan) => MappingPlan) {
    setDraft(d => update(d))
    setDirty(true)
  }

  function setEntry(index: number, patch: Partial<MappingEntry>) {
    edit(d => ({
      ...d,
      entries: d.entries.map(e => (e.heading_index === index ? { ...e, ...patch } : e)),
    }))
  }

  function setInsert(i: number, patch: Partial<MappingInsert>) {
    edit(d => ({ ...d, inserted: d.inserted.map((ins, k) => (k === i ? { ...ins, ...patch } : ins)) }))
  }

  function addInsert() {
    const placed = new Set([
      ...draft.entries.flatMap(e => e.section_ids),
      ...draft.inserted.map(i => i.section_id),
    ])
    const pick = sections.find(s => !placed.has(s.id)) ?? sections[0]
    if (!pick) return
    const last = outline.headings[outline.headings.length - 1]?.index ?? 0
    edit(d => ({
      ...d,
      inserted: [...d.inserted, { after_heading_index: last, section_id: pick.id, heading: pick.title }],
    }))
  }

  function removeInsert(i: number) {
    edit(d => ({ ...d, inserted: d.inserted.filter((_, k) => k !== i) }))
  }

  function setPlaceholder(key: string, value: string) {
    edit(d => ({ ...d, placeholders: { ...d.placeholders, [key]: value } }))
  }

  const save = useMutation({
    mutationFn: () => putMappingPlan(project, reportId, {
      ...draft,
      // Empty values are "not filled", not "fill with nothing".
      placeholders: Object.fromEntries(Object.entries(draft.placeholders).filter(([, v]) => v.trim() !== '')),
      entries: draft.entries.map(e => ({
        ...e,
        new_text: e.action === 'rename' ? (e.new_text ?? '') : null,
      })),
    }),
    onSuccess: async (stored) => {
      // The PUT answers with the stored plan: it IS the draft now.
      setDraft(draftFrom(outline, stored))
      setDirty(false)
      qc.setQueryData<ReportTemplateState>(
        REPORT_TEMPLATE_KEY(project, reportId),
        prev => (prev ? { ...prev, plan: stored } : prev),
      )
      toast.success('Mapping plan saved')
      await qc.invalidateQueries({ queryKey: REPORT_TEMPLATE_KEY(project, reportId) })
    },
    onError: (e) => toast.error(`Could not save the plan: ${templateErrorMessage(e)}`),
  })

  async function propose() {
    try {
      setOutcomeVisible(false)
      await job.proposeMapping(reportId, language ? { language } : {})
    } catch (e) {
      toast.error(`Could not ask for a mapping plan: ${templateErrorMessage(e)}`)
    }
  }

  // The strip: this report's mapping job while it runs, and its outcome
  // line once it LEAVES running here (a record that is already terminal at
  // first read is an earlier visit's, not shown).
  const record = job.record
  const mappingJob = record && record.mode === 'mapping' && record.report_id === reportId ? record : null
  const jobBusy = job.isRunning || job.isStarting
  const wasRunning = useRef(false)
  useEffect(() => {
    const running = mappingJob?.status === 'running'
    if (wasRunning.current && !running && mappingJob) setOutcomeVisible(true)
    wasRunning.current = running
  }, [mappingJob])

  const placeholderKeys = useMemo(() => {
    const keys = outline.placeholders.map(p => p.text)
    for (const k of Object.keys(draft.placeholders)) if (!keys.includes(k)) keys.push(k)
    return keys
  }, [outline.placeholders, draft.placeholders])

  return (
    <PageSection
      title="Mapping plan"
      hint={
        <span className="inline-flex items-center gap-2">
          <span>which report section goes under which template heading</span>
          {dirty && <Tag tone="warn"><span data-testid="mapping-dirty">unsaved</span></Tag>}
        </span>
      }
      right={
        <div className="flex items-center gap-2">
          <Btn
            onClick={() => { void propose() }}
            disabled={jobBusy}
            title={jobBusy
              ? 'A report job is running — wait for it or abort it'
              : 'Ask the active LLM profile to map the report sections onto the template headings'}
            data-testid="mapping-propose"
          >
            <Sparkles size={12} /> Propose with the assistant
          </Btn>
          <Btn
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            title={dirty ? 'Store this plan for the next export' : 'No unsaved changes'}
            data-testid="mapping-save"
          >
            <Save size={12} /> {save.isPending ? 'Saving…' : 'Save plan'}
          </Btn>
          <Btn
            variant="primary"
            onClick={onExport}
            disabled={!onExport || dirty || !!exportPending}
            title={dirty ? 'Save the plan first — the export uses the stored plan' : 'Render this version into the template with the stored plan'}
            data-testid="mapping-export"
          >
            <Download size={12} /> {exportPending ? 'Exporting…' : 'Export with this template'}
          </Btn>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        {mappingJob && (job.isRunning || outcomeVisible) && (
          <ReportJobStrip
            record={mappingJob}
            titles={titles}
            progressPct={job.progressPct}
            aborting={job.aborting}
            onAbort={() => { job.abort().catch(e => toast.error(templateErrorMessage(e))) }}
          />
        )}

        <table className="w-full text-[11.5px]" data-testid="mapping-table">
          <thead>
            <tr className="text-[10.5px] text-muted text-left">
              <th className="py-1 pr-2 font-medium">#</th>
              <th className="py-1 pr-2 font-medium">Level</th>
              <th className="py-1 pr-2 font-medium">Template heading</th>
              <th className="py-1 pr-2 font-medium">Style</th>
              <th className="py-1 pr-2 font-medium">Action</th>
              <th className="py-1 pr-2 font-medium">New text</th>
              <th className="py-1 font-medium">Report sections</th>
            </tr>
          </thead>
          <tbody>
            {outline.headings.map(h => {
              const entry = draft.entries.find(e => e.heading_index === h.index)
                ?? { heading_index: h.index, action: 'keep' as const, new_text: null, section_ids: [] }
              return (
                <tr
                  key={h.index}
                  className={`border-t border-border align-top ${entry.action === 'drop' ? 'opacity-60' : ''}`}
                  data-testid={`mapping-row-${h.index}`}
                  data-heading-index={h.index}
                >
                  <td className="py-1.5 pr-2 font-mono text-muted">{h.index}</td>
                  <td className="py-1.5 pr-2 font-mono text-muted">H{h.level}</td>
                  <td className="py-1.5 pr-2 text-text" style={{ paddingLeft: `${Math.max(0, h.level - 1) * 12}px` }}>
                    <span className={entry.action === 'drop' ? 'line-through' : ''}>{h.text}</span>
                  </td>
                  <td className="py-1.5 pr-2 text-muted whitespace-nowrap">{h.style}</td>
                  <td className="py-1.5 pr-2">
                    <select
                      aria-label={`Action for heading ${h.index}`}
                      className={SELECT}
                      value={entry.action}
                      onChange={e => setEntry(h.index, {
                        action: e.target.value as MappingAction,
                        new_text: e.target.value === 'rename' ? (entry.new_text ?? h.text) : null,
                      })}
                    >
                      {ACTIONS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 pr-2">
                    {entry.action === 'rename' && (
                      <input
                        aria-label={`New text for heading ${h.index}`}
                        className={`${INPUT} w-full min-w-[120px]`}
                        value={entry.new_text ?? ''}
                        onChange={e => setEntry(h.index, { new_text: e.target.value })}
                        placeholder={h.text}
                      />
                    )}
                  </td>
                  <td className="py-1.5">
                    <select
                      multiple
                      aria-label={`Sections for heading ${h.index}`}
                      className={`${SELECT} w-full min-w-[160px]`}
                      size={Math.min(4, Math.max(2, sections.length))}
                      value={entry.section_ids}
                      disabled={entry.action === 'drop'}
                      onChange={e => setEntry(h.index, {
                        section_ids: Array.from(e.target.selectedOptions).map(o => o.value),
                      })}
                      title="The report sections written under this heading (Ctrl/Cmd-click for several)"
                    >
                      {sections.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
                    </select>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        <div className="flex flex-col gap-1.5" data-testid="mapping-inserted">
          <div className="flex items-center gap-2">
            <span className="text-[10.5px] font-medium text-muted">Inserted sections</span>
            <Btn onClick={addInsert} disabled={sections.length === 0} title="Insert a report section after a template heading" data-testid="mapping-insert-add">
              <Plus size={11} /> Add
            </Btn>
          </div>
          {draft.inserted.length === 0 ? (
            <p className="text-[11px] text-muted">None — every report section is written under an existing heading.</p>
          ) : draft.inserted.map((ins, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 text-[11.5px]" data-testid="mapping-inserted-row">
              <select
                aria-label={`Inserted section ${i}`}
                className={SELECT}
                value={ins.section_id}
                onChange={e => setInsert(i, { section_id: e.target.value, heading: title(e.target.value) })}
              >
                {sections.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
                {!sections.some(s => s.id === ins.section_id) && <option value={ins.section_id}>{ins.section_id}</option>}
              </select>
              <span className="text-muted">as</span>
              <input
                aria-label={`Inserted heading ${i}`}
                className={`${INPUT} min-w-[160px]`}
                value={ins.heading}
                onChange={e => setInsert(i, { heading: e.target.value })}
              />
              <span className="text-muted">after heading</span>
              <select
                aria-label={`Insert after heading ${i}`}
                className={SELECT}
                value={String(ins.after_heading_index)}
                onChange={e => setInsert(i, { after_heading_index: Number(e.target.value) })}
              >
                {outline.headings.map(h => (
                  <option key={h.index} value={String(h.index)}>{h.index} · {h.text}</option>
                ))}
              </select>
              <Btn onClick={() => removeInsert(i)} aria-label={`Remove insertion ${i}`} title="Remove this insertion">
                <Trash2 size={11} />
              </Btn>
            </div>
          ))}
        </div>

        {placeholderKeys.length > 0 && (
          <div className="flex flex-col gap-1.5" data-testid="mapping-placeholders">
            <span className="text-[10.5px] font-medium text-muted">Placeholders</span>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
              {placeholderKeys.map(key => (
                <label key={key} className="inline-flex items-center gap-2 text-[11.5px]">
                  <span className="font-mono text-muted min-w-[120px] truncate" title={key}>{key}</span>
                  <input
                    aria-label={`Placeholder ${key}`}
                    className={`${INPUT} flex-1`}
                    value={draft.placeholders[key] ?? ''}
                    onChange={e => setPlaceholder(key, e.target.value)}
                    placeholder="leave as in the template"
                  />
                </label>
              ))}
            </div>
          </div>
        )}

        {draft.unmapped_sections.length > 0 && (
          <p className="text-[11px] text-warn" data-testid="mapping-unmapped">
            Not placed: {draft.unmapped_sections.map(title).join(', ')}
          </p>
        )}
        {draft.notes.length > 0 && (
          <ul className="text-[11px] text-muted pl-4 list-disc" data-testid="mapping-notes" aria-label="Plan notes">
            {draft.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        )}
      </div>
    </PageSection>
  )
}
