# Generator category: a word match, not a substring

Status: ready-for-agent (GK triage 2026-10-23; outsourced task F, docs/agents/outsourced.md)
Type: task
Blocked by: ts-05 (#586)
Gated: yes (adequacy reads it)
Model tier: opus

Follow-up noted in the reviews of #586.

## Gap

`services/profile_shapes._gen_category` tests `'dr' in carrier`, so "hydro" and "hydrogen" file under demand response. Time Series puts them on the Demand Response tab and Properties shows them no editors; `adequacy/eh_readiness` reads the same function.

## What to build

Match the demand-response keywords as words (or a carrier list), with a test per keyword family, and check the adequacy readers that call it. Owner: whoever holds `profile_shapes` / adequacy; it needs the gatekeeper because adequacy reads it.

## Done when

A test shows "hydro" and "hydrogen" generators no longer file under demand response (hydro under renewable or its own class), a "dr" or "dsm" one still does, and `adequacy/eh_readiness` (a co-reader) keeps its readiness tests passing.
