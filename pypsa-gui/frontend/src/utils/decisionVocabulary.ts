// The decision study's words (plan S8; spec §8.1 "maintained once, used
// everywhere in the guided flow"). THE ONLY SOURCE of novice labels for the
// guided flow: every page under `pages/decision/` reads its wording from
// here, so a term is never called one thing on the intake and another on the
// verdict. Spec §8.2: plain label first, the technical term as a subtitle, the
// unit always.
//
// The study's own honesty codes are explained by `HELP`, a MIRROR of
// `backend/services/study/report.py::HELP` (and its prefix table and fallback).
// DECISION: mirror + parity test rather than a payload field — the only
// payload carrying these sentences is an ASSEMBLED report's `honesty_help`,
// and the verdict page must explain its disclosures before any report exists.
// The parity is `backend/tests/test_decision_vocabulary_parity.py`, which reads
// the three JSON blocks between the BEGIN/END markers below; keep them JSON
// (double quotes, no trailing comma) and edit the backend table first.
import type { MaturityClass, LedgerStatus, VerdictClass, Engine, ValueStreamsBasis, Provenance } from '../api/decisionStudies'

// ── spec §8.1, amended for the MVP-1 StorageUnit battery ───────────────────

export interface VocabEntry { label: string; unit?: string; technical: string; note?: string }

export const VOCAB: Record<string, VocabEntry> = {
  battery_power: { label: 'Battery power', unit: 'MW', technical: 'p_nom (StorageUnit)' },
  battery_hours: { label: 'Hours of storage', unit: 'h', technical: 'max_hours (StorageUnit)', note: 'Battery energy (MWh) is power × hours of storage.' },
  battery_energy: { label: 'Battery energy', unit: 'MWh', technical: 'p_nom × max_hours (StorageUnit)' },
  availability_profile: { label: 'Availability profile', unit: 'share of capacity', technical: 'p_max_pu' },
  upfront_cost: { label: 'Upfront cost', unit: 'EUR/kW or EUR/kWh', technical: 'overnight_cost', note: 'The guided flow never asks for an annuity.' },
  yearly_fixed_cost: { label: 'Yearly fixed cost', unit: 'EUR/kW/yr', technical: 'fom_cost' },
  running_cost: { label: 'Running cost', unit: 'EUR/MWh', technical: 'marginal_cost' },
  lifetime: { label: 'Lifetime', unit: 'years', technical: 'lifetime' },
  discount_rate: { label: 'Discount rate (real)', unit: '%', technical: 'discount_rate', note: 'Real terms: inflation is taken out.' },
  round_trip_efficiency: { label: 'Round-trip efficiency', unit: '%', technical: 'efficiency_store × efficiency_dispatch' },
  connection_limit: { label: 'Grid connection limit', unit: 'MW', technical: 'p_nom on the import Link (eh_role = grid_import)' },
  run: { label: 'Run the optimisation', technical: 'Run LOPF' },
  annual_mwh: { label: 'Yearly consumption', unit: 'MWh/yr', technical: 'Σ loads_t.p_set' },
  zone: { label: 'Country or grid zone', technical: 'Bus.country' },
  year: { label: 'Year modelled', technical: 'snapshots (8760 hours of one non-leap year)' },
  latitude: { label: 'Latitude (for the PV profile)', unit: '°', technical: 'synthetic PV profile' },
}

/** One sentence per ledger key the novice may not read correctly by its label alone. */
export const ROW_MEANING: Record<string, string> = {
  // Gate S2 [S8] / S4: the one meaning of the energy-price level.
  energy_price_level:
    'A multiplier on the tariff’s time-of-use SPREAD, not on its price level: each energy band moves '
    + 'away from (above 1) or towards (below 1) the bands’ time-weighted average, which stays where it is. '
    + 'Network charges, the demand charge and the export price are not scaled. On a tariff with one flat '
    + 'energy band it changes nothing, so it does not apply there.',
  demand_charge_price: 'What the tariff charges per MW of the highest import in each billing period.',
  discount_rate: 'The real (inflation-free) rate at which future savings are discounted.',
  sizing_limit_connection_multiple:
    'The largest battery or PV the optimiser may build, as a multiple of your grid connection. A size '
    + 'that stops at this limit is not an optimum; the verdict says so.',
  battery_round_trip_efficiency: 'The share of the energy put into the battery that comes back out.',
}

// ── the intake's steps and the hub's sections ─────────────────────────────

export type IntakeStepId = 'site' | 'existing' | 'load' | 'goal' | 'horizon' | 'check'
export const INTAKE_STEP_LABELS: Record<IntakeStepId, string> = {
  site: 'Your site',
  existing: 'What exists today',
  load: 'Your consumption',
  goal: 'Your goal',
  horizon: 'Horizon and perspective',
  check: 'Check your answers',
}
export const INTAKE_NAV_LABEL = 'Decision study intake steps'

export type HubSectionId =
  | 'site' | 'demand' | 'options' | 'tariff' | 'finance' | 'assumptions' | 'run' | 'findings' | 'report'
