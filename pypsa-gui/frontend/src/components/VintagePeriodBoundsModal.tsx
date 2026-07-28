import { useEffect, useId, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Layers, Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'

import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { Dialog } from './Dialog'

interface Props {
  componentClass: string  // "Generator" | "StorageUnit" | "Store" | "Link"
  name: string
  onClose: () => void
}

type Row = { period: string; min: string; max: string }

// Per-period capacity bounds editor. Renders one row per investment period
// (read from the network) with editable Min / Max numeric inputs. Save PUTs
// the whole asset's bounds dict; the backend drops rows with neither bound
// set, so emptying both fields effectively "removes" that period's row at
// save time without a separate per-row delete button.
// Stores measure capacity in MWh (energy reservoir); the other supported
// component classes use MW (power). Wire the unit label through everywhere
// the modal mentions a capacity number so the user isn't reading "MW" on a
// Store row whose underlying field is `e_nom_*` and therefore MWh.
const UNIT_FOR_CLASS: Record<string, string> = {
  Store: 'MWh',
  Generator: 'MW',
  StorageUnit: 'MW',
  Link: 'MW',
  // Lines and Transformers use s_nom (apparent power) — MVA in PyPSA's
  // strict reading, but the GUI labels everything as MW for consistency
  // with how the rest of the app displays s_nom values.
  Line: 'MW',
  Transformer: 'MW',
}

export default function VintagePeriodBoundsModal({ componentClass, name, onClose }: Props) {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const unit = UNIT_FOR_CLASS[componentClass] ?? 'MW'
  const titleId = useId()

  // Periods come from the multi-period config. Single-period networks have
  // an empty list — the modal then surfaces a hint to enable periods first
  // rather than rendering an empty table.
  const periodsQuery = useQuery({
    queryKey: nk(currentProject, 'investmentPeriods'),
    queryFn: networkApi.getInvestmentPeriods,
  })
  const periods = useMemo(() => {
    const ps = periodsQuery.data?.periods ?? []
    return [...ps].sort((a, b) => a - b)
  }, [periodsQuery.data])

  // Saved bounds for this asset only — load the global list and pluck.
  const boundsQuery = useQuery({
    queryKey: nk(currentProject, 'vintageBounds'),
    queryFn: networkApi.listVintageBounds,
  })
  const savedForAsset = useMemo(() => {
    return boundsQuery.data?.bounds?.[componentClass]?.[name] ?? {}
  }, [boundsQuery.data, componentClass, name])

  // Local edit buffer. Seeded once periods + saved bounds are loaded; the
  // user's edits aren't pushed to the server until they hit Save.
  const [rows, setRows] = useState<Row[]>([])
  const [seeded, setSeeded] = useState(false)
  useEffect(() => {
    if (seeded) return
    if (periodsQuery.isLoading || boundsQuery.isLoading) return
    const next: Row[] = periods.map(p => {
      const r = savedForAsset[String(p)] ?? {}
      return {
        period: String(p),
        min: r.p_nom_min != null ? String(r.p_nom_min) : '',
        max: r.p_nom_max != null ? String(r.p_nom_max) : '',
      }
    })
    setRows(next)
    setSeeded(true)
  }, [periodsQuery.isLoading, boundsQuery.isLoading, periods, savedForAsset, seeded])

  const setCell = (i: number, col: 'min' | 'max', v: string) => {
    setRows(prev => prev.map((r, idx) => idx === i ? { ...r, [col]: v } : r))
  }

  const saveMut = useMutation({
    mutationFn: () => {
      // Build the payload: rows with at least one bound become entries; rows
      // with neither are silently dropped (backend would drop them anyway).
      const bounds: Record<string, { p_nom_min?: number | null; p_nom_max?: number | null }> = {}
      for (const r of rows) {
        const minNum = r.min.trim() === '' ? null : Number(r.min)
        const maxNum = r.max.trim() === '' ? null : Number(r.max)
        if (minNum == null && maxNum == null) continue
        if (minNum != null && !Number.isFinite(minNum)) {
          throw new Error(`Period ${r.period}: Min "${r.min}" is not a number`)
        }
        if (maxNum != null && !Number.isFinite(maxNum)) {
          throw new Error(`Period ${r.period}: Max "${r.max}" is not a number`)
        }
        if (minNum != null && maxNum != null && minNum > maxNum) {
          throw new Error(`Period ${r.period}: Min (${minNum}) > Max (${maxNum})`)
        }
        const entry: { p_nom_min?: number; p_nom_max?: number } = {}
        if (minNum != null) entry.p_nom_min = minNum
        if (maxNum != null) entry.p_nom_max = maxNum
        bounds[r.period] = entry
      }
      return networkApi.updateVintageBounds(componentClass, name, bounds)
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'vintageBounds') })
      const n = Object.keys(res.bounds).length
      toast.success(n > 0
        ? `Saved per-period bounds for ${n} period(s)`
        : 'Cleared per-period bounds')
      onClose()
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(msg)
    },
  })

  const clearMut = useMutation({
    mutationFn: () => networkApi.deleteVintageBounds(componentClass, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'vintageBounds') })
      toast.success('Cleared all per-period bounds')
      onClose()
    },
  })

  const hasSaved = Object.keys(savedForAsset).length > 0

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      panelClassName="bg-bg rounded-xl shadow-2xl w-[560px] max-w-[95vw] max-h-[88vh] overflow-hidden flex flex-col"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <Layers size={15} className="text-accent" />
          <span id={titleId} className="text-sm font-semibold text-text">Per-period capacity bounds</span>
          <span className="text-[11px] text-muted font-mono">{componentClass} · {name}</span>
        </div>
        <button onClick={onClose} className="p-1 text-muted hover:text-text transition-colors">
          <X size={15} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 text-[12px]">
        <p className="text-muted mb-3 leading-relaxed">
          For each investment period, set a lower / upper bound on the capacity
          the optimiser can build in that period. Leave both empty for periods
          with no constraint. At solve time the asset is expanded into one
          vintage row per period (build_year = period), each carrying its own
          bounds — the optimiser then sizes each vintage independently.
        </p>

        {periods.length === 0 ? (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-amber-600 text-[11.5px]">
            No investment periods configured. Enable multi-period planning
            under <span className="font-semibold">Solver settings → Periods</span> first.
          </div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-[10.5px] text-muted uppercase tracking-wider">
                <th className="text-left py-1 font-medium">Period</th>
                <th className="text-left py-1 font-medium">Min ({unit})</th>
                <th className="text-left py-1 font-medium">Max ({unit})</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.period} className="border-t border-border">
                  <td className="py-1.5 font-mono">{r.period}</td>
                  <td className="py-1 pr-2">
                    <input
                      type="number"
                      value={r.min}
                      onChange={e => setCell(i, 'min', e.target.value)}
                      placeholder="—"
                      className="w-full px-2 py-1 border border-border rounded font-mono text-[11.5px] bg-bg"
                    />
                  </td>
                  <td className="py-1 pl-2">
                    <input
                      type="number"
                      value={r.max}
                      onChange={e => setCell(i, 'max', e.target.value)}
                      placeholder="—"
                      className="w-full px-2 py-1 border border-border rounded font-mono text-[11.5px] bg-bg"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 py-3 border-t border-border shrink-0">
        {hasSaved && (
          <button
            onClick={() => clearMut.mutate()}
            disabled={clearMut.isPending}
            className="flex items-center gap-1 text-[11.5px] text-muted hover:text-danger"
            title="Remove all saved per-period bounds for this asset"
          >
            <Trash2 size={12} />
            Clear all
          </button>
        )}
        <span className="flex-1" />
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-[12px] text-muted hover:text-text"
        >
          Cancel
        </button>
        <button
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending || periods.length === 0}
          className="px-3 py-1.5 text-[12px] bg-accent text-white rounded font-medium hover:opacity-90 disabled:opacity-40"
        >
          {saveMut.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Dialog>
  )
}
