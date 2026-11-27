// The Results panel's tab rows and the one rule for which of them show
// (assistant-assurance 06 S5). Results.tsx renders these; `tabsFor` is the same
// rule read without a mounted panel, by the result-navigation browser journey
// and its tests.
import { Gauge, TrendingUp, Activity, Network as NetworkIcon, Layers, DollarSign, Cloud, Wallet, Scissors, AlertTriangle, BatteryCharging, Crosshair, ShieldAlert, ShieldCheck, Briefcase, Workflow, CalendarClock, Zap, FileText, FileDown, Package, Table2 } from 'lucide-react'
import type { UiMode } from '../../store/uiStore'
import type { ResultsTab } from './groups'

// `multiOnly` tabs appear only for multi-period results — the Overview
// consolidates per-period economics / generation mix, which is meaningless
// on a flat single-period run. `expertOnly` tabs are hidden in Guided mode
// (guided-mode spec §3.5): Guided keeps adequacy and FMEA, the two the
// hub-design flow reads.
export const TABS: Array<{ id: ResultsTab; label: string; Icon: typeof TrendingUp; tip: string; multiOnly?: boolean; expertOnly?: boolean; processOnly?: boolean; productsOnly?: boolean }> = [
  { id: 'summary',    label: 'Summary',            Icon: Gauge,            tip: 'The solve at a glance: cost, emissions, adequacy and the key capacities, each linking to its tab' },
  { id: 'overview',   label: 'Overview',           Icon: Layers,           tip: 'Per-period economics, generation mix & storage — the whole horizon at a glance', multiOnly: true, expertOnly: true },
  { id: 'capex',      label: 'Capacity expansion', Icon: TrendingUp,       tip: 'CAPEX, OPEX, sized assets', expertOnly: true },
  { id: 'dispatch',   label: 'Dispatch',           Icon: Activity,         tip: 'Per-snapshot generation, storage, loads', expertOnly: true },
  { id: 'loadflow',   label: 'Load Flow',          Icon: NetworkIcon,      tip: 'Line flows, voltages, losses', expertOnly: true },
  { id: 'grid_code',  label: 'Grid code & connection', Icon: ShieldCheck, tip: 'The grid study: campus PCC compliance against the grid code, and a planning → dynamics study\'s connection capacity and connection-point assessment', expertOnly: true },
  { id: 'electrical_design', label: 'Electrical design', Icon: Zap, tip: 'The campus study: transformer sizing, reactive compensation, short circuit, critical hours and the investment that meets them', expertOnly: true },
  { id: 'island',     label: 'Island & ride-through', Icon: ShieldAlert, tip: 'The island study: ride-through hours, the instant of islanding, the cost of island capability, the pickup sequence and the frequency cuts', expertOnly: true },
  { id: 'grid_forming', label: 'Grid-forming',   Icon: Zap,              tip: 'The recommended grid-forming settings per unit, the cases before and after, and the capacity needed when no setting passes', expertOnly: true },
  { id: 'flows',      label: 'Energy flow',        Icon: Workflow,         tip: 'Where the site\'s energy came from, what it passed through and where it went — Source → Via → Destination in MWh, with a CSV export' },
  { id: 'loadshape',  label: 'Load shape',         Icon: CalendarClock,    tip: 'How the site\'s load and grid exchange are shaped in time — import and export heatmaps, a one-week profile, monthly and annual site energy' },
  { id: 'prices',     label: 'Prices',             Icon: DollarSign,       tip: 'Marginal prices, duration curve, drivers', expertOnly: true },
  { id: 'economics',  label: 'Asset economics',    Icon: Wallet,           tip: 'Per-asset revenue, profit, LCOE/LCOS — by group or by individual asset', expertOnly: true },
  { id: 'emissions',  label: 'Emissions',          Icon: Cloud,            tip: 'CO₂ totals, cap shadow price, per-carrier breakdown', expertOnly: true },
  { id: 'curtailment',label: 'Curtailment',        Icon: Scissors,         tip: 'Renewable energy rejected by the LP — total + per-carrier + time series', expertOnly: true },
  { id: 'lostload',   label: 'Lost load',          Icon: AlertTriangle,    tip: 'VOLL slack dispatch (unserved demand) — per-carrier and per-bus breakdown', expertOnly: true },
  { id: 'adequacy',   label: 'Adequacy',           Icon: ShieldCheck,      tip: 'Reliability targets, COPT screening, the cost-vs-availability frontier, sequential Monte Carlo and the reliability-targeted planning loop' },
  { id: 'storage',    label: 'Storage cycling',    Icon: BatteryCharging,  tip: 'Equivalent full-cycle count per storage unit + carrier rollup', expertOnly: true },
  { id: 'grid_risk',  label: 'Grid risk to production', Icon: ShieldAlert, tip: 'Each grid risk (ride-through, island, N-1, connection curtailment) as lost tonnes, then EUR, per year', processOnly: true },
  { id: 'products',   label: 'Products',           Icon: Package,          tip: 'What the site makes (hydrogen, ammonia, …): output in its own unit, running time and, for hydrogen, the cost per kg', productsOnly: true },
  { id: 'fmea',       label: 'FMEA',               Icon: ShieldAlert,      tip: 'Failure modes ranked by criticality (€ per year): computed rows + expert class-D rows, mitigability, CSV export' },
  { id: 'investment', label: 'Investment case',    Icon: Briefcase,        tip: 'The investment case: the site bill, participants and value flows, the Library, tariffs and contracts', expertOnly: true },
  { id: 'asset',      label: 'Asset Detail',       Icon: Crosshair,        tip: 'One asset in full — every applicable result, as numbers or charts, exportable', expertOnly: true },
]

