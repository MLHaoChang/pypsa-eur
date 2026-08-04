// Scenario category (baseline / scenario / stress).
//
// It is a REAL FIELD now — `ProjectInfo.scenario_type`, backed by a column
// since backend migration 0004. Before that it was smuggled as a `[type]`
// prefix on `scenario_description`, written by the branch dialog and decoded
// by exactly one panel, so every other surface printed the marker at the user
// and the value could not be corrected after creation.
//
// The decoder stays because the encoding still arrives from outside the
// migrated database: a `.pypsaproj` bundle exported by an older install
// carries the tag inline in its metadata.json. The backend splits what it can
// on import, and this is the client-side backstop for anything it did not
// see. Nothing here WRITES the prefix any more — `tagScenType` is gone, and
// the create/edit calls send `scenario_type` as its own field.

export const SCEN_TYPES = ['baseline', 'scenario', 'stress'] as const
export type ScenType = typeof SCEN_TYPES[number]

/** Human labels for the picker. Keep in step with the backend's set. */
export const SCEN_TYPE_LABEL: Record<ScenType, string> = {
  baseline: 'Baseline — canonical reference run',
  scenario: 'Scenario — a named variant',
  stress: 'Stress test',
}

// Anchored, and the alternatives are spelled out rather than `\w+`: a
// description that legitimately opens with a bracketed word ("[draft] cut the
// gas fleet") must survive intact, not lose its first word to the parser.
const TAG_RE = /^\[(baseline|scenario|stress)\]\s*([\s\S]*)$/

function isScenType(v: unknown): v is ScenType {
  return typeof v === 'string' && (SCEN_TYPES as readonly string[]).includes(v)
}

export interface ParsedScenType {
  /** The category, or null when neither the field nor a tag supplied one. */
  type: ScenType | null
  /** The description with any legacy tag removed — '' when nothing remains. */
  text: string
}

/**
 * The category and description to DISPLAY for a project.
 *
 * `scenario_type` wins whenever it is set, so a description a user later edits
 * to start with "[baseline]" cannot silently re-categorise their project. The
 * tag is only consulted when the field is empty, which today means a bundle
 * that predates the split.
 *
 * An unrecognised `scenario_type` (a category added server-side that this
 * build has no label for) resolves to `null` — no badge — rather than
 * rendering a raw value or throwing the row away.
 */
export function resolveScenType(
  project: { scenario_type?: string | null; scenario_description?: string | null },
): ParsedScenType {
  const description = project.scenario_description
  if (project.scenario_type) {
    return {
      type: isScenType(project.scenario_type) ? project.scenario_type : null,
      text: description ?? '',
    }
  }
  return parseScenType(description)
}

/** Decode a legacy `[type] text` description. Prose passes through whole. */
export function parseScenType(desc?: string | null): ParsedScenType {
  if (!desc) return { type: null, text: '' }
  const m = TAG_RE.exec(desc)
  if (m) return { type: m[1] as ScenType, text: m[2].trim() }
  return { type: null, text: desc }
}

/**
 * Just the human-readable part of a description, for surfaces that show the
 * text but have nowhere to put the category badge. Returns '' rather than the
 * raw string when the description was nothing but a legacy tag, so callers can
 * keep using a falsy check to skip rendering an empty line.
 */
export function scenDescriptionText(desc?: string | null): string {
  return parseScenType(desc).text
}
