// SC-07 S2: the companion's entry points inside Site 3D (UX U2–U4).
// - One row under the part tabs (UX V1–V2): "Explain <selection or part>",
//   "Help with: <blocker>" and "Prepare a change", in the hub cards' Ask look.
//   Each opens the assistant with a question prefilled and sends nothing (the
//   hub cards' `ask`); nothing navigates on a click.
// - "Help with" shows only while `/flow` reports a blocker for the open part,
//   named by the blocker's own reason; absent, not disabled, otherwise.
// - Words only: the Site by its name, the part by its tab label, a building
//   by its visible label; never an id or a field key (U4).
import type { SceneBlocker, SitePart, FlowScene } from '../api/flow'
import { ask, ASK_TITLE } from '../pages/hubDesign/delegate'
import type { SceneBuilding } from '../api/siteScene'
import { MessageCircleQuestion } from 'lucide-react'

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

const sentence = (s: string) => s.replace(/[.\s]+$/, '') + '.'

/** The hub cards' Ask look (GridReadinessScorecard): it opens the dock and goes nowhere, so no "›". */
const ASK_CLASS = 'min-w-0 max-w-full inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] '
  + 'text-muted hover:border-accent hover:text-accent'

function AskButton({ label, prefill, testid }: { label: string; prefill: string; testid: string }) {
  return (
    <button type="button" className={ASK_CLASS} title={`${label}. ${ASK_TITLE}`} data-testid={testid} onClick={() => ask(prefill)}>
      <MessageCircleQuestion size={12} className="shrink-0" aria-hidden />
      <span className="truncate">{label}</span>
    </button>
  )
}

export default function SiteCompanion({ siteName, part, scene, selected }: {
  siteName: string; part: SitePart; scene: FlowScene | undefined; selected: SceneBuilding | null
}) {
  const where = `the ${PART_LABEL[part]} part of the site “${siteName}” in Site 3D`
  const blocker = partBlocker(scene, part)
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="site-companion" role="group" aria-label="Ask the assistant">
      {selected
        ? <AskButton testid="site-companion-explain" label={`Explain ${buildingLabel(selected)}`}
            prefill={`Explain ${buildingLabel(selected)}, selected in ${where}.`} />
        : <AskButton testid="site-companion-explain" label={`Explain ${PART_LABEL[part]}`}
            prefill={`Explain what I am looking at: ${where}.`} />}
      {blocker && (
        <AskButton testid="site-companion-blocker" label={`Help with: ${sentence(blocker.reason).slice(0, -1)}`}
          prefill={`Help me with what blocks ${where}: ${sentence(blocker.reason)}`} />
      )}
      <AskButton testid="site-companion-prepare" label="Prepare a change"
        prefill={`Prepare a change to the site “${siteName}” in Site 3D: `} />
    </div>
  )
}
