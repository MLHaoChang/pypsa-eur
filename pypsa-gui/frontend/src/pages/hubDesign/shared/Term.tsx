// A plain-language term with a "?" hover (guided-mode spec §5.1, §5.8). The
// text is the guide catalogue's field — the same words the tour and the
// assistant use — with an offline fallback identical to the catalogue
// string (Term.test pins the two together, like the Bus card's fallbacks).
import type { ReactNode } from 'react'
import { useGuideField } from '../../../components/GuidedTour'
import { InfoTip } from '../../../layout/properties/cardKit'

export const TERM_FALLBACK = {
  hub_start:
    "Start from a ready-made example site, or use the network you already have.",
  hub_site:
    "Check what the study needs to know about the site: the grid connection, the load that must stay on, grid strength and how often equipment breaks.",
  hub_goal:
    "Set how many hours of shortfall per year you can accept, then run the study.",
  hub_results:
    "Read what the study found: whether the goal is met, what it costs and the biggest risks.",
  hub_improve:
    "Work through the study's recommendations, check what happens when equipment fails, and add hard conditions to test.",
  site_type:
    "How the site relates to the grid: a strong grid you can always import from, a weak or limited connection, or no grid connection at all.",
  grid_connection:
    "The line or transformer that brings power from the public grid to the site, and how much power it can carry.",
  critical_load:
    "The demand that must keep running even when the grid connection fails, for example servers, operating theatres or cooling.",
  grid_strength:
    "How strong the public grid is where the site connects. A weak grid limits how much solar, wind and battery equipment the site can safely connect.",
  outage_data:
    "How often each generator or connection breaks down and how long a repair takes. The study needs this to estimate shortfall.",
  shortfall_hours:
    "Expected hours per year when some demand cannot be served. Lower is more reliable; the study checks the site against your goal.",
  energy_strictness:
    "How much of the yearly energy demand may go unserved at most, as a very small share. Stricter means more backup equipment and higher cost.",
  verdict:
    "The study's answer: certified (the goal is met), not certified (it is missed), not decided (more random trials are needed) or no goal set (nothing to check against).",
  cost_at_target:
    "The yearly cost of the planned design, including new equipment and running costs. It leaves out the cost of any shortfall; the verdict above says how reliable the design is.",
  top_risks:
    "The failures that cost the most per year, combining how often they happen with how much damage each one does.",
  not_established:
    "Parts of the study that could not be worked out, with the reason, so you know what the result does not cover.",
  stress_scenario:
    "A hard condition to test the site against, such as a heatwave or a dark, windless week, with how often it happens.",
  fmea_check:
    "A check that switches off each piece of equipment in turn and in stress conditions, to find the failures that hurt the site most.",
  template_provenance:
    "Where the example site's numbers come from. Example sites use made-up but realistic data, so replace them with your own before deciding.",
  voll_plain:
    "What each unit of energy that cannot be delivered is worth to you, in euros. The study needs a price above zero to weigh shortfall against the cost of backup.",
  // P29 (B2): the Guided FMEA tab.
  fmea_class_a:
    "A generator breaks down at random. The row estimates how often that happens and what the missing energy would cost, without re-running the whole plan.",
  fmea_class_b:
    "A connection, such as the grid supply or a converter, is switched off and the plan is re-run to measure the energy the site cannot get.",
  fmea_class_c:
    "A hard condition you described, such as a heatwave or a dark, windless week, is applied and the plan is re-run to measure the shortfall.",
  fmea_class_d:
    "A failure you added yourself, with your own estimate of how often it happens and what one event costs. It is kept with the project.",
  fmea_engine:
    "How this row was worked out: a quick estimate for a generator, a full re-run of the plan for connections and stress scenarios, or your own figures.",
  fmea_severity:
    "What one event costs: the energy that cannot be delivered, priced at what undelivered energy is worth to you.",
  fmea_occurrence:
    "How often the failure happens, in events per year. For equipment it comes from how often it breaks down and how long a repair takes.",
  fmea_criticality:
    "The yearly risk: how often the failure happens times what one event costs. The table is ranked by it, largest first.",
} as const

export type TermKey = keyof typeof TERM_FALLBACK

/** The catalogue text for `k` (fallback while it loads or offline). */
export function useTerm(k: TermKey): string {
  return useGuideField(k, TERM_FALLBACK[k])
}

export function Term({ k, children }: { k: TermKey; children: ReactNode }) {
  const text = useTerm(k)
  return (
    <span data-testid={`term-${k}`} data-tip={text} className="inline-flex items-baseline">
      <InfoTip text={text}>{children}</InfoTip>
    </span>
  )
}