export const HUB_SECTION_LABELS: Record<HubSectionId, string> = {
  site: 'Site and grid',
  demand: 'Demand',
  options: 'Options',
  tariff: 'Prices and tariffs',
  finance: 'Finance',
  assumptions: 'Assumptions',
  run: 'Run',
  findings: 'Findings',
  report: 'Report',
}

export type ChipState =
  | 'done' | 'using_defaults' | 'customised' | 'needs_attention' | 'not_started'
  | 'rerun_needed' | 'running' | 'not_established' | 'stale' | 'unavailable'
export const CHIP_LABELS: Record<ChipState, string> = {
  done: 'Done',
  using_defaults: 'Using defaults',
  customised: 'Customised',
  needs_attention: 'Needs attention',
  not_started: 'Not started',
  rerun_needed: 'Re-run needed',
  running: 'Running',
  not_established: 'Not established',
  stale: 'Out of date',
  unavailable: 'Not available',
}

export const VIEW_LABELS = {
  hub: 'Overview',
  intake: 'Answers',
  options: 'Options',
  tariff: 'Prices and tariffs',
  finance: 'Finance',
  ledger: 'Assumptions',
  run: 'Run',
  verdict: 'Verdict',
  why: 'Why and how',
  robust: 'How robust',
  report: 'Report',
} as const
export type DecisionView = keyof typeof VIEW_LABELS

// ── verdict, maturity, statuses ───────────────────────────────────────────

export const VERDICT_LABELS: Record<VerdictClass, string> = {
  recommended: 'Recommended',
  marginal: 'Marginal',
  not_recommended: 'Not recommended',
}
export const VERDICT_NOT_ESTABLISHED = 'Verdict not established'
export const NOT_ESTABLISHED = 'not established'

export const MATURITY_LABELS: Record<MaturityClass, string> = {
  screening: 'Screening',
  feasibility: 'Feasibility',
  design: 'Design',
}
export const MATURITY_SENTENCE: Record<MaturityClass, string> = {
  screening: 'A first look on library defaults or a typical load: good for deciding whether to look further, not for a budget.',
  feasibility: 'Your own load and your own key figures: good for a go/no-go on a feasibility study.',
  design: 'Reserved for a design-grade study; this version does not reach it.',
}
export const MATURITY_NOT_ESTABLISHED = 'Maturity not established'

export const LEDGER_STATUS_LABELS: Record<LedgerStatus, string> = {
  default: 'Default',
  customised: 'Customised',
  needs_attention: 'Needs attention',
}
export const PROVENANCE_LABELS: Record<Provenance, string> = {
  library: 'from the library',
  user: 'your value',
  imported: 'imported',
  measured: 'measured',
}

/** The reason in a `needs_attention:<key>:<reason>` ledger note, in words. */
export function needsAttentionReason(reason: string): string {
  if (reason === 'not_applicable') {
    return 'The tariff you chose now has no such charge, so your value no longer applies. Reset the row.'
  }
  if (reason === 'not_used_in_mvp1') return 'This value is not used in this version. Reset the row.'
  const m = /^unit_changed_(.+)_to_(.+)$/.exec(reason)
  if (m) return `The row’s unit changed from ${m[1]} to ${m[2]}. Reset it, then enter your value in ${m[2]}.`
  return `This row needs attention (${reason}). Reset it before running.`
}

export const ENGINE_LABELS: Record<Engine, string> = {
  lp: 'optimisation (LP)',
  lp_duals: 'the model’s own prices (LP duals)',
  bill_calculator: 'bill calculator',
  contract: 'contract',
  mc_resilience: 'resilience simulation',
  cash_flow_expander: 'cash-flow model',
  ledger: 'assumptions ledger',
  method_constant: 'a constant of the method',
  tariff_engine: 'tariff engine',
  finance_engine: 'finance engine',
}

export const FIDELITY_LABELS = { quick_screen: 'quick screen (8760 h, one year)', full_study: 'full study (8760 h, one year)' } as const

/** The waterfall's label by what the streams are measured against (gate S6/S7 carry). */
export const STREAMS_BASIS_LABELS: Record<ValueStreamsBasis, { title: string; sentence: string }> = {
  baseline: {
    title: 'Full saving against the grid-only baseline',
    sentence: 'Each bar is one part of the option’s yearly saving on your bill, against grid supply alone.',
  },
  pv_only_reference: {
    title: 'Battery increment over PV-only',
    sentence: 'Each bar is what the battery adds on top of the same PV built alone — not the option’s full saving.',
  },
}

export const BASIS_SENTENCE = 'Real terms, before tax, without subsidy'

/** Headline KPIs: plain words first, the technical term as a subtitle (gate S8 BC-S8-3). */
export const KPI_LABELS: Record<string, { label: string; technical: string }> = {
  battery_npv: { label: 'Value of the battery today', technical: 'Battery NPV (net present value)' },
  battery_p_nom_mw: { label: 'Battery power', technical: 'p_nom (StorageUnit)' },
  battery_payback_simple: { label: 'Years to pay back the battery', technical: 'simple payback' },
}

