// "Confirm no backup obligation" (SCU06 K8b, UX 3 term 2): the storages the user
// confirms hold no backup energy for the site, `commercial.reserve_no_backup_storages`.
// Saved through the commercial config's one save path; the server refuses a storage
// that holds a backup role (a backup string, a spare, ride-through storage), and that
// refusal shows as its plain sentence. The ticks are a draft until saved, so a market
// pack apply that reloads the config keeps them.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi } from '../../../api/commercial'
import { networkApi } from '../../../api/network'
import type { CommercialConfig } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { ResultsWhere } from './marketPackLinks'
import { refusalText } from './marketPackWords'

const same = (a: string[], b: string[]) => a.length === b.length && a.every(x => b.includes(x))

export default function NoBackupChecklist({ cfg }: { cfg: CommercialConfig }) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const storages = useQuery({ queryKey: nk(project, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const saved = cfg.reserve_no_backup_storages ?? []
  // null: no unsaved change, so the list follows the saved config.
  const [draft, setDraft] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const ticked = draft ?? saved
  const dirty = draft !== null && !same(draft, saved)

  const toggle = (name: string, on: boolean) => {
    setMessage(null)
    setDraft(on ? [...ticked.filter(s => s !== name), name] : ticked.filter(s => s !== name))
  }
  const save = async () => {
    if (!draft) return
    setBusy(true)
    setMessage(null)
    try {
      // Only the storages ticked or unticked here change, on the list as stored
      // now, so a change saved elsewhere since this card's read is kept.
      const on = draft.filter(s => !saved.includes(s))
      const off = saved.filter(s => !draft.includes(s))
      const fresh = (await commercialApi.getCommercial())?.reserve_no_backup_storages ?? []
      const next = [...fresh.filter(s => !off.includes(s)), ...on.filter(s => !fresh.includes(s))]
      await commercialApi.saveCommercial({ reserve_no_backup_storages: next })
      setDraft(null)
      setMessage({ tone: 'ok', text: 'Saved.' })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
    } catch (e) {
      setMessage({ tone: 'error', text: `Not saved. ${refusalText(e)}` })
    } finally {
      setBusy(false)
    }
  }

  const names = (storages.data ?? []).map(s => s.name)
  // A saved name the network no longer has stays listed, so it can be unticked.
  const listed = [...names, ...ticked.filter(s => !names.includes(s))]
  return (
    <fieldset className="space-y-1" data-testid="mp-no-backup">
      <legend className="text-[11px] font-semibold">Confirm no backup obligation</legend>
      <p className="text-[11px] text-muted">
        Tick a storage only if it holds no backup energy for the site. A ticked storage may offer
        reserves down to empty, and a product that names no storage may use it. A backup string,
        a spare or ride-through storage cannot be ticked.</p>
      {storages.isError && <p className="text-[11px] text-warn">The project's storage could not be listed.</p>}
      {storages.isSuccess && listed.length === 0 && (
        <p className="text-[11px] text-muted" data-testid="mp-no-backup-none">The project has no storage.</p>)}
      {listed.map(name => (
        <label key={name} className="flex items-center gap-2 text-[11px]">
          <input type="checkbox" checked={ticked.includes(name)} disabled={busy}
                 data-testid={`mp-no-backup-${name}`} onChange={e => toggle(name, e.target.checked)} />
          <span>{name}{!names.includes(name) && storages.isSuccess
            ? <span className="text-muted"> (no longer in the project)</span> : null}</span>
        </label>
      ))}
      <div className="flex items-center gap-2 text-[11px]">
        <button type="button" disabled={!dirty || busy} data-testid="mp-no-backup-save"
                className="border border-border rounded px-2 py-0.5 disabled:opacity-40"
                onClick={() => { void save() }}>Save</button>
        <button type="button" disabled={!dirty || busy} data-testid="mp-no-backup-discard"
                className="px-2 py-0.5 disabled:opacity-40"
                onClick={() => { setDraft(null); setMessage(null) }}>Discard</button>
        <span className={dirty ? 'text-warn' : 'text-muted'} data-testid="mp-no-backup-state">
          {dirty ? 'Unsaved changes' : 'Saved'}</span>
      </div>
      {message && <p role={message.tone === 'error' ? 'alert' : 'status'} data-testid="mp-no-backup-message"
                     className={`text-[11px] ${message.tone === 'error' ? 'text-danger' : 'text-success'}`}>
        {message.text}{message.tone === 'ok' && <> <ResultsWhere /></>}</p>}
    </fieldset>
  )
}
