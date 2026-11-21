/**
 * Parameter search over the catalogue (spec expert-catalogue §4.3): one index for
 * the ⌘K palette and the Parameter guide. A query matches the plain name, aliases,
 * short help, guide text and the model-file name. Ranking: exact plain name >
 * alias > model-file name > word prefix > short help > guide text; with a lens,
 * the entries that function needs come first.
 *
 * A hit by model-file name is flagged (`via: 'model'`), so a row can say "matches
 * the model-file name" without ever showing the raw name (RAW-NAMES-1).
 */
import type { CatalogueDepth, CatalogueEntry } from './api'

export type MatchVia = 'plain' | 'alias' | 'model' | 'prefix' | 'help' | 'guide'

export interface ParameterHit {
  entry: CatalogueEntry
  via: MatchVia
  score: number
}

const SCORE: Record<MatchVia, number> = { plain: 100, alias: 80, model: 70, prefix: 60, help: 30, guide: 20 }
const DEPTH_RANK: Record<CatalogueDepth, number> = { essentials: 0, basic: 1, advanced: 2, detail: 3 }
/** Below this many characters a query matches only names, never help or guide text. */
const MIN_TEXT_QUERY = 3

interface Row {
  entry: CatalogueEntry
  plain: string
  plainWords: string[]
  aliases: string[]
  aliasWords: string[][]
  model: string
  help: string
  helpWords: string[]
  guide: string
}

export interface ParameterIndex {
  search(query: string, opts?: { lens?: string | null; limit?: number }): ParameterHit[]
}

export const norm = (s: string): string => s.toLowerCase().replace(/\s+/g, ' ').trim()
/** The letter-and-digit words of a text, lower case. */
export const words = (s: string): string[] => norm(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean)

/** Markdown as plain text: code blocks, headings, emphasis and link targets dropped. */
export function plainText(md: string | null): string {
  if (!md) return ''
  return md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#*_`>|]/g, ' ')
    .replace(/\s+/g, ' ').trim()
}

const guideText = (md: string | null): string => norm(plainText(md))

/** Every query word starts some word of the text, in any order. */
function prefixMatch(q: string[], ws: string[]): boolean {
  return q.length > 0 && q.every(t => ws.some(w => w.startsWith(t)))
}

export function buildParameterIndex(entries: readonly CatalogueEntry[]): ParameterIndex {
  const rows: Row[] = entries.map(e => ({
    entry: e,
    plain: norm(e.plain_name),
    plainWords: words(e.plain_name),
    aliases: e.aliases.map(norm),
    aliasWords: e.aliases.map(words),
    model: e.model_name.toLowerCase(),
    help: norm(e.short_help),
    helpWords: words(e.short_help),
    guide: guideText(e.guide),
  }))

  const match = (r: Row, q: string, qw: string[]): MatchVia | null => {
    if (r.plain === q) return 'plain'
    if (r.aliases.includes(q)) return 'alias'
    if (r.model === q.replace(/ /g, '_')) return 'model'
    if (prefixMatch(qw, r.plainWords) || r.aliasWords.some(a => prefixMatch(qw, a))) return 'prefix'
    if (q.length < MIN_TEXT_QUERY) return null
    if (r.help.includes(q) || prefixMatch(qw, r.helpWords)) return 'help'
    if (r.guide.includes(q)) return 'guide'
    return null
  }

  return {
    search(query, opts = {}) {
      const q = norm(query)
      if (!q) return []
      const qw = words(q)
      const hits: ParameterHit[] = []
      for (const r of rows) {
        const via = match(r, q, qw)
        if (via) hits.push({ entry: r.entry, via, score: SCORE[via] })
      }
      const lens = opts.lens ?? null
      const needed = (h: ParameterHit) => (lens && h.entry.functions[lens] === 'needs' ? 1 : 0)
      hits.sort((a, b) =>
        needed(b) - needed(a)
        || b.score - a.score
        || DEPTH_RANK[a.entry.depth] - DEPTH_RANK[b.entry.depth]
        || a.entry.plain_name.localeCompare(b.entry.plain_name)
        || a.entry.id.localeCompare(b.entry.id))
      return opts.limit ? hits.slice(0, opts.limit) : hits
    },
  }
}
