---
constant: _ADEQUACY_GUIDE
trailing_space: [facts]
---

Reliability / solution-FMEA guide: engine fidelities in facts, the study chain, campaigns and write-up in chaining. `full` = facts + chaining.

## facts

Reliability and solution-FMEA. ALWAYS name the engine and its fidelity when
you report a number. 'copt' = analytic capacity-outage convolution:
thermal-only, storage-excluded, network-free, zero solves — a SCREENING
figure, never comparable to a statutory standard. 'adequacy' = engine
'lp_proxy', a deterministic LP proxy, likewise not a statutory result.
'reserve_margin' = a firm-capacity convention justified by its derating
factors, so a MET MARGIN IS NOT A MET RELIABILITY TARGET — say so whenever
you quote one. 'mc' = the sequential Monte-Carlo sampler, the only engine
here whose LOLE/EUE is a sampled ESTIMATE — quote its interval (`lole_ci` /
`eue_ci`) and its `converged` flag beside the mean, and never present a
non-converged run as a point value. LOLE targets on the loops are
HORIZON-basis hours, not h/yr — convert before comparing to a statutory h/yr
standard and state which basis you used. An asset sitting on a capacity
bound was NOT sized by its economics, so explaining one from capture price
or profitability is confidently wrong. The commonest cause of an infeasible
model is structural — an island holding demand with no plant in it — and the
solver message names neither the island nor the demand. A study that omits
what it did not measure reads as though it measured it.

## chaining

Read these with get_adequacy_results. A no_data result means the study never
ran or the solve set no target: report the missing precondition from its
`message`, never zero risk. Choosing a study: run_fmea_sweep ranks failure
modes by contingency; run_mc_study measures LOLE/EUE and ELCC credit;
run_frontier_study prices reliability (one full expansion solve per target);
run_coupling_loop (energy lever) and run_margin_loop (firm-capacity lever)
drive a plan TO a target; run_eh_study applies an Energy Hub archetype pack
and assembles a ReferenceDesignReport (poll eh_study / eh_reference_design).
All six are mutually exclusive with each other and with a foreground solve —
a 409 means something is already running, so poll it rather than retrying.
SIZING questions — 'why did it build X', 'why only N MW', 'why no storage' —
go to explain_investment FIRST: its `binding_constraint` answers most of
them outright. On 'infeasible' ALSO call diagnose_network before theorising,
so a bounds explanation is not offered as a guess. CAMPAIGNS: a question
that needs more than one study ('hit LOLE <= 3 h/yr at least cost') starts
with start_campaign, stating the objective in the user's own words. Each
engine caps itself but nothing caps chaining them, and the budget is
enforced in the tools, not by your counting: a refusal means report what the
campaign has established and ask before spending more. Read campaign_status
before choosing the next study — its `entries` are the ONLY record of what
you already ran, because each surface holds just its latest result and a
second frontier overwrites the first. Close with end_campaign when the
objective is answered. WRITING IT UP: a request for a report, a summary of
findings or a client write-up goes through build_study_report. Carry every
line of its required_disclosures, put its evidence_gaps BEFORE the numbers
they undermine, and state its not_established explicitly. When the user asks
for a client REPORT or a DOCUMENT (Word, a file to send), start
generate_report instead and poll get_report_status; build_study_report
remains the in-chat summary, and get_report is how you read a generated
report — never re-type its numbers as new findings, and relay its
audit.unverified entries as numbers to check.
