// "How robust" (spec §3 screen 10, plan S8): the fixed-size tornado, bars
// sorted by swing, each the battery NPV at the driver's low and high value
// with the sizes held fixed. Drivers never reached (`robustness.pending`) and
// drivers skipped (`robustness.skipped`, key -> code) are named in words.
import type { Findings, LedgerRow, StudyError, TornadoRecord } from '../../api/decisionStudies'
import { HELP, RUN_STATUS_LABELS, helpFor } from '../../utils/decisionVocabulary'
import { sortTornado, valueText } from './decisionModel'
import { Banner, Button, Card, Refusal } from './DecisionUi'

export default function Robust({ findings, tornado, error, onStart, onAbort, busy, ledgerRows = [] }: {
  findings: Findings
  ledgerRows?: LedgerRow[]
  tornado: TornadoRecord | null
  error: StudyError | null
  onStart: () => void
  onAbort: () => void
  busy: boolean
}) {
  const rob = findings.robustness
  const rows = sortTornado(rob.tornado)
  const labelOf = (key: string) => rob.tornado.find(r => r.key === key)?.label
    ?? ledgerRows.find(r => r.key === key)?.label
    ?? key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
  const vals = rows.flatMap(r => [r.npv_low, r.npv_high, rob.npv_centre]).filter((v): v is number => v != null)
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals)
  const pos = (v: number) => ((v - lo) / (hi - lo || 1)) * 100
  const running = tornado?.status === 'running'
  return (
    <div className="flex flex-col gap-4">
      <Card title="Run the robustness check" right={running
        ? <Button kind="danger" onClick={onAbort}>Stop</Button>
        : <Button kind="primary" onClick={onStart} disabled={busy}>{rob.tornado.length ? 'Run it again' : 'Run it'}</Button>}>
        <p data-testid="robust-budget">
          Each key driver is moved to the low and high end of its range with the battery size held fixed. The cost
          and rate drivers need no solve; each price driver needs two re-dispatches (four with PV). The worst case is
          checked against the study’s solve budget before the first solve, and each solve is charged as it is made.
        </p>
        {tornado && (
          <p className="text-muted">
            Last check: {RUN_STATUS_LABELS[tornado.status]}
            {tornado.solves_charged != null && ` · ${tornado.solves_charged} solve(s) charged`}
            {running && tornado.current ? ` · now: ${labelOf(tornado.current)}` : ''}
          </p>
        )}
        {error && <Refusal error={error} testId="robust-refusal" />}
      </Card>

      {rob.note && (
        <Banner tone={rob.status === 'ok' ? 'info' : 'warn'} testId="robust-note" title={helpFor(rob.note).text} />
      )}

      {rows.length > 0 && (
        <Card title="Battery NPV at each driver’s low and high value">
          <p className="text-[11px] text-muted">{HELP.tornado_method_redispatch_fixed_sizes} Centre: {valueText(rob.npv_centre, 'EUR')}.</p>
          <ul className="flex flex-col gap-2">
            {rows.map(r => {
              const a = r.npv_low, b = r.npv_high
              const left = a != null && b != null ? pos(Math.min(a, b)) : 0
              const width = a != null && b != null ? Math.max(1, pos(Math.max(a, b)) - left) : 0
              return (
                <li key={r.key} data-testid="tornado-bar" data-key={r.key} className="grid grid-cols-[14rem_1fr] gap-2 items-center">
                  <span>{r.label}<br />
                    <span className="text-[10.5px] text-muted font-mono">
                      {valueText(r.low_value, r.unit)} → {valueText(r.high_value, r.unit)} · swing {valueText(r.swing, 'EUR')}
                    </span>
                  </span>
                  <span className="relative h-4 rounded-sm bg-panel" aria-label={`NPV ${valueText(a, 'EUR')} to ${valueText(b, 'EUR')}`}>
                    <span className="absolute inset-y-0 bg-accent/70 rounded-sm" style={{ left: `${left}%`, width: `${width}%` }} />
                    {lo < 0 && <span className="absolute inset-y-0 w-px bg-text" style={{ left: `${pos(0)}%` }} />}
                  </span>
                  <span data-testid="tornado-values" className="col-span-2 text-[11px] font-mono">
                    NPV at the low value: {valueText(a, 'EUR')} · at the high value: {valueText(b, 'EUR')}
                  </span>
                  {r.notes.length > 0 && <span className="col-span-2 text-[10.5px] text-muted">{r.notes.map(n => helpFor(n).text).join(' ')}</span>}
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      {rob.pending.length > 0 && (
        <Card title="Drivers the check did not reach">
          <ul data-testid="robust-pending" className="list-disc pl-5">
            {rob.pending.map(k => <li key={k}>{labelOf(k)}</li>)}
          </ul>
        </Card>
      )}

      {Object.keys(rob.skipped).length > 0 && (
        <Card title="Drivers the check skipped, and why">
          <ul data-testid="robust-skipped" className="flex flex-col gap-1.5">
            {Object.entries(rob.skipped).map(([k, code]) => (
              <li key={k} data-code={code}>
                <strong>{labelOf(k)}</strong>: {helpFor(code).text}
                <span className="ml-1 font-mono text-[10px] text-muted">{code}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
