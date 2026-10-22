// Which of an asset's scalar fields a series editor also writes, and whether
// a series is set for each (UX review, checklist §6.2): Properties shows a
// field with a series as read-only ("Time series set … Edit in Availability")
// and labels one without as the constant the editor starts from, so one field
// never has two editors that silently override each other.
import { useQueries } from '@tanstack/react-query'
import { seriesApi, type CurrentSeries } from '../../api/profiles'
import { provenanceWord } from './SeriesKit'

export interface SeriesField {
  /** The series' short description when one is set; null for the constant. */
  set: string | null
  /** The editor section that owns the series. */
  editor: string
}

export function describeSeries(c: CurrentSeries): string {
  const word = provenanceWord(c.provenance)
  const kind = word !== 'measured' && c.kind ? `, ${String(c.kind).replace(/_/g, ' ')}` : ''
  return `${word}${kind}${c.refinements ? ', refined' : ''}`
}

export function useSeriesFields(
  project: string | null, componentType: string, component: string,
  fields: Record<string, string>,
): Record<string, SeriesField> {
  const attrs = Object.keys(fields)
  const results = useQueries({
    queries: attrs.map(attribute => ({
      queryKey: ['series-current', project, componentType, attribute, component],
      queryFn: () => seriesApi.current(project as string, componentType, attribute, component),
      enabled: !!project,
      staleTime: 30_000,
    })),
  })
  const out: Record<string, SeriesField> = {}
  attrs.forEach((attribute, i) => {
    const data = results[i]?.data
    out[attribute] = { set: data?.exists ? describeSeries(data) : null, editor: fields[attribute] }
  })
  return out
}

/** Which of a generator's series have an editor, by the server's category
 *  (the Time Series tab the generator sits on, so Properties and Time Series
 *  agree on its one entry): a renewable's or demand response's profile has
 *  none, its row upload is the entry; demand response's activation cost has
 *  none either. Null while the category loads. */
export function generatorSeriesEditors(category: string | undefined): {
  availability: boolean
  price: boolean
  fields: Record<string, string>
} | null {
  if (category === undefined) return null
  if (category === 'dr') {
    return { availability: false, price: false,
      fields: { p_max_pu: 'Time Series', p_min_pu: 'Time Series', marginal_cost: 'Time Series' } }
  }
  if (category === 'renewable') {
    return { availability: false, price: true,
      fields: { p_max_pu: 'Time Series', p_min_pu: 'Time Series', marginal_cost: 'Price series' } }
  }
  return { availability: true, price: true,
    fields: { p_max_pu: 'Availability', p_min_pu: 'Availability', marginal_cost: 'Price series' } }
}