/** One-line explanations of the finance and robustness terms (gate S8 BC-S8-3). */
export const GLOSSARY = {
  npv: {
    term: 'Value today (NPV)',
    text: 'Everything the battery saves over its life minus everything it costs, with future money counted at '
      + 'today’s value using the discount rate. Above zero, it pays for itself.',
  },
  irr: {
    term: 'Yearly return (IRR)',
    text: 'The discount rate at which the value today would be exactly zero: the yearly return the investment earns.',
  },
  payback: {
    term: 'Years to pay back',
    text: 'How many years of bill savings it takes to earn back what the battery cost.',
  },
  tornado: {
    term: 'The centre and the tornado bounds',
    text: 'The centre is the result with every assumption at its central value. The robustness check (the '
      + '“tornado”) moves each key assumption, one at a time, to the low and the high end of its plausible range, '
      + 'with the battery size held fixed; “at every tornado bound” means the result held at all of them.',
  },
  taxes_levies: {
    term: 'Taxes & levies',
    text: 'Taxes, levies and certificate charges the tariff bills on the energy bought from the grid. '
      + 'Shown only when the tariff has such a charge.',
  },
  pv_only: {
    term: 'Battery value against PV-only',
    text: 'When an option also builds solar PV, the battery’s value is what it adds on top of the same PV built '
      + 'alone, so it assumes the site builds that PV.',
  },
} as const

/** A `bess_pv` verdict (gate S8 BC-S8-2; the report's S7 `_BESS_PV` / `_BATTERY_ONLY_*` rule). */
export const PV_VERDICT = {
  title: 'If the site will not build PV',
  total: (npv: string) => `This option also builds PV. Its total value today, PV included, is ${npv}; `
    + 'the battery value above assumes the site builds that PV.',
  best: (power: string, hours: string, npv: string) => `Without PV, the best battery-only option is a battery of `
    + `${power} with ${hours} of storage, with a battery value today of ${npv}: the figure to read if the site `
    + 'will not build PV.',
  none: 'Without PV, no battery-only option sized a battery above zero with an established value, so the study '
    + 'does not show that a battery pays on its own.',
}

/** The bill's components (seven since U2: owner decision 7 adds taxes and levies). */
export const BILL_COMPONENT_LABELS = {
  energy: 'Energy',
  network: 'Network charges',
  demand: 'Demand charge',
  capacity: 'Capacity charge',
  fixed: 'Fixed charges',
  export_credit: 'Export credit',
  taxes_levies: 'Taxes & levies',
} as const

/** Field, column and card labels used by the pages (gate S8 [S7]: this file is the only source). */
export const UI_LABELS = {
  sectorProfile: 'Sector profile',
  fileUnit: 'The file’s unit',
  unitFromHeader: 'As its header says',
  loadFile: 'Load file',
  pvType: 'PV type',
  pvRooftop: 'Rooftop',
  pvGround: 'Ground-mounted',
  studyName: 'Study name',
  baseProjectName: 'Name of the study’s own project',
  colOption: 'Option',
  colSolved: 'Solved',
  colBattery: 'Battery',
  colBill: 'Yearly bill',
  colBatteryNpv: 'Battery value today (NPV)',
  colOptionNpv: 'Option value today, PV included (NPV)',
  readBefore: 'Read these before the numbers',
  caseAssumes: 'What this case assumes',
  expertView: 'Expert view',
  openInExpert: 'Open in the Expert view',
  unsaved: 'Not saved yet',
  saveChanges: 'Save these changes',
  cashFlow: 'Cumulative cash flow',
  valueStreams: 'Where the value comes from',
} as const

export const OPTION_LABELS: Record<string, string> = {
  none: 'Grid only (baseline)',
  bess_1h: 'Battery, 1 hour',
  bess_2h: 'Battery, 2 hours',
  bess_4h: 'Battery, 4 hours',
  bess_pv_2h: 'Battery (2 hours) with PV',
}

export const STREAM_LABELS: Record<string, string> = {
  demand_charge_reduction: 'Demand-charge reduction',
  energy_shift: 'Energy time-shift',
  export_credit: 'Export credit',
  fixed: 'Fixed charges',
  taxes_levies: 'Taxes & levies',
}

// ── refusals: every typed code a study route can answer with ──────────────
//
// `RunRefused` codes (`study_not_runnable`, `campaign_budget_exhausted`,
// `baseline_not_solved`, …) are not manifest `error_kind`s (gate S6 [N6]), so
// they get copy here; each ends in the next action (spec §8.2).

export interface ErrorCopy { title: string; action: string; rerun?: boolean }

