// The grid-readiness scorecard (DC grid readiness 02, PR B): the advisory
// red-flag screening of GET /campus-electrical/{name}/grid-readiness, read
// only. One component, two mounts (UX review of the placement):
//
// * Expert, RUN › Issues: each row's "Go to field" opens its component, the
//   same jump the Issues rows use.
// * Guided, the Site card: no jump (it would drop the user out of Guided);
//   each row offers the card's Ask action and, where the input is a real
//   field, says where it is edited in Expert.
//
// Nothing here blocks Run (project workflow R1). A row's verdict is a word and
// an icon, never a colour alone; an unknown row says first what is missing.
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, ChevronRight, HelpCircle,
  MessageCircleQuestion } from 'lucide-react'
import { create } from 'zustand'
import { campusApi, type GridReadiness, type GridReadinessRow, type GridReadinessVerdict }
  from '../../api/campusElectrical'
import { nk } from '../../utils/queryKeys'
import { useUIStore } from '../../store/uiStore'
import { useChatReadiness } from '../../hooks/useChatProfiles'
import { ask, ASK_TITLE } from '../../pages/hubDesign/delegate'
import { NO_ASSISTANT } from '../../pages/hubDesign/shared/CardShell'

export type ScorecardMode = 'expert' | 'guided'

/** The project flow's section labels (design §2.1), for the row groups. */
export const SECTION_LABELS: Record<string, string> = {
  project: 'Project & site',
  carriers: 'Carriers & processes',
  connection: 'Connection & grid',
  loads: 'Loads & demand',
  assets: 'Assets & sizing',
  profiles: 'Time series & profiles',
  commercial: 'Commercial & parties',
  scenarios: 'Scenarios & optimisation',
  readiness: 'Readiness → run',
  results: 'Results → reports',
}

/** The inputs that are a field a user can edit in Expert today. A ledger
 *  item id (connection_ramp, oscillation) is not one until B2 adds it. */
const EXPERT_FIELDS: Record<string, string> = {
  efficiency: 'Efficiency',
  eh_sk_mva: 'Short-circuit level (MVA)',
  target_lole_h: 'LOLE target',
  p_nom: 'Capacity',
}

const VERDICT: Record<GridReadinessVerdict, { word: string; Icon: typeof CheckCircle2; cls: string }> = {
  pass: { word: 'Pass', Icon: CheckCircle2, cls: 'bg-success/10 text-success border-success/25' },
  warn: { word: 'Warn', Icon: AlertTriangle, cls: 'bg-warn/10 text-warn border-warn/25' },
  fail: { word: 'Fail', Icon: AlertCircle, cls: 'bg-danger/10 text-danger border-danger/25' },
  unknown: { word: 'Unknown', Icon: HelpCircle, cls: 'bg-bg-2 text-muted border-border' },
}

/** Which groups and rows are open: kept across remounts, so the Issues
 *  panel reopens as the user left it (UX review). */
interface OpenState {
  open: Record<string, boolean>
  toggle: (key: string, fallback: boolean) => void
}
export const useScorecardOpen = create<OpenState>(set => ({
  open: {},
  toggle: (key, fallback) => set(s => ({ open: { ...s.open, [key]: !(s.open[key] ?? fallback) } })),
}))

export function useGridReadiness(enabled = true) {
  const project = useUIStore(s => s.currentProject)
  return useQuery({
    queryKey: nk(project, 'grid_readiness'),
    queryFn: () => campusApi.gridReadiness(project ?? ''),
    enabled: !!project && enabled,
    staleTime: 30_000,
  })
}

function fmt(v: GridReadinessRow['limit'] | GridReadinessRow['project_value'], unit: string | null): string {
  if (v == null) return 'not known'
  if (typeof v === 'number') return `${Number(v.toPrecision(4))}${unit ? ` ${unit}` : ''}`
  if (typeof v === 'string') return v
  const lole = v as { lole_h_per_year?: number; horizon_years?: number }
  if (typeof lole.lole_h_per_year === 'number') {
    return `${Number(lole.lole_h_per_year.toPrecision(3))} h/yr over ${lole.horizon_years ?? '?'} year(s)`
  }
  return JSON.stringify(v)
}

