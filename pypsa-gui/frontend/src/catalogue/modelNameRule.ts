/**
 * The whole-word snake_case model-name rule (RAW-NAMES-1, expert-catalogue spec
 * §7.2 and §7.3). One rule for two readers: the UI copy guard
 * (`uiCopyGuard.test.ts`), which fails on a model name in copy, and the chat's
 * display replacement (`displayPlainNames.ts`), which swaps a backticked one for
 * its plain name. The backend copy (`services/chat_plain_names.py`) is held to
 * it by the shared case table `backend/tests/data/display_plain_names_cases.json`.
 */

/** Not inside a longer identifier: no letter, digit or `_` before / after. */
export const NAME_EDGE = '(?<![A-Za-z0-9_])'
export const NAME_END = '(?![A-Za-z0-9_])'

export function escapeRe(s: string): string {
  return s.replace(/[.+^${}()|[\]\\]/g, '\\$&')
}

/** An alternation of `alts`, longest first, so `p_nom_max` wins over `p_nom`. */
export const word = (alts: string[]) => alts.map(escapeRe).sort((a, b) => b.length - a.length).join('|')

/** The catalogue's model names that carry an underscore. */
export function snakeNames(modelNames: Iterable<string>): string[] {
  return [...new Set(modelNames)].filter((n) => n.includes('_')).sort()
}

/** A snake_case model name anywhere, as a whole word. */
export function snakeNameRule(modelNames: Iterable<string>): RegExp {
  return new RegExp(`${NAME_EDGE}(?:${word(snakeNames(modelNames))})${NAME_END}`)
}