export const ERROR_COPY: Record<string, ErrorCopy> = {
  decision_studies_unavailable: {
    title: 'Decision studies are not available in multi-user mode yet.',
    action: 'They need a fix to how time series are stored per user (OPEN-ITEMS 1). Use the desktop app for now.',
  },
  decision_studies_disabled: {
    title: 'Decision studies are switched off.',
    action: 'Start the desktop app with PYPSAGUI_DECISION_STUDIES=1 to switch them on.',
  },
  study_running: { title: 'The study is running.', action: 'Wait for it to finish, or stop it on the Run page.' },
  solver_in_flight: { title: 'Another solve is using this study’s project.', action: 'Wait for it to finish, then try again.' },
  campaign_budget_exhausted: { title: 'The solve budget is too small for this run.', action: 'Raise the budget, or run fewer options (turn PV off).' },
  campaign_budget_invalid: { title: 'The solve budget is not valid.', action: 'Choose a budget between 1 and the maximum shown.' },
  study_not_runnable: {
    title: 'This study record cannot be run.',
    action: 'It is attached to an existing project or was copied from another one. Start a new study from a question card.',
  },
  study_not_found: { title: 'The study no longer exists.', action: 'Open another study.' },
  intake_incomplete: { title: 'Some answers are missing.', action: 'Complete the site, the grid connection and the consumption steps.' },
  intake_invalid: { title: 'An answer is not valid.', action: 'Correct the answer named in the message.' },
  leap_year_unsupported: { title: 'Leap years are not supported.', action: 'Choose a non-leap year (for example 2025).' },
  ledger_needs_attention: { title: 'An assumption needs attention.', action: 'Open Assumptions and reset the rows marked “Needs attention”.' },
  ledger_tariff_stale: { title: 'The assumptions are priced on another tariff.', action: 'Open Assumptions and refresh them for the tariff you chose.' },
  ledger_missing: { title: 'The study has no assumptions yet.', action: 'Open Assumptions once to create them.' },
  ledger_row_missing: { title: 'An assumption the study needs has no value.', action: 'Open Assumptions and fill in the row named in the message.' },
  load_upload_invalid: { title: 'Your load file could not be read.', action: 'Give one column of 8760 hourly values, optionally after a timestamp column.' },
  load_upload_timestamps_invalid: { title: 'The timestamps in your load file are not the study year’s hours.', action: 'Give one row per hour of the year, in order, from 1 January 00:00.' },
  load_upload_unit_unknown: { title: 'The load file does not say its unit.', action: 'Choose kW or MW for the file.' },
  load_upload_unit_mismatch: { title: 'The file’s header and your answer give different units.', action: 'Correct the unit you chose, or the file’s header.' },
  load_upload_unresolved: { title: 'The uploaded file cannot be found.', action: 'Upload the file again.' },
  load_profile_unknown: { title: 'That sector profile does not exist.', action: 'Choose one of the listed profiles.' },
  load_not_established: { title: 'The bill needs your load first.', action: 'Complete the consumption step.' },
  tariff_invalid: { title: 'The tariff cannot be used.', action: 'Choose a library tariff, or correct the one you entered.' },
  tariff_unsupported: { title: 'This tariff has a charge this version cannot price.', action: 'Choose another tariff.' },
  tariff_billing_period: { title: 'The tariff’s billing period is not supported.', action: 'Use a monthly or yearly billing period.' },
  demand_charge_invalid: { title: 'The demand charge cannot be priced.', action: 'Correct the demand charge.' },
  energy_price_level_invalid: { title: 'The energy price level does not apply to this tariff.', action: 'Reset the energy price level row.' },
  option_not_offered: { title: 'That option needs PV turned on.', action: 'Turn PV on in Options, or choose another option.' },
  baseline_not_solved: {
    title: 'The last run did not solve the grid-only baseline, so nothing can be measured against it.',
    action: 'Run the study again.', rerun: true,
  },
  fork_changed_since_run: {
    title: 'An option’s network was changed after the run (opened, edited or re-solved in the Expert view).',
    action: 'Its results no longer match. Run the study again.', rerun: true,
  },
  ledger_changed_since_run: {
    title: 'Your assumptions changed after the run.',
    action: 'The options were sized on the old ones. Run the study again.', rerun: true,
  },
  intake_changed_since_run: {
    title: 'Your answers (site, load, tariff or PV) changed after the run.',
    action: 'The options were built from the old ones. Run the study again.', rerun: true,
  },
  // U2 WP8 (plan §2 C10): the run was made by an earlier version of the app,
  // or an option's tariff or finance settings were edited after it (the
  // message below says which).
  engine_inputs_changed_since_run: {
    title: 'This study’s results no longer match how its options are priced and valued.',
    action: 'Either the app’s calculation was updated since the run, or an option’s tariff or finance settings were edited. Run the study again to refresh them.',
    rerun: true,
  },
  study_never_run: { title: 'The study has not been run yet.', action: 'Open Run and start it.' },
  tornado_never_run: { title: 'The robustness check has not been run yet.', action: 'Start it on the How robust page.' },
  report_never_assembled: { title: 'The report has not been assembled yet.', action: 'Assemble it from the latest findings.' },
  report_prose_invalid: {
    title: 'The report could not be rendered: a paragraph failed the number check.',
    action: 'This is a defect in the report template, not in your study. Report it; the findings are unaffected.',
  },
  option_not_solved: { title: 'The last run did not solve this option.', action: 'Run the study again.' },
  option_unknown: { title: 'The question has no such option.', action: 'Choose an option from the table.' },
  baseline_has_no_case: { title: 'The grid-only baseline has no investment case.', action: 'Every case is measured against it; choose an option.' },
  fork_solving: { title: 'An option is still solving in the queue.', action: 'Wait for it to finish.' },
  fork_has_children: { title: 'A project was branched from one of the study’s options.', action: 'Delete or move that project first.' },
  study_unreadable: { title: 'The study record on disk cannot be read.', action: 'Restore it from a snapshot, or start a new study.' },
  study_library_unreadable: { title: 'The assumptions library cannot be read.', action: 'Reinstall the app or report the defect.' },
  currency_mixed: { title: 'Two currency years are mixed.', action: 'Enter every money value in the study’s currency year.' },
  currency_year_unstated: { title: 'A money value has no currency year.', action: 'State the currency year of the value.' },
  load_upload_unit_unsupported: {
    title: 'The load file’s header names a unit other than kW or MW.',
    action: 'Convert the column to kW or MW (kWh or MWh per hour) and upload it again.',
  },
  load_profile_invalid: { title: 'That sector profile is damaged in the library.', action: 'Choose another profile, or report the defect.' },
  import_link_inactive: { title: 'The study’s grid connection is switched off in its network.', action: 'Run the study again; it rebuilds the network.' },
  // Gate U2-WP7 N13: the case route's 422 when the investment calculation
  // refuses the case; re-running the same inputs would not change it.
  engine_refused: {
    title: 'The investment case for this option could not be calculated from these assumptions.',
    action: 'The message below names what is missing or inconsistent. Correct that assumption; if none applies, report it as a defect.',
  },
}