export function VerdictChip({ verdict }: { verdict: GridReadinessVerdict }) {
  const { word, Icon, cls } = VERDICT[verdict]
  return (
    <span data-testid="gr-verdict" data-verdict={verdict}
      className={`shrink-0 inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium ${cls}`}>
      <Icon size={11} aria-hidden /> {word}
    </span>
  )
}

/** The summary line: "red flags" in Expert and the report, plain words in Guided. */
export function summaryText(s: GridReadiness['summary'], mode: ScorecardMode): string {
  if (mode === 'guided') {
    return `${s.red_flags} ${s.red_flags === 1 ? 'thing' : 'things'} to look at, ${s.unknown} not yet known`
  }
  return `${s.red_flags} red ${s.red_flags === 1 ? 'flag' : 'flags'} · ${s.unknown} unknown · ${s.assumed} assumed`
}

function editableIn(row: GridReadinessRow, labels: Record<string, string>): string | null {
  const t = row.target
  if (!t.field || !(t.field in EXPERT_FIELDS)) return null
  const field = labels[t.field] ?? EXPERT_FIELDS[t.field]
  return [SECTION_LABELS[t.section] ?? t.section, t.entity, field].filter(Boolean).join(' › ')
}

function ScoreRow({ row, mode, onJump, fieldLabels, noAssistant }: {
  row: GridReadinessRow; mode: ScorecardMode
  onJump: (type: string, name: string) => void
  fieldLabels: Record<string, string>; noAssistant: boolean
}) {
  const key = `row:${row.id}`
  const open = useScorecardOpen(s => s.open[key] ?? false)
  const toggle = useScorecardOpen(s => s.toggle)
  const where = editableIn(row, fieldLabels)
  const t = row.target
  const missing = row.verdict === 'unknown' && row.note
  return (
    <li data-testid={`gr-row-${row.id}`} className="flex flex-col gap-1 px-4 py-2.5 border-t border-border first:border-t-0">
      <div className="flex flex-wrap items-start gap-2">
        <VerdictChip verdict={row.verdict} />
        <div className="flex-1 min-w-[14rem] text-[12px]">
          <div className="font-semibold text-text">{row.requirement}</div>
          {missing && <div data-testid="gr-missing" className="text-muted">{row.note}</div>}
          <div className="text-muted">
            Limit: <span className="text-text">{fmt(row.limit, row.unit)}</span>
            {row.assumed && (
              <span data-testid="gr-assumed"
                className="ml-1 rounded border border-warn/30 bg-warn/5 px-1 text-[10px] text-warn">assumed</span>
            )}
            <span className="mx-1.5">·</span>
            Project: <span className="text-text">{fmt(row.project_value, row.unit)}</span>
          </div>
        </div>
        {mode === 'expert' && t.component && t.entity && (
          <button type="button" data-testid={`gr-jump-${row.id}`} onClick={() => onJump(t.component!, t.entity!)}
            title={`Open ${t.component} "${t.entity}" in the editor`}
            className="shrink-0 flex items-center gap-1 rounded-md border border-accent/30 bg-accent/5 px-2 py-1 text-[10.5px] font-medium text-accent hover:bg-accent/10">
            Go to field <ArrowRight size={11} />
          </button>
        )}
        {mode === 'guided' && (
          <button type="button" data-testid={`gr-ask-${row.id}`}
            onClick={() => ask(`On the grid-readiness scorecard, explain "${row.requirement}": `
              + `why it reads ${VERDICT[row.verdict].word.toLowerCase()}, what the limit and its source are, `
              + 'and what I could change.')}
            disabled={noAssistant} title={noAssistant ? NO_ASSISTANT : ASK_TITLE}
            className="shrink-0 inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] text-muted hover:border-accent hover:text-accent disabled:opacity-50 disabled:hover:border-border disabled:hover:text-muted">
            <MessageCircleQuestion size={12} /> Ask the assistant about this
          </button>
        )}
        <button type="button" data-testid={`gr-more-${row.id}`} aria-expanded={open}
          onClick={() => toggle(key, false)}
          className="shrink-0 text-[11px] text-muted hover:text-accent inline-flex items-center gap-0.5">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {open ? 'Less' : 'Source'}
        </button>
      </div>
      {open && (
        <div data-testid={`gr-detail-${row.id}`} className="ml-[4.5rem] flex flex-col gap-0.5 text-[11px] text-muted">
          <div><span className="text-text">Source:</span> {row.source}</div>
          {row.note && !missing && <div>{row.note}</div>}
          {mode === 'guided' && where && (
            <div data-testid={`gr-editable-${row.id}`}>Editable in Expert: {where}</div>
          )}
        </div>
      )}
    </li>
  )
}

