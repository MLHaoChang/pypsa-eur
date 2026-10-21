// "Prices the optimisation uses" (IPP ticket 07; UX reviewer 2026-10-20): the export and the
// wholesale import price series, section 7a's `lp_input` prices (#447 §2.0), above the import
// tariff in the one tariff editor (TariffSection), so both of its mounts show this one block.
// Saved through the commercial config's one save path; the server aligns and refuses a series
// that does not cover every snapshot.
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi, libraryApi } from '../../../api/commercial'
import type { CommercialConfig, LibraryRef } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { SeriesSelect } from './SeriesSelect'

function Provenance({ value, testId }: { value: LibraryRef | null; testId: string }) {
  if (!value) return null
  const illustrative = /illustrative/i.test(value.source)
  return (
    <span className="text-[11px] text-muted" data-testid={testId}>
      Library series {value.id} v{value.version} · {value.source}
      {illustrative && <span className="text-warn"> (illustrative, not market data)</span>}
    </span>
  )
}

export default function MarketPricesBlock({ cfg }: { cfg: CommercialConfig }) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const series = useQuery({ queryKey: nk(project, 'library', 'series'),
                            queryFn: () => libraryApi.listSeries(), retry: false })
  const [exportRef, setExportRef] = useState<LibraryRef | null>(cfg.export_price_ref ?? null)
  const [importRef, setImportRef] = useState<LibraryRef | null>(cfg.import_price_ref ?? null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  useEffect(() => {
    setExportRef(cfg.export_price_ref ?? null)
    setImportRef(cfg.import_price_ref ?? null)
  }, [cfg.export_price_ref, cfg.import_price_ref])

  const refs = series.data ?? []
  const save = async () => {
    setMessage(null)
    const patch: Partial<CommercialConfig> = { import_price_ref: importRef }
    if (cfg.export_link) patch.export_price_ref = exportRef
    try {
      await commercialApi.saveCommercial(patch as never)
      setMessage({ tone: 'ok', text: 'The prices are saved; re-solve to use them.' })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
    } catch (e) {
      const r = (e as { response?: { data?: { detail?: unknown } } })?.response
      const detail = r?.data?.detail
      const text = typeof detail === 'string' ? detail
        : (detail as { message?: string } | undefined)?.message
          ?? (e instanceof Error ? e.message : 'refused')
      setMessage({ tone: 'error', text: `The prices are not saved: ${text}` })
    }
  }
  return (
    <section className="space-y-1 border border-border rounded p-2" data-testid="ic-market-prices">
      <h4 className="text-[11px] font-semibold">Prices the optimisation uses</h4>
      <p className="text-[11px] text-muted">
        Library price series (€/MWh). Read by the optimisation (it prices import and export).</p>
      {series.isError && <p className="text-[11px] text-warn">The Library's series could not be listed.</p>}
      {cfg.export_link && (
        <label className="flex items-center gap-2 flex-wrap">
          <span className="w-48">Export price (on {cfg.export_link})</span>
          <SeriesSelect label="Export price series" value={exportRef} series={refs}
                        onChange={setExportRef} />
          <Provenance value={exportRef} testId="ic-export-price-provenance" />
        </label>
      )}
      <label className="flex items-center gap-2 flex-wrap">
        <span className="w-48">Wholesale import price</span>
        <SeriesSelect label="Import price series" value={importRef} series={refs}
                      onChange={setImportRef} />
        <Provenance value={importRef} testId="ic-import-price-provenance" />
      </label>
      <button type="button" className="text-[11px] border border-border rounded px-2 py-0.5"
              onClick={save}>Save the prices</button>
      {message && <p role={message.tone === 'error' ? 'alert' : 'status'}
                     className={`text-[11px] ${message.tone === 'error' ? 'text-danger' : 'text-success'}`}>
        {message.text}</p>}
    </section>
  )
}