export const ERROR_FALLBACK: ErrorCopy = { title: 'The request was refused.', action: 'The message below says why.' }

export function errorCopy(code: string | null | undefined): ErrorCopy {
  return (code && ERROR_COPY[code]) || ERROR_FALLBACK
}

// ── the study's own code -> sentence table (MIRROR; see the file header) ───

// BEGIN HELP MIRROR
export const HELP: Record<string, string> = {
  "basis_real_pre_tax_no_subsidy": "Money is in real terms, before tax and without subsidy, in the currency year stated beside each figure.",
  "currency_year_stated": "Every money figure states the currency year it is expressed in.",
  "single_year_extrapolated": "One representative year of operation is repeated over the whole horizon.",
  "perfect_foresight_dispatch": "The model dispatches the battery knowing the whole year's load and prices in advance; a real controller will capture less.",
  "demand_charge_perfect_foresight": "The demand-charge saving assumes the battery knows every month's peak in advance; a real controller misses some peaks, so this stream is an upper bound.",
  "duals_include_demand_charge": "The model's energy prices include the demand charge's shadow price, so they are not market prices.",
  "no_degradation": "Battery degradation is not modelled.",
  "inverter_replaced_at_its_lifetime": "The inverter is replaced at the end of its own lifetime, inside the horizon.",
  "battery_upfront_from_ledger_not_back_calculated": "The battery's purchase cost comes from the assumptions ledger; other screens that back-calculate it from an annualised cost show a different upfront figure.",
  "battery_fom_on_inverter_investment_only": "The battery's fixed operating cost is charged on the inverter investment only.",
  "salvage_annuity_pv_remaining_life": "Equipment with life left at the end of the horizon is credited with the present value of the annuities the model charged for that remaining life.",
  "salvage_not_computed": "The residual value at the end of the horizon could not be computed.",
  "npv_excludes_uncomputed_salvage": "The NPV leaves out a residual value that could not be computed.",
  "market_revenue_at_duals_excluded_from_cash_flow": "Revenue at the model's prices is reported beside the case and excluded from the cash flow: the bill already prices the same energy.",
  "lcos_excludes_charging_energy_cost": "The pro forma's levelised cost of storage leaves out the energy bought to charge the battery.",
  "npv_nonnegative_at_optimum_by_construction": "At the optimiser's chosen size the NPV cannot be negative: the optimiser only builds what pays for itself, so the sign restates its choice and is not independent evidence.",
  "irr_and_discounted_payback_bounded_at_optimum_by_construction": "For the same reason the IRR is at least the discount rate and the discounted payback is within the horizon; the magnitudes inform, the bounds do not.",
  "size_zero_no_investment": "The optimiser sized this battery to zero: no investment is worth making, and its NPV is not read for a sign.",
  "tornado_method_redispatch_fixed_sizes": "The tornado holds the recommended sizes fixed and re-dispatches them at each driver's low and high value, so a bar can turn negative.",
  "snapshot_weightings_not_unit": "The model's hours are weighted; the annual figures depend on those weights.",
  "synthetic_load_profile": "The load is a synthetic sector profile, not the site's metered load.",
  "synthetic_pv_profile": "The PV output is a synthetic clear-sky profile, not measured output.",
  "load_upload_converted_from_kw": "The uploaded load was in kW and was converted to MW.",
  "load_upload_without_timestamps": "The uploaded load has no timestamps; its values were read as the study year's hours in order, from the first hour of January.",
  "tariff_not_chosen_library_default": "No tariff was chosen, so the library's default tariff was used; replace it with the site's own.",
  "tariff_has_uncoded_notes": "The supplied tariff carries notes that are not codes; read them in the tariff.",
  "partial_billing_period_charged_in_full": "A billing period the modelled hours cover only in part is charged in full.",
  "capacity_charge_prorated_by_hours": "The capacity charge is pro-rated by the modelled hours over a full year.",
  "tariff_illustrative": "The tariff is illustrative, not a published tariff.",
  "battery_value_is_the_option_value": "For a battery-only option the battery's value is the option's value.",
  "battery_removed_same_pv_reference": "The battery's value is the option less a reference with the same PV and no battery.",
  "pv_rows_cancel_exactly": "The PV's costs are the same in the option and in its reference, so they cancel.",
  "reference_is_the_baseline": "The option's PV was sized to zero, so its reference is the grid-only baseline.",
  "battery_value_against_pv_only_reference": "The battery's value is measured against the same PV built alone: it assumes the site builds the PV.",
  "bess_pv_value_not_attributable_to_battery": "The battery-and-PV option's value could not be split between the battery and the PV, because its PV-only reference was not computed.",
  "value_streams_need_the_pv_only_reference": "The value streams of a battery-and-PV option need its PV-only reference, which was not computed.",
  "value_streams_battery_increment_over_pv_only_reference": "The value streams are the battery's increment over the same PV alone, not the option's full saving.",
  "options_not_established": "Some options were not solved; they are listed in the options table.",
  "options_not_all_judged": "Not every battery option was judged, so the recommendation covers only the options the study could judge; a different size may be better.",
  "size_at_upper_bound": "A size stopped at its upper limit: the optimiser would have built more, so the limit, not the economics, set the size.",
  "fork_changed_since_run": "An option's network changed after the run and was left out; re-run the study.",
  "tornado_stale": "The tornado was run on an earlier run or ledger and is not used.",
  "tornado_not_run": "The tornado has not been run, so the verdict is not established.",
  "tornado_aborted": "The tornado was stopped before it reached every driver.",
  "tornado_stopped_at_solve_deadline": "A re-dispatch did not finish within the solve deadline, so the tornado stopped there, as a run does: the bars already computed are kept and the drivers not reached are named.",
  "tornado_row_failed": "A tornado bound could not be evaluated.",
  "tornado_not_established": "The tornado is not complete, so the verdict is not established.",
  "tornado_on_another_option": "The tornado ran on a different option than the best one; run it again.",
  "no_battery_candidate": "No option sized a battery above zero with an established value.",
  "no_battery_option_judged": "No battery option could be judged.",
  "best_battery_npv_not_positive_at_centre": "The best battery's NPV is not positive at the centre, so the tornado was not run.",
  "case_not_established": "The option's investment case could not be established.",
  "battery_not_solved": "The option's battery was not solved.",
  "uncoded_note_dropped": "A note that was not a clean code was dropped and is named here.",
  "row_missing": "The driver has no row in the assumptions ledger.",
  "row_not_modelled_in_tornado": "The tornado does not model this driver.",
  "row_has_no_value": "The driver's row has no value.",
  "row_has_no_range": "The driver's row has no range to evaluate.",
  "not_applicable": "The driver does not apply to this tariff or site.",
  "energy_price_level_no_effect_single_band": "The tariff has one flat energy band, so scaling its spread changes nothing.",
  "tariff_has_no_demand_charge": "The tariff has no demand charge to vary.",
  "range_recentred_on_user_value": "The user's value lies outside the library range, so the range's relative width was centred on it.",
  "market_revenue_at_duals_zero_profit_at_optimum": "The revenue at the model's prices equals the battery's annualised cost at the optimum: zero profit by construction. It is not a second revenue line and is excluded from the cash flow.",
  "market_revenue_at_duals_exceeds_cost_at_size_limit": "A size limit binds, so the revenue at the model's prices exceeds the annualised cost by the limit's shadow value: this is not zero profit. It is still excluded from the cash flow, because the bill already prices the same energy.",
  "market_revenue_at_duals_relation_not_established": "How the revenue at the model's prices compares with the annualised cost is not established, because the run did not record how each size was limited. It is excluded from the cash flow.",
  "sector_profiles_have_broad_peaks": "The shipped sector profiles have broad peak plateaus, which a battery cannot shave cheaply.",
  "intake_changed_since_findings": "The study's answers (site, load, tariff or PV) changed after the findings were computed; re-run the study.",
  "intake_changed_since_run": "The study's answers changed after the run; re-run the study.",
  "lcos_two_definitions": "Two levelised costs of storage are shown and they answer different questions: the pro forma's excludes the energy bought to charge the battery, asset economics' includes it.",
  "battery_only_options_sized_to_zero": "Every battery-only option was sized to zero: at this load and on this tariff no battery pays for itself on its own.",
  "synthetic_load_understates_peak_shaving": "A smooth synthetic load lacks the short spikes a real meter records, so the peak-shaving value is likely understated; upload the site's metered load.",
  "value_streams_increment_over_pv_only": "The waterfall shows the battery's increment over the same PV alone, not the option's full saving against the grid-only baseline.",
  "break_even_at_a_tornado_bound": "At least one tornado bound leaves the battery NPV at break-even, within the tolerance below zero, so the claim is that it does not turn negative, not that it stays positive.",
  "robustness_pending": "The tornado did not reach every driver; those not reached are listed.",
  "typical_week_not_in_mvp1": "The typical-week operating chart is not part of this version.",
  "question_has_no_reliability_part": "This question does not ask about reliability or resilience.",
  "no_system_recommended": "The verdict recommends no battery, so there is no system to describe.",
  "breakevens_not_in_mvp1": "Break-even thresholds and the option map are not part of this version.",
  "maturity_not_established": "The study's maturity is not established yet, so its accuracy band is not known.",
  "no_option_named": "The verdict names no option, so there is no case to show.",
  "value_streams_not_established": "The value streams are not established.",
  "verdict_not_established": "The verdict is not established; the reasons are listed.",
  "currency_year_unknown": "No case states a currency year, so the money figure is not shown.",
  "fidelity_unknown": "The run that produced the figure is not recorded, so it is not shown.",
  "not_computed": "The figure was not computed.",
  "ledger_changed_since_findings": "The assumptions ledger changed after the findings were computed; re-run the study.",
  "fork_changed_since_findings": "An option's network changed, or is gone, since the findings were computed; re-run the study.",
  "report_has_no_findings_hashes": "The report does not record what it was computed from.",
  "ledger_changed_during_run": "The assumptions ledger changed while the study was running.",
  "intake_changed_during_run": "The study's answers changed while the study was running.",
  "copied_record_findings_computed_on_the_origin_forks": "This study was copied from another project; its findings and report were computed on the origin's option networks.",
  "run_by_an_earlier_version": "The study was run by an earlier version of the app, which priced its options and valued their investment cases differently, so these results are out of date. Run the study again to bring them up to date.",
  "engine_inputs_changed_since_findings": "An option's tariff or finance settings were edited after the findings were computed (for example in the Expert view); re-run the study.",
  "tariff_export_exceeds_import": "In some hours the tariff credits export above the import price, so the model sends energy out and back through the connection for profit. Check the export price: unless the contract really pays this, the battery's value is overstated.",
  "tariff_export_exceeds_import_via_storage": "The tariff's best export credit, after the battery's losses, is above its cheapest import price, so the model charges the battery from the grid to export later. Unless the contract pays export of grid-charged energy, the battery's value is overstated.",
  "capacity_charge_assumed_connection_size": "The tariff's capacity charge states no contracted capacity, so it is billed on the site's connection size. Enter the contracted capacity from the contract if it differs.",
  "battery_upfront_from_two_parts": "The battery's purchase cost is the sum of its two parts from the assumptions ledger (inverter and storage block), and every screen reads the same figure.",
  "irr_cash_changes_sign_more_than_once": "The yearly cash changes sign more than once (the inverter replacements cost more than a year's saving), so more than one rate could zero the NPV; the one closest to zero is shown.",
  "wacc_field_holds_the_real_rate": "The finance engine's discount-rate field is labelled nominal; on this real basis it holds the study's real discount rate.",
  "wacc_gate_differs_on_rate_bound": "This tornado bound values the case at another discount rate than the one the optimisation sized the battery at; the sizes are kept, so the bound shows the rate's effect on value only.",
  "demand_peak_hourly_resolution": "The demand charge is billed on hourly peaks, the model's time step, not on shorter metering intervals.",
  "lcos_includes_charging_energy_cost": "The levelised cost of storage is what each MWh the battery delivers costs over its life, including the energy that charged it. Energy taken from the grid is counted at the price the site paid for it; energy taken from the site's own PV is counted at the export income the site gave up by storing it instead of selling it. The optimisation's own figure counts all charging energy at the model's hourly price at the site, which also reflects any demand charge, so the two can differ.",
  "finance_rules_from_guided_defaults": "The ledger has no rows for the finance rules (financial close, escalation, contingency, degradation), so the guided study's own defaults were applied.",
  "meter_links_are_not_investments": "The connection meters are part of the site's bill, not of the investment: they carry no purchase cost.",
  "case_streams_do_not_reconcile_with_engine": "The bill savings and the finance engine's operating cash disagree, so the case is not shown."
}
// END HELP MIRROR

