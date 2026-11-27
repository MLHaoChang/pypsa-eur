// One name, unit and basis per result figure (guided-results 04): the backend's
// `services/figure_words.json`, read as is, so Guided, Expert, the reports and
// the harness print the same words. `{currency}` and `{money}` are filled here
// the way services/figure_words.py fills them.
import source from '../../../backend/services/figure_words.json'

export type FigureId = keyof typeof source.figures

interface Figure { name: string; unit: string; tag?: string; basis?: string }

const FIGURES: Record<FigureId, Figure> = source.figures

const fill = (text: string, currency?: string | null, money?: string | null) =>
  text.replace(/\{currency\}/g, currency || 'report currency').replace(/\{money\}/g, money || '')

/** The figure's one name, e.g. "Expected shortfall". */
export const figureName = (id: FigureId): string => FIGURES[id].name

/** Its short tag for details and tooltips (owner decision GR04-SHORTFALL-1: "LOLE"), if any. */
export const figureTag = (id: FigureId): string | undefined => FIGURES[id].tag

export const figureUnit = (id: FigureId, currency?: string | null): string => fill(FIGURES[id].unit, currency)

/** The figure's basis line, e.g. what an NPV discounts. */
export const figureBasis = (id: FigureId, currency?: string | null, money?: string | null): string | undefined => {
  const b = FIGURES[id].basis
  return b == null ? undefined : fill(b, currency, money)
}

/** What the figure is, for a tooltip; it names the tag (e.g. LOLE) once. */
export function figureHint(id: FigureId): string {
  const f = FIGURES[id]
  const basis = figureBasis(id) ?? ''
  return f.tag && !basis.includes(f.tag) ? `${f.tag}: ${basis}` : basis
}