function Group({ id, title, rows, mode, onJump, fieldLabels, noAssistant, defaultOpen }: {
  id: string; title: string; rows: GridReadinessRow[]; mode: ScorecardMode
  onJump: (type: string, name: string) => void; fieldLabels: Record<string, string>
  noAssistant: boolean; defaultOpen: boolean
}) {
  const key = `group:${mode}:${id}`
  const open = useScorecardOpen(s => s.open[key] ?? defaultOpen)
  const toggle = useScorecardOpen(s => s.toggle)
  if (!rows.length) return null
  return (
    <div data-testid={`gr-group-${id}`} className="border-t border-border first:border-t-0">
      <button type="button" aria-expanded={open} onClick={() => toggle(key, defaultOpen)}
        className="w-full flex items-center gap-1.5 px-4 py-2 bg-bg-2 text-left text-[11.5px] font-semibold text-text">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {title}
        <span className="font-mono font-normal text-muted">{rows.length}</span>
      </button>
      {open && (
        <ul className="flex flex-col">
          {rows.map(r => <ScoreRow key={r.id} row={r} mode={mode} onJump={onJump}
            fieldLabels={fieldLabels} noAssistant={noAssistant} />)}
        </ul>
      )}
    </div>
  )
}

/** The scorecard body. `fieldLabels` maps a target field to the template
 *  ledger's label (Guided's "Editable in Expert" line), so no item id shows. */
export function GridReadinessScorecard({ data, mode, onJump = () => {}, fieldLabels = {} }: {
  data: GridReadiness; mode: ScorecardMode
  onJump?: (type: string, name: string) => void
  fieldLabels?: Record<string, string>
}) {
  const noAssistant = useChatReadiness().ready === false
  const screening = data.rows.filter(r => !r.id.startsWith('dynamic:'))
  const dynamic = data.rows.filter(r => r.id.startsWith('dynamic:'))
  const sections = [...new Set(screening.map(r => r.section))]
  return (
    <div data-testid="grid-readiness-scorecard" className="flex flex-col">
      <div className="flex flex-col gap-0.5 px-4 py-2.5 text-[12px]">
        <div className="text-text">
          Screening against <span data-testid="gr-profile" className="font-semibold">
            {data.profile.title ?? data.profile.id}</span>
        </div>
        <div data-testid="gr-summary" className="text-muted">{summaryText(data.summary, mode)}</div>
        <div className="text-[11px] text-muted">Advisory: nothing here blocks Run.</div>
        {data.profile.error && (
          <div data-testid="gr-profile-error" className="text-[11px] text-warn">{data.profile.error}</div>
        )}
      </div>
      {sections.map(s => (
        <Group key={s} id={s} title={SECTION_LABELS[s] ?? s} mode={mode} onJump={onJump}
          rows={screening.filter(r => r.section === s)} fieldLabels={fieldLabels}
          noAssistant={noAssistant} defaultOpen />
      ))}
      <Group id="dynamic" title="Needs a dynamic (EMT) study" rows={dynamic} mode={mode} onJump={onJump}
        fieldLabels={fieldLabels} noAssistant={noAssistant} defaultOpen={false} />
    </div>
  )
}
