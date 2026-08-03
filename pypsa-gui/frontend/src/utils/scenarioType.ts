// Scenario category (baseline / scenario / stress).
//
// `ProjectInfo` carries no field for it, so the category is smuggled as a
// `[type]` prefix on `scenario_description` — written by the scenario-create
// dialog, read back by `parseScenType`.
//
// This lived inside ScenariosPanel, which was the only place that knew to
// strip the prefix. Every OTHER surface that renders a description printed the
// marker at the user: a scenario created with no description showed the
// literal text "[scenario]" as its description on the projects home. Shared
// encoding needs shared decoding, so both halves live here and the panel, the
// projects home and the legacy-migrate page all read through them.
//
// The prefix is a workaround, not a design — a real `scenario_type` column
// retires this module.

export const SCEN_TYPES = ['baseline', 'scenario', 'stress'] as const
export type ScenType = typeof SCEN_TYPES[number]

// Anchored, and the type alternatives are spelled out rather than `\w+`: a
// description that legitimately opens with a bracketed word ("[draft] cut the
// gas fleet") must survive intact, not lose its first word to the parser.
const TAG_RE = /^\[(baseline|scenario|stress)\]\s*([\s\S]*)$/

export interface ParsedScenType {
  /** The category, or null when the description carries no recognised tag. */
  type: ScenType | null
  /** The description with the tag removed — '' when there was nothing else. */
  text: string
}

export function parseScenType(desc?: string | null): ParsedScenType {
  if (!desc) return { type: null, text: '' }
  const m = TAG_RE.exec(desc)
  if (m) return { type: m[1] as ScenType, text: m[2].trim() }
  return { type: null, text: desc }
}

/** The stored form: `[type] description`, or just `[type]` with no text. */
export function tagScenType(type: ScenType, description: string): string {
  return `[${type}] ${description.trim()}`.trim()
}

/**
 * Just the human-readable part of a description, for surfaces that show the
 * text but have nowhere to put the category badge. Returns '' rather than the
 * raw string when the description was nothing but a tag, so callers can keep
 * using a falsy check to skip rendering an empty line.
 */
export function scenDescriptionText(desc?: string | null): string {
  return parseScenType(desc).text
}
