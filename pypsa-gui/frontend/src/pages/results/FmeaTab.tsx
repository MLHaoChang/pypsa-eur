// The FMEA worksheet (Phase 3 Task 2) — spec §§4.2, 8.3. One table:
// engine-computed rows (regenerated from /results/copt on every view) and
// expert class-D rows (persisted in the per-project sidecar), interleaved on
// one €/yr criticality ranking. No RPN, no Action Priority (decided v2).
// Computed rows expose exactly one editable cell — mitigability — whose value
// lives in the sidecar's overlays and therefore survives re-solves.
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Download, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { downloadCSV } from './shared'
import { SortHeader, TableSearchBox, useFilterableTable } from './useFilterableTable'
import {
  buildManualRow,
  mergeWorksheet,
  WORKSHEET_CSV_HEADER,
  worksheetCsvRows,
  type ModesPayload,
  type WorksheetRow,
  type WorksheetSidecar,
} from './fmea'

const FIDELITY_TIP: Record<string, string> = {
  analytic_convolution:
    'COPT screening (analytic convolution): thermal-only, storage-excluded, ' +
    'network-free. Not comparable to a statutory standard.',
  deterministic_scenario:
    'LP proxy (deterministic, perfect foresight, one realisation). Not ' +
    'comparable to a statutory standard.',
  expert_judgement: 'Expert-entered — not computed from the model.',
}

