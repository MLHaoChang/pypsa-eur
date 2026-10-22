// The weather bar (RP-04b, spec R3): the one editor of the weather source and
// year every renewable generation reads (per-generator Generate, the bulk
// default, Profile Studio, the assistant). Saved in the project. The time basis
// is shown read-only: it is set in the site connection.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { errorText, profilesApi, type ProfileSource } from '../../api/profiles'
import { nk } from '../../utils/queryKeys'
import { Seg } from '../PageKit'
import { openSiteConnection } from '../../pages/economics/SiteConnectionLink'

const YEAR_WORDS = {
  saved: 'your choice',
  snapshots: 'from your snapshots',
  default: 'default: the last completed year',
} as const

export const weatherKey = (project: string | null) => nk(project, 'profile_weather')

export function WeatherBar({ project }: { project: string }) {
  const qc = useQueryClient()
  const weather = useQuery({ queryKey: weatherKey(project), queryFn: () => profilesApi.weather(project) })
  const save = useMutation({
    mutationFn: (c: { source: ProfileSource; year: number }) => profilesApi.saveWeather(project, c),
    onSuccess: data => { qc.setQueryData(weatherKey(project), data) },
  })
  const w = weather.data
  const [draft, setDraft] = useState('')
  useEffect(() => { if (w) setDraft(String(w.year)) }, [w?.year]) // eslint-disable-line react-hooks/exhaustive-deps

  if (weather.isPending) return <p className="px-3 py-2 text-[11px] text-muted">Loading the weather choice…</p>
  if (!w) return <p className="px-3 py-2 text-[11px] text-warn">The weather choice could not be read.</p>

  const commitYear = () => {
    const y = Number(draft)
    if (Number.isInteger(y) && y !== w.year) save.mutate({ source: w.source, year: y })
    else setDraft(String(w.year))
  }
  return (
    <div data-testid="weather-bar" className="px-3 py-2 border-b border-border/40 bg-bg text-[11px] space-y-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-text">Weather for generated profiles</span>
        <Seg value={w.source} onChange={source => save.mutate({ source, year: w.year })}
          options={[{ value: 'synthetic', label: 'Synthetic' }, { value: 'atlite', label: 'ERA5' }]} />
        <label className="flex items-center gap-1">
          Year
          <input type="number" data-testid="weather-year" value={draft}
            onChange={e => setDraft(e.target.value)} onBlur={commitYear}
            onKeyDown={e => { if (e.key === 'Enter') commitYear() }}
            className="w-20 border border-border rounded px-1 py-0.5 bg-bg" />
        </label>
        <span className="text-muted" data-testid="weather-year-source">{YEAR_WORDS[w.year_source]}</span>
      </div>
      <p className="text-muted" data-testid="weather-time-basis">
        Snapshots are read as {w.time_basis.value}
        {w.time_basis.assumed ? ': no site time is set' : ''}.{' '}
        <button type="button" className="text-accent hover:underline" onClick={openSiteConnection}>
          Edit in Connection &amp; grid ›
        </button>
      </p>
      {save.isError && <p className="text-warn" data-testid="weather-error">{errorText(save.error)}</p>}
    </div>
  )
}