// BEGIN PREFIX HELP MIRROR
export const PREFIX_HELP: ReadonlyArray<readonly [string, string]> = [
  [
    "technology_costs_are_",
    "The technology costs are projections for a future year."
  ],
  [
    "tariff_currency_year_",
    "The tariff's currency year differs from the study's."
  ],
  [
    "study_currency_year_",
    "The study's currency year differs from the ledger's."
  ],
  [
    "needs_attention",
    "A user row no longer applies after a change and must be reset."
  ],
  [
    "user_row_not_in_library",
    "A user row has no library counterpart."
  ],
  [
    "key_drivers_without_a_row",
    "A key driver of the question has no ledger row."
  ],
  [
    "bill_unavailable_",
    "A bill could not be computed, so the case is not established."
  ],
  [
    "load_upload_qa_",
    "A data-quality check flagged the uploaded load (all zero, flat, negative or with a spike far above its median); check the file."
  ],
  [
    "reference_",
    "The option's PV-only reference was not computed."
  ],
  [
    "fork_changed_since_findings",
    "An option's network changed, or is gone, since the findings were computed; re-run the study."
  ],
  [
    "engine_inputs_changed_since_findings",
    "An option's tariff or finance settings were edited after the findings were computed (for example in the Expert view); re-run the study."
  ],
  [
    "size_at_upper_bound",
    "A size stopped at its upper limit: the optimiser would have built more, so the limit, not the economics, set the size."
  ],
  [
    "options_not_established",
    "Some options were not solved; they are listed in the options table."
  ],
  [
    "engine_reason:",
    "The finance engine could not establish part of the case; the reason is named."
  ]
]
// END PREFIX HELP MIRROR

