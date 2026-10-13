// Payloads the backend actually produced: the S4 fake solver through the real
// study routes (`backend/tests/test_study_tornado_routes.py` fixtures), dumped
// once and trimmed (long arrays cut; `explain` to one entry; report section
// bodies emptied). Tests derive their variants from these, so the shapes the
// pages read are the shapes the routes send.
//
// U2 WP8 part B (plan §5.4): renamed in place to the names the routes send
// since U2 — `tariff_engine` / `finance_engine` (with the case's
// `tariff_engine:counterfactual` bill ref and engine list), `levelised_cost`
// (+ `levelised_cost_basis`) and `terminal_value_eur`, and the case's LCOS
// note to the engine's. The figures are the S8 dump's. The S8 originals are
// kept verbatim as the backend's stored pre-U2 study
// (`backend/tests/fixtures/pre_u2_study/`, `test_study_engine_literal_compat.py`).
import type {
  DecisionReport, DecisionStudy, Findings, IntakePreview, InvestmentCase, LedgerPayload,
  RunRecord, StudyLibrary, TornadoRecord,
} from '../../../api/decisionStudies'
import studyJson from './study.json'
import ledgerJson from './ledger.json'
import runJson from './run.json'
import findingsJson from './findings.json'
import findingsPvJson from './findings_pv.json'
import findingsPreJson from './findings_pre.json'
import findingsPvPreJson from './findings_pv_pre.json'
import reportJson from './report.json'
import tornadoJson from './tornado.json'
import caseJson from './case.json'
import libraryJson from './library.json'
import previewUploadJson from './preview_upload.json'
import previewUnitUnknownJson from './preview_unit_unknown.json'

export const study = studyJson as unknown as DecisionStudy
export const ledger = ledgerJson as unknown as LedgerPayload
export const run = runJson as unknown as RunRecord
/** Battery-only options, tornado done: `recommended`, streams against the baseline. */
export const findings = findingsJson as unknown as Findings
/** A `bess_pv` verdict: streams are the battery's increment over PV-only. */
export const findingsPv = findingsPvJson as unknown as Findings
/** Before the tornado: verdict not established (`tornado_not_run`). */
export const findingsPre = findingsPreJson as unknown as Findings
/** PV option not attributable yet: `options_not_all_judged`. */
export const findingsPvPre = findingsPvPreJson as unknown as Findings
export const report = reportJson as unknown as DecisionReport
export const tornado = tornadoJson as unknown as TornadoRecord
export const optionCase = caseJson as unknown as InvestmentCase
export const library = libraryJson as unknown as StudyLibrary
/** A kW upload with a spike and a peak above the 2 MW connection. */
export const previewUpload = previewUploadJson as unknown as IntakePreview
export const previewUnitUnknown = previewUnitUnknownJson as unknown as IntakePreview
