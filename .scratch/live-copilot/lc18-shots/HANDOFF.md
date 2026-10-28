# LC-18 hand-off (2026-10-28, builder session_01G7qCum6hHGQRxaTwC8ZDBn, at 302k context)

The coordinator (session_01G7qCukVvzqtK2GPFkZjVvj) was inactive when this note was sent; it is kept here instead.

- Branch `claude/lc18-sensitivity-compare` from master 21cef975. No PR yet.
- OKs: GK B plan GO (session_01G7qCuo9wYsCcnqwYtKcsqV; its conditions are covered by code and tests). UX plan GO (session_01G7qCuipbbh8qmQqjFHg8e9: assistant table, tool-given labels, plain-word reasons, offer-only Compare with Back, shots in Guided and Expert). No NOT OKs.
- Out-of-plan fix, reported to GK B: `services/workflow_tools._record_solve_inputs` now fingerprints the solving context (user_ts included). Without it, every queued solve of a project with an uploaded profile reads "out of date" forever. Test: `test_cases_with_an_uploaded_profile_read_as_current`. GK B was also told its "CAPEX is annual" note is wrong: the engine pro-rates CAPEX by nyears.
- Green: `tests/test_sensitivity_comparison.py` (guard mutations each caught). Also green before the workflow_tools fix: inventory, capability_overview, edit_card, experiment_comparison, two_case, openai_tool_selection, sensitivity_chat, error-kind manifest.
- Not yet confirmed after the fix: lc12_stale_result_reads, exact_fingerprint_assurance, oj07, loadflow_summary, conversation_recovery, chat_tools_dispatch, project_changes_multi, harness_reliability, assistant_capabilities.

Next:
1. Run those suites.
2. Shots: move `seed.py` and `shot_lc18.js` to `pr<N>/` on claude/campus-ic-02-05-shots and delete this folder from the PR. The chat-table shot works. The Compare shots still show the Projects home, because no project is open: open "Campus base" first. "Back to" was not visible: find where FlowReturn's button renders for Results/compare.
3. mp-code-review against the issue, open the PR, ask GK B and UX for OKs, drive CI.

Risk: services/ overlaps with open PRs #795, #797 and #802.