// BEGIN FALLBACK MIRROR
export const FALLBACK: { text: string } = {
  "text": "No explanation is recorded for this code."
}
// END FALLBACK MIRROR

/**
 * The sentence behind a code: the tariff's own (`Tariff.honesty_help`) first,
 * then the study's table, a qualified code by its prefix, else the fallback —
 * the order of `report.py::help_for`, so the page and the report agree.
 */
export function helpFor(code: string, tariffHelp?: Record<string, string> | null): { text: string; source: 'tariff' | 'study' | 'fallback' } {
  if (tariffHelp && code in tariffHelp) return { text: tariffHelp[code], source: 'tariff' }
  if (code in HELP) return { text: HELP[code], source: 'study' }
  for (const [prefix, text] of PREFIX_HELP) {
    if (code.startsWith(prefix)) return { text, source: 'study' }
  }
  return { text: FALLBACK.text, source: 'fallback' }
}

// ── budget and run sentences ──────────────────────────────────────────────

/** Gate S4 [N]: the full estimate is charged when the run starts. */
export function budgetSentence(solves: number, options: number): string {
  return `This run solves ${options} option${options === 1 ? '' : 's'}, one solve each: ${solves} solve${solves === 1 ? '' : 's'} `
    + 'are charged to the study’s budget when the run starts. The whole estimate is charged up front, '
    + 'so stopping the run early still counts the full estimate as used.'
}

