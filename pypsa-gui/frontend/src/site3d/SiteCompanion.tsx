// SC-07 S2: the companion's entry points inside Site 3D (UX U2–U4).
// - One row under the part tabs (UX V1–V2): "Explain <selection or part>",
//   and "Prepare a change", in the hub cards' Ask look.
//   Each opens the assistant with a question prefilled and sends nothing (the
//   hub cards' `ask`); nothing navigates on a click.
// - "Ask for help" sits on a `/flow` blocker's own line, after its "›" link
//   (UX #1226, the fix on the refusal's line as in #1193); only with a blocker.
// - Words only: the Site by its name, the part by its tab label, a building
//   by its visible label; never an id or a field key (U4).
import type { SitePart } from '../api/flow'
import { ask, ASK_TITLE } from '../pages/hubDesign/delegate'
import type { SceneBuilding } from '../api/siteScene'
import { MessageCircleQuestion } from 'lucide-react'
import type React from 'react'

export const PART_LABEL: Record<SitePart, string> = { map: 'Map', generate: 'Generate', model: 'Model', results: 'Results' }

/** The section words a scene blocker's "›" link shows. */
export const SECTION_WORDS: Record<string, string> = { project: 'Site coordinates', loads: 'Loads', results: 'Results' }

/** A building as the correction panel names it. */
export const buildingLabel = (b: SceneBuilding): string =>
  b.source.namespace === 'osm' ? `OSM building ${b.source.source_id}` : 'Proposed building'

const sentence = (s: string) => s.replace(/[.\s]+$/, '') + '.'

/** The hub cards' Ask look (GridReadinessScorecard): it opens the dock and goes nowhere, so no "›". */
const ASK_CLASS = 'min-w-0 max-w-full inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] '
  + 'text-muted hover:border-accent hover:text-accent'

export function AskButton({ label, prefill, testid }: { label: string; prefill: string; testid: string }) {
  return (
    <button type="button" className={ASK_CLASS} title={`${label}. ${ASK_TITLE}`} data-testid={testid} onClick={() => ask(prefill)}>
      <MessageCircleQuestion size={12} className="shrink-0" aria-hidden />
      <span className="truncate">{label}</span>
    </button>
  )
}

/** Where the user is, in words: "the Model part of the site “Campus” in Site 3D". */
export const whereWords = (siteName: string, part: SitePart) => `the ${PART_LABEL[part]} part of the site “${siteName}” in Site 3D`

/** "Ask for help" on a blocker's own line: the blocker's reason, prefilled. */
export function BlockerAsk({ where, reason }: { where: string; reason: string }) {
  return <AskButton testid="site-companion-blocker" label="Ask for help" prefill={`Help me with what blocks ${where}: ${sentence(reason)}`} />
}

export default function SiteCompanion({ siteName, part, selected, resume }: {
  siteName: string; part: SitePart; selected: SceneBuilding | null
  /** SC-07 S5b R5: the Resume line, on its own line in this row (UX (13)). */
  resume?: React.ReactNode
}) {
  const where = whereWords(siteName, part)
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="site-companion" role="group" aria-label="Ask the assistant">
      {selected
        ? <AskButton testid="site-companion-explain" label={`Explain ${buildingLabel(selected)}`}
            prefill={`Explain ${buildingLabel(selected)}, selected in ${where}.`} />
        : <AskButton testid="site-companion-explain" label={`Explain ${PART_LABEL[part]}`}
            prefill={`Explain what I am looking at: ${where}.`} />}
      <AskButton testid="site-companion-prepare" label="Prepare a change"
        prefill={`Prepare a change to the site “${siteName}” in Site 3D: `} />
      {resume}
    </div>
  )
}
