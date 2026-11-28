/**
 * Display replacement in chat (expert-catalogue spec §7.3): a backticked model
 * name in the assistant's text (`p_nom`) is shown as the catalogue's plain name,
 * as plain words. Display and speech only: the stored transcript keeps what the
 * model wrote.
 *
 * Only a backticked inline token that is a whole catalogue model name by the
 * guard's snake_case rule (`modelNameRule.ts`) is replaced. Bare prose, fenced
 * code and quoted text (`>`, often the user's own words) are left as written.
 * A name whose plain name differs by component class, or that the catalogue
 * does not hold, stays raw in code style: no half-translation. A name the user
 * typed in the message being answered ("what is e_nom?") stays raw too, so the
 * reply talks about the key the user asked about.
 *
 * The plain names are `modelNames.generated.json`, written from the backend
 * Parameter catalogue (`smoke/catalogue_model_names.py`): the words the
 * catalogue's guide and search show, and the server's `lookup.plain_name`.
 * The backend copy is `services/chat_plain_names.py`; both read the case table
 * `backend/tests/data/display_plain_names_cases.json`.
 */
import generated from './modelNames.generated.json'
import { snakeNameRule } from './modelNameRule'

/** Class or settings scope → {model_name: plain name}. */
export type ModelNameMap = Record<string, Record<string, string>>

/** Names the Properties panel still labels differently (`FieldSpec.label`), so
 *  chat keeps them raw until expert-catalogue slice 03 moves the panel to the
 *  catalogue. Mirrored in `services/chat_plain_names.py`, which has the test
 *  that fails once a label matches. */
export const PANEL_NAMES_DIFFER: readonly string[] = [
  'marginal_cost',
  'capital_cost',
  'p_nom_max',
  's_nom',
]

interface NameIndex {
  rule: RegExp
  /** Model name → its one plain name, or null when classes disagree. */
  plain: Map<string, string | null>
}

function buildIndex(names: ModelNameMap): NameIndex {
  const plain = new Map<string, string | null>()
  for (const byName of Object.values(names)) {
    for (const [key, word] of Object.entries(byName)) {
      plain.set(key, plain.has(key) && plain.get(key) !== word ? null : word)
    }
  }
  for (const key of PANEL_NAMES_DIFFER) plain.set(key, null)
  return { rule: snakeNameRule(plain.keys()), plain }
}

const indexes = new WeakMap<ModelNameMap, NameIndex>()
function indexOf(names: ModelNameMap): NameIndex {
  let ix = indexes.get(names)
  if (!ix) indexes.set(names, (ix = buildIndex(names)))
  return ix
}

const DEFAULT_NAMES = generated as ModelNameMap

/** The catalogue's plain name of `key` on class or scope `cls`. */
export function plainNameOf(cls: string, key: string): string | undefined {
  return DEFAULT_NAMES[cls]?.[key]
}

/** The plain name a backticked `key` is shown as; null when it stays raw. */
export function displayWord(key: string, names: ModelNameMap = DEFAULT_NAMES): string | null {
  const { rule, plain } = indexOf(names)
  const m = rule.exec(key)
  return m && m[0] === key ? plain.get(key) ?? null : null
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/
const QUOTE = /^\s*>/

/** One line outside fenced code and quotes: its single-backtick spans, CommonMark-paired by run length. */
function replaceLine(line: string, swap: (token: string) => string | null): string {
  const runs = [...line.matchAll(/`+/g)]
  let out = ''
  let at = 0
  for (let i = 0; i < runs.length; i++) {
    const open = runs[i]
    const close = runs.findIndex((r, j) => j > i && r[0].length === open[0].length)
    if (close < 0) continue
    const start = open.index!
    const end = runs[close].index! + runs[close][0].length
    const word = open[0].length === 1 ? swap(line.slice(start + 1, runs[close].index!)) : null
    if (word !== null) {
      out += line.slice(at, start) + word
      at = end
    }
    i = close
  }
  return out + line.slice(at)
}

/**
 * `text` (assistant markdown) with each backticked model name shown as its
 * plain name. `userText`: the user's message this text answers; a model name in
 * it stays raw.
 */
export function displayPlainNames(text: string, opts: { userText?: string; names?: ModelNameMap } = {}): string {
  if (!text || !text.includes('`')) return text
  const names = opts.names ?? DEFAULT_NAMES
  const { rule } = indexOf(names)
  const asked = new Set([...(opts.userText ?? '').matchAll(new RegExp(rule.source, 'g'))].map((m) => m[0]))
  const swap = (token: string) => (asked.has(token) ? null : displayWord(token, names))
  let fence: string | null = null
  let quoted = false
  return text.split('\n').map((line) => {
    if (fence !== null) {
      if (new RegExp(`^ {0,3}${fence[0] === '`' ? '`' : '~'}{${fence.length},}\\s*$`).test(line)) fence = null
      return line
    }
    const f = FENCE.exec(line)
    if (f) {
      fence = f[1]
      quoted = false
      return line
    }
    if (!line.trim()) quoted = false
    else if (QUOTE.test(line)) quoted = true
    // A quote runs on (lazy continuation) until a blank line.
    return quoted ? line : replaceLine(line, swap)
  }).join('\n')
}