// Hand-off tabs (R6): what leaves the tool, not a solve output. Kept apart from TABS, which is
// the list of result views (the workbook picker and the assistant inventory read it as such).
export const HANDOFF_TABS: typeof TABS = [
  { id: 'reports',    label: 'Reports',            Icon: FileText,         tip: 'Study reports written from the Energy Hub reference design and the adequacy study; read them here and export to Word' },
  { id: 'exports',    label: 'Exports',            Icon: FileDown,         tip: 'Export the project: bundle, NetCDF, CSV, Excel, MATPOWER or the results workbook' },
  { id: 'equipment_schedule', label: 'Equipment schedule', Icon: Table2, tip: 'Every electrical asset of the last DC Grid study: rating, quantity, governing criterion, source and capex, with an Excel download' },
  { id: 'specialist_packet', label: 'Specialist packet', Icon: Package,   tip: 'What a dynamics specialist receives: the dispatch source, ranked snapshots, assumptions ledger and per-hour bundles' },
]
// Every tab the panel can show: the result views, then Hand-off.
export const ALL_TABS: typeof TABS = [...TABS, ...HANDOFF_TABS]

// The tabs Guided mode shows (every other row is `expertOnly`).
export const GUIDED_TABS: ReadonlySet<ResultsTab> = new Set<ResultsTab>(
  ALL_TABS.filter(t => !t.expertOnly).map(t => t.id),
)

/** Single-period (flat snapshots) or multi-period (the snapshots list `periods`). */
export type NetworkKind = 'single' | 'multi'
/** What the process view lists: process units (Grid risk) and product buses (Products). */
export interface TabFlags { processUnits?: boolean; products?: boolean }

/** Whether the panel shows this row in this mode, for this network. */
export function tabShown(t: (typeof ALL_TABS)[number], mode: UiMode, kind: NetworkKind, flags: TabFlags = {}): boolean {
  return (!t.multiOnly || kind === 'multi') && (!t.expertOnly || mode === 'expert')
    && (!t.processOnly || flags.processUnits === true) && (!t.productsOnly || flags.products === true)
}

/** The result views (TABS order) the panel shows. Hand-off tabs are not result
 * views and the assistant's `results_tab` cannot name them, so they are left out. */
export function tabsFor(mode: UiMode, kind: NetworkKind, flags: TabFlags = {}): ResultsTab[] {
  return TABS.filter(t => tabShown(t, mode, kind, flags)).map(t => t.id)
}
