import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Btn } from '../PageKit'
import { memoryApi, errorWords, KIND_WORDS, type MemoryItem } from '../../api/memory'

// The body form for one item (plan §4 "Edit"): saving writes a new version;
// the old versions stay in History. Limits mirror memory_store's.
export const SUMMARY_MAX = 160
export const NOTE_MAX = 280

// Closed preference values (memory_store.PREFERENCE_KEYS); other keys keep
// their value and only the summary and note are editable here.
const PREFERENCE_CHOICES: Record<string, string[]> = {
  'units.power': ['kW', 'MW', 'GW'],
  'units.energy': ['kWh', 'MWh', 'GWh', 'TWh'],
  'answer.style': ['brief', 'detailed'],
}
const PREFERENCE_WORDS: Record<string, string> = {
  'units.power': 'Power unit',
  'units.energy': 'Energy unit',
  'answer.style': 'Answer style',
  currency: 'Currency',
  'report.sections': 'Report sections',
  'sensitivities.default': 'Default sensitivities',
}
export const CHART_TYPE_WORDS: Record<string, string> = {
  line: 'Line', bar: 'Bar', stacked_area: 'Stacked area', stacked_bar: 'Stacked bar',
  duration: 'Duration curve', heatmap: 'Heatmap',
}

const inputCls = 'h-[24px] text-[11px] bg-bg border border-border rounded px-1.5 text-text'

export default function MemoryEditForm({
  item, confirmAfter, onDone,
}: { item: MemoryItem; confirmAfter: boolean; onDone: () => void }) {
  const qc = useQueryClient()
  const body = (item.body ?? {}) as Record<string, unknown>
  const [summary, setSummary] = useState(item.summary)
  const [value, setValue] = useState(String(body.value ?? ''))
  const [note, setNote] = useState(String(body.note ?? ''))
  const [text, setText] = useState(String(body.text ?? ''))
  const chart = (body.spec as { chart?: { type?: string } } | undefined)?.chart
  const [chartType, setChartType] = useState(String(chart?.type ?? 'line'))

  const key = String(body.key ?? '')
  const choices = PREFERENCE_CHOICES[key]

  const nextBody = (): Record<string, unknown> | undefined => {
    if (item.kind === 'preference') {
      const out: Record<string, unknown> = { ...body }
      if (choices || key === 'currency') out.value = value
      if (note.trim()) out.note = note.trim(); else delete out.note
      return out
    }
    if (item.kind === 'lesson') return { ...body, text: text.trim() }
    if (item.kind === 'chart_spec') {
      const spec = { ...(body.spec as Record<string, unknown>) }
      spec.chart = { ...(spec.chart as Record<string, unknown>), type: chartType }
      return { ...body, spec }
    }
    return undefined
  }

  const save = useMutation({
    mutationFn: async () => {
      const saved = await memoryApi.edit(item.id, { summary: summary.trim(), body: nextBody() })
      return confirmAfter ? memoryApi.act(item.id, 'confirm') : saved
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory'] })
      toast.success(confirmAfter ? 'Saved to memory' : 'Memory updated')
      onDone()
    },
  })

  const summaryEmpty = summary.trim() === ''
  const lessonEmpty = item.kind === 'lesson' && text.trim() === ''
  const blockReason = summaryEmpty ? 'A summary is needed.'
    : lessonEmpty ? 'The lesson text is needed.' : null

  return (
    <form
      className="px-4 py-3 border-b border-border bg-panel/40 flex flex-col gap-2 text-[11px]"
      data-testid="memory-edit-form"
      onSubmit={e => { e.preventDefault(); if (!blockReason) save.mutate() }}
    >
      <div className="text-[10.5px] text-muted">
        Edit {KIND_WORDS[item.kind].toLowerCase()} · v{item.version} · saving writes v{item.version + 1}
      </div>
      <label className="flex flex-col gap-0.5">
        <span className="text-muted">Summary</span>
        <input aria-label="Summary" className={inputCls} value={summary} maxLength={SUMMARY_MAX}
          onChange={e => setSummary(e.target.value)} />
      </label>
      {item.kind === 'preference' && (
        <div className="flex gap-3">
          <label className="flex flex-col gap-0.5">
            <span className="text-muted">{PREFERENCE_WORDS[key] ?? key}</span>
            {choices ? (
              <select aria-label="Value" className={inputCls} value={value} onChange={e => setValue(e.target.value)}>
                {choices.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : key === 'currency' ? (
              <input aria-label="Value" className={`${inputCls} w-20 uppercase`} value={value} maxLength={3}
                onChange={e => setValue(e.target.value.toUpperCase())} />
            ) : (
              <span className="text-text">Kept as saved</span>
            )}
          </label>
          <label className="flex flex-col gap-0.5 flex-1">
            <span className="text-muted">Note</span>
            <input aria-label="Note" className={inputCls} value={note} maxLength={NOTE_MAX}
              onChange={e => setNote(e.target.value)} />
          </label>
        </div>
      )}
      {item.kind === 'lesson' && (
        <label className="flex flex-col gap-0.5">
          <span className="text-muted">Lesson</span>
          <textarea aria-label="Lesson" className={`${inputCls} h-14 py-1`} value={text} maxLength={NOTE_MAX}
            onChange={e => setText(e.target.value)} />
        </label>
      )}
      {item.kind === 'chart_spec' && (
        <label className="flex flex-col gap-0.5 w-48">
          <span className="text-muted">Chart</span>
          <select aria-label="Chart type" className={inputCls} value={chartType}
            onChange={e => setChartType(e.target.value)}>
            {Object.entries(CHART_TYPE_WORDS).map(([v, w]) => <option key={v} value={v}>{w}</option>)}
          </select>
        </label>
      )}
      {save.error && (
        <p role="alert" className="text-danger">{errorWords(save.error)}</p>
      )}
      <div className="flex items-center gap-2">
        <Btn variant="primary" type="submit" disabled={!!blockReason || save.isPending}>
          {confirmAfter ? 'Save and confirm' : 'Save'}
        </Btn>
        <Btn type="button" onClick={onDone}>Cancel</Btn>
        {blockReason && <span className="text-muted">{blockReason}</span>}
      </div>
    </form>
  )
}