/** Where one option stands in a run (the Run page's stage list). */
export const STAGE_LABELS = {
  not_run: 'Not run',
  solved: 'Solved',
  solving: 'Solving',
  waiting: 'Waiting',
  not_reached: 'Not reached',
  not_solved: 'Not solved',
} as const

export const RUN_STATUS_LABELS = {
  running: 'Running',
  done: 'Finished',
  aborted: 'Stopped',
  failed: 'Failed',
} as const

/** Why the Expert view could not open an option (the project switch's outcome). */
export const EXPERT_REFUSAL: Record<string, string> = {
  'busy-study': 'The option could not be opened: a study is running on the project you are in. Wait for it to finish.',
  'busy-solve': 'The option could not be opened: a solve is running. Wait for it to finish.',
  'not-found': 'The option’s network no longer exists. Run the study again.',
  'abort-failed': 'The option could not be opened: the running solve could not be stopped.',
}
export const EXPERT_REFUSAL_FALLBACK = 'The option could not be opened. Try again.'

export const EXPERT_WARNING =
  'The Expert view opens this option’s own network in the workbench. If you edit, re-solve or save it '
  + 'there, it no longer matches the study: the study’s pages will refuse to read it (“changed since '
  + 'the run”) and the report will be marked out of date until you run the study again.'

export const BASE_PROJECT_NOTE =
  'This study’s project holds the grid-only network built when the study was created. The options are '
  + 'rebuilt from your answers and assumptions on every run, so editing that network does not change the study.'

// ── follow-ups F1-F: reopening a study, its forks in the project lists ────

/** Getting back to a study after a reload (plan F1-F, F2; gate S8 [S5]). */
export const REOPEN_LABELS = {
  /** The project-home entry: the last open study's project, and its studies. */
  homeHeading: 'Your decision studies',
  homeIntro: (project: string) =>
    `The decision studies in “${project}”, the project of the study you last had open.`,
  lastOpen: 'Last open',
  openStudy: (name: string) => `Open the decision study “${name}”`,
  /** The panel, when the study it was showing is gone. */
  lostTitle: 'The study you last had open is no longer there.',
  lostAction: (project: string) =>
    `It was in “${project}”; it, or its project, has been deleted or renamed. Open another study below.`,
} as const

/** A study's own fork in the project lists (plan F1-F, F3; review v2 [S11]). */
export const STUDY_FORK_LABELS = {
  badge: 'Decision study',
  badgeTitle: (study: string | null) => (study
    ? `Made and used by the decision study “${study}”. Its results are that study’s; leave it to the study.`
    : 'Made by a decision study that no longer exists.'),
  deleteWarning: (fork: string, study: string | null) => (study
    ? `'${fork}' belongs to the decision study “${study}”: it holds one of that study’s options. `
      + 'Deleting it removes the option’s results, and the study’s pages will say the option is not solved '
      + 'until you run the study again. Delete it anyway? This removes its files from disk.'
    : `'${fork}' was made by a decision study that no longer exists. Delete it? This removes its files from disk.`),
} as const

/** The range the robustness check moved a driver over (plan F1-F, F5; gate S9). */
export const TESTED_RANGE_LABELS = {
  tested: 'Range the robustness check tested',
  recentred: 'Re-centred on your value',
} as const