function EngineBadge({ engine, fidelity }: { engine: string; fidelity: string }) {
  const cls = engine === 'expert'
    ? 'bg-warn/10 text-warn'
    : 'bg-accent/10 text-accent'
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${cls}`}
      title={FIDELITY_TIP[fidelity] ?? fidelity}>
      {engine}
    </span>
  )
}

export default function FmeaTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()

  // All computed classes (A analytic + the last sweep's B/C) on one list;
  // polls while a sweep runs so rows land when it finishes.
  const { data: modes, refetch: refetchModes } = useQuery({
    queryKey: nk(currentProject, 'results', 'fmea_modes'),
    queryFn: () => resultsApi.getFmeaModes(),
    refetchInterval: q =>
      (q.state.data as { sweep_status?: string } | null)?.sweep_status === 'running'
        ? 2000 : false,
  })
  const sidecarKey = nk(currentProject, 'adequacy', 'worksheet')
  const { data: sidecar } = useQuery({
    queryKey: sidecarKey,
    queryFn: () => resultsApi.getWorksheet(currentProject ?? ''),
    enabled: !!currentProject,
  })

  const save = useMutation({
    mutationFn: (next: { manual_rows: Array<Record<string, unknown>>; overlays: WorksheetSidecar['overlays'] }) =>
      resultsApi.putWorksheet(currentProject ?? '', next),
    onSuccess: () => qc.invalidateQueries({ queryKey: sidecarKey }),
    onError: (e: Error) => toast.error(`Worksheet save failed: ${e.message}`),
  })

  const sc = (sidecar ?? null) as WorksheetSidecar | null
  const rows = useMemo(
    () => mergeWorksheet((modes ?? null) as ModesPayload | null, sc),
    [modes, sc],
  )

  // Class B/C need LP re-solves — run on demand, budget-guarded server-side.
  const sweep = useMutation({
    mutationFn: async () => {
      const reg = currentProject
        ? await resultsApi.getStressScenarios(currentProject).catch(() => null)
        : null
      return resultsApi.postFmeaSweep(reg?.scenarios ?? [])
    },
    onSuccess: () => { void refetchModes() },
    onError: (e: Error) => toast.error(`Sweep failed to start: ${e.message}`),
  })
  const sweepRunning =
    (modes as ModesPayload | null | undefined)?.sweep_status === 'running' ||
    sweep.isPending
  type SortK = 'name' | 'occurrence_per_year' | 'severity_eur' | 'criticality_eur_per_year'
  const { rows: filtered, search, setSearch, sortKey, sortDir, onSortClick } =
    useFilterableTable<WorksheetRow, SortK>({
      rows,
      initialSortKey: 'criticality_eur_per_year',
      initialSortDir: 'desc',
      getValue: (r, k) => r[k],
      getSearchText: r =>
        `${r.name} ${r.mode_id} ${r.mitigability} ${r.component_class}`.toLowerCase(),
    })

  // Mitigability edits: computed rows → overlays; manual rows → the row.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const commitMitigability = (row: WorksheetRow, value: string) => {
    if (!sc) return
    if (row.editable) {
      const manual_rows = sc.manual_rows.map(m =>
        (m as { mode_id?: unknown }).mode_id === row.mode_id
          ? { ...m, mitigability: value }
          : m)
      save.mutate({ manual_rows, overlays: sc.overlays })
    } else {
      const overlays = { ...sc.overlays }
      if (value.trim()) overlays[row.mode_id] = { ...overlays[row.mode_id], mitigability: value }
      else delete overlays[row.mode_id]
      save.mutate({ manual_rows: sc.manual_rows, overlays })
    }
  }

  const deleteManual = (row: WorksheetRow) => {
    if (!sc) return
    save.mutate({
      manual_rows: sc.manual_rows.filter(
        m => (m as { mode_id?: unknown }).mode_id !== row.mode_id),
      overlays: sc.overlays,
    })
  }

  const [form, setForm] = useState({ name: '', occ: '', sev: '', mit: '' })
  const addManual = () => {
    if (!sc || !form.name.trim()) return
    const row = buildManualRow({
      name: form.name,
      occurrencePerYear: parseFloat(form.occ) || 0,
      severityEur: parseFloat(form.sev) || 0,
      mitigability: form.mit || undefined,
    })
    save.mutate({ manual_rows: [...sc.manual_rows, row], overlays: sc.overlays })
    setForm({ name: '', occ: '', sev: '', mit: '' })
  }

  const exportCsv = () =>
    downloadCSV('fmea_worksheet.csv', WORKSHEET_CSV_HEADER, worksheetCsvRows(rows))

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-3">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          FMEA worksheet
        </h3>
        <p className="text-[11px] text-muted mt-1">
          Failure modes ranked by €/yr criticality — engine-computed rows
          regenerate on every view; expert rows and mitigability notes persist
          with the project and survive re-solves. No number here is comparable
          to a statutory reliability standard (hover a badge for each row's
          fidelity).
        </p>
      </header>

      <div className="flex items-center gap-2">
        <TableSearchBox value={search} onChange={setSearch} placeholder="Filter modes…" />
        <button onClick={() => sweep.mutate()} disabled={sweepRunning}
          title="Re-solves each eligible link outage (class B) and each stress scenario (class C) with capacities frozen — several LP solves; the network ends back in its base state."
          className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent transition-colors disabled:opacity-50">
          <RefreshCw size={11} className={sweepRunning ? 'animate-spin' : ''} />
          {sweepRunning ? 'Sweeping…' : 'Run B/C sweep'}
        </button>
        {(modes as ModesPayload | null | undefined)?.sweep_error && (
          <span className="text-[10px] text-danger">
            {(modes as ModesPayload).sweep_error}
          </span>
        )}
        <button onClick={exportCsv}
          className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent transition-colors">
          <Download size={11} /> CSV
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="text-[11px] text-muted">
          No failure modes yet. Computed rows appear once generators carry
          outage data (Properties → Adequacy); expert rows can be added below.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[10.5px]">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <SortHeader label="Mode" columnKey="name" sortKey={sortKey} sortDir={sortDir} onClick={onSortClick("name")} />
                <th className="py-1 pr-2">Class</th>
                <SortHeader label="Occurrence (/yr)" columnKey="occurrence_per_year" sortKey={sortKey} sortDir={sortDir} onClick={onSortClick("occurrence_per_year")} />
                <SortHeader label="Severity (€)" columnKey="severity_eur" sortKey={sortKey} sortDir={sortDir} onClick={onSortClick("severity_eur")} />
                <SortHeader label="Criticality (€/yr)" columnKey="criticality_eur_per_year" sortKey={sortKey} sortDir={sortDir} onClick={onSortClick("criticality_eur_per_year")} />
                <th className="py-1 pr-2">Mitigability</th>
                <th className="py-1 pr-2">Provenance</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => (
                <tr key={r.mode_id} className="border-b border-border/40">
                  <td className="py-1 pr-2 font-mono">{r.name}
                    {!r.in_metric_scope && (
                      <span className="ml-1 text-[9px] text-muted"
                        title="Outside the electricity-only availability metric — reported, not counted.">
                        (out of scope)
                      </span>
                    )}
                  </td>
                  <td className="py-1 pr-2">{r.failure_class}</td>
                  <td className="py-1 pr-2 font-mono">{r.occurrence_per_year.toFixed(2)}
                    <span className="text-muted"> {r.occurrence_basis}</span></td>
                  <td className="py-1 pr-2 font-mono">{r.severity_eur.toFixed(0)}</td>
                  <td className="py-1 pr-2 font-mono font-semibold">{r.criticality_eur_per_year.toFixed(0)}</td>
                  <td className="py-1 pr-2">
                    <input
                      className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] w-44 focus:outline-none focus:border-accent"
                      value={drafts[r.mode_id] ?? r.mitigability}
                      placeholder="—"
                      onChange={e => setDrafts(p => ({ ...p, [r.mode_id]: e.target.value }))}
                      onBlur={e => {
                        if (e.target.value !== r.mitigability) commitMitigability(r, e.target.value)
                        setDrafts(p => { const { [r.mode_id]: _drop, ...rest } = p; return rest })
                      }}
                    />
                  </td>
                  <td className="py-1 pr-2"><EngineBadge engine={r.engine} fidelity={r.fidelity} /></td>
                  <td className="py-1 text-right">
                    {r.editable && (
                      <button onClick={() => deleteManual(r)} title="Delete expert row"
                        className="text-muted hover:text-danger transition-colors">
                        <Trash2 size={11} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="border border-border rounded p-2 mt-1">
        <p className="text-[10px] font-semibold text-muted uppercase tracking-wide mb-1.5">
          Add expert failure mode (class D)
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <input className="bg-bg border border-border rounded px-2 py-1 text-[10.5px] w-44 focus:outline-none focus:border-accent"
            placeholder="Name (e.g. fuel supply loss)" value={form.name}
            onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
          <input className="bg-bg border border-border rounded px-2 py-1 text-[10.5px] w-28 font-mono focus:outline-none focus:border-accent"
            type="number" step="any" placeholder="events/yr" value={form.occ}
            onChange={e => setForm(p => ({ ...p, occ: e.target.value }))} />
          <input className="bg-bg border border-border rounded px-2 py-1 text-[10.5px] w-28 font-mono focus:outline-none focus:border-accent"
            type="number" step="any" placeholder="severity €" value={form.sev}
            onChange={e => setForm(p => ({ ...p, sev: e.target.value }))} />
          <input className="bg-bg border border-border rounded px-2 py-1 text-[10.5px] w-44 focus:outline-none focus:border-accent"
            placeholder="mitigability (optional)" value={form.mit}
            onChange={e => setForm(p => ({ ...p, mit: e.target.value }))} />
          <button onClick={addManual} disabled={!form.name.trim() || save.isPending}
            className="inline-flex items-center gap-1 px-2 py-1 bg-accent text-white rounded text-[10px] font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50">
            <Plus size={11} /> Add
          </button>
        </div>
        <p className="text-[9.5px] text-muted mt-1.5">
          Criticality is computed as occurrence × severity. Expert rows carry
          their own provenance badge and never impersonate an engine.
        </p>
      </div>
    </div>
  )
}
