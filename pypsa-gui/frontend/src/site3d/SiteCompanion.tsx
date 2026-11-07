// SC-07 S2: the companion's entry points inside Site 3D (UX U2–U4).
// - One row under the part tabs: "Explain this", "Help with <section>" and
//   "Prepare a change". Each opens the assistant with a question prefilled
//   and sends nothing (the hub cards' `ask`); nothing navigates on a click.
// - "Help with" shows only while `/flow` reports a blocker for the open part,
//   named by the blocker's own reason and the section words its "›" link uses.
// - Words only: the Site by its name, the part by its tab label, a building
//   by its visible label; never an id or a field key (U4).
import type { SceneBlocker, SitePart, FlowScene } from '../api/flow'
import { ask, ASK_TITLE } from '../pages/hubDesign/delegate'
import type { SceneBuilding } from '../api/siteScene'

export const PART_LABEL: Record<SitePart, string> = { map: 'Map', generate: 'Generate', model: 'Model', results: 'Results' }

/** The `/flow` scene action behind each part; Map stands on the creation entry. */
const PART_ACTION: Partial<Record<SitePart, string>> = { generate: 'generate_native', model: 'assign_native', results: 'play_results' }

/** The section words a scene blocker's "›" link shows. */
export const SECTION_WORDS: Record<string, string> = { project: 'Site coordinates', loads: 'Loads', results: 'Results' }

/** A building as the correction panel names it. */
export const buildingLabel = (b: SceneBuilding): string =>
  b.source.namespace === 'osm' ? `OSM building ${b.source.source_id}` : 'Proposed building'

/** The blocker `/flow` reports for the open part, or null. */
export function partBlocker(scene: FlowScene | undefined, part: SitePart): SceneBlocker | null {
  if (!scene) return null
  const name = PART_ACTION[part]
  if (!name) return scene.entry?.enabled === false ? scene.entry.blocker ?? null : null
  const a = scene.actions.find(x => x.action === name)
  return a && !a.available ? a.blocker : null
}

const blockerWords = (b: SceneBlocker, part: SitePart): string => {
  const section = (b.home as { guided?: { section: string } }).guided?.section
  return (section && SECTION_WORDS[section]) || PART_LABEL[part]
}

const sentence = (s: string) => s.replace(/[.\s]+$/, '') + '.'

export default function SiteCompanion({ siteName, part, scene, selected }: {
  siteName: string; part: SitePart; scene: FlowScene | undefined; selected: SceneBuilding | null
}) {
  const where = `the ${PART_LABEL[part]} part of the site “${siteName}” in Site 3D`
  const blocker = partBlocker(scene, part)
  const explain = selected
    ? `Explain ${buildingLabel(selected)}, selected in ${where}.`
    : `Explain what I am looking at: ${where}.`
  const btn = 'whitespace-nowrap text-accent hover:underline'
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]" data-testid="site-companion" aria-label="Ask the assistant">
      <span className="text-muted">Ask the assistant:</span>
      <button type="button" className={btn} title={ASK_TITLE} data-testid="site-companion-explain" onClick={() => ask(explain)}>
        Explain this
      </button>
      {blocker && (
        <button type="button" className={btn} title={ASK_TITLE} data-testid="site-companion-blocker"
          onClick={() => ask(`Help me with what blocks ${where}: ${sentence(blocker.reason)}`)}>
          Help with {blockerWords(blocker, part)}
        </button>
      )}
      <button type="button" className={btn} title={ASK_TITLE} data-testid="site-companion-prepare"
        onClick={() => ask(`Prepare a change to the site “${siteName}” in Site 3D: `)}>
        Prepare a change
      </button>
    </div>
  )
}
