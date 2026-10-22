# Run the bounded checked-answer evaluation

The CI gate evaluates 274 versioned requests through the authenticated public stream
and owned answer API in disposable projects. Every current default metric in all
nineteen profiles has an independently specified positive expectation. Network
aggregation uses two generators (300 EUR), generator economics uses one
(230 EUR), the StorageUnit trajectory/accounting oracle totals 51 EUR, and the
opt-in `carrier-energy/1` oracle sums two wind generators to 32 MWh (peak 8 MW),
the opt-in loading oracles put line L12 at a 100 % peak against its 8 MVA rating,
the opt-in lost-load oracle sheds 15 MWh over 3 weighted hours (10000 EUR at VoLL 1000),
and the opt-in result oracles give a 25 EUR/MWh time-weighted price, 8.5 t of generator CO2
(6 t at a 40 EUR/t binding cap), 33 of 50 MWh curtailed, a 3 MW build at its bound
0.2 storage cycles and a 6 MWh energy balance with a 4 MWh process shortfall
15 MWh of grid import with 3 weighted hours at the import limit, and a stored
investment case at 25 % with equity and project NPVs of 20 and 40 USD (IRRs 0.5 and 0.75)
with an LCOE of 10 USD/MWh nominal and 8 real.
See the [plan](pilot-evaluation-plan.md) and the hand-worked comments in
[the catalogue](../../pypsa-gui/backend/harness/pilot_evaluation.py).
The additional 80 stress cases cover eight varied numerical scenarios across
all five profiles; see the [calibration guide](calibration-runbook.md).

## Deterministic gate

From the repository root:

```sh
pixi run gui-pilot-evaluation
```

Or from `pypsa-gui/backend`, using the configured test Python environment:

```sh
python tools/run_pilot_evaluation.py --expanded --report /tmp/pilot-evaluation.json
```

The CI backend job runs this before ordinary backend tests and uploads the JSON
and bounded pytest log, including on failure. Actions must be enabled in the
repository for that job to execute; local success is not remote CI success.
The ordinary backend suite also executes the deterministic cases and scorer/
budget regressions. Its five optional live cases skip without explicit opt-in;
the standalone default gate does not collect them or count them as passes.

Missing or duplicate IDs, skips, test/fixture/collection errors, wrong results,
missing positive fulfillment, false accepts/rejects, unchecked publication and
timeouts produce a nonzero exit. The runner overwrites any prior passed report
before launch, checkpoints progress and records source commit, a hash of all
backend Python sources, oracle/manifest/policy versions and separate deterministic
and live denominators. A report's metrics include scored failing observations;
unobserved cases remain explicit failures and cannot silently shrink required
coverage. Zero-denominator rates are null. No report grants runtime approval.

## Optional live evaluation

This command opts into **paid model generation** for the five golden requests:

```sh
python tools/run_pilot_evaluation.py --report /tmp/pilot-live.json --live --live-model gpt-6.1-sol
```

It reads the injected `PYPSA_GUI_OPENAI_API_KEY` or existing `OPENAI_API_KEY` in
memory and uses the official provider adapter. Profiles, database, projects and
answer records are disposable; existing application settings are not changed.
No credential is written to a profile/report or printed.

Fixed shared ceilings reserve **every HTTP attempt**, including adapter retries,
before send: 10 requests, 65,536 serialized payload bytes per request, 512,000
aggregate input bytes, 2,048 output tokens per request, 20,480 reserved output
tokens overall, and a 240-second admission deadline. HTTP connect/read timeouts
are 10/15 seconds; the standalone live process has a 300-second overall deadline
including fixtures. No reservation is refunded on absent usage, rejection or
interruption. Inherited pytest worker/selection options are removed so they
cannot multiply the meter or reduce coverage. The transport refuses redirects
and non-official destinations. These are resource ceilings, **not a dollar-cost
quote**; context accounting and provider pricing remain external.

Deterministic output is scripted at the provider seam. Live output is generated
by the selected model, but only five supported golden cases run live; adverse
drafts and source defects remain deterministic. Live failure/incomplete execution
fails the gate, with raw output, prompts and exceptions omitted from the report.
Without `--live`, live evaluation is explicitly `pending_opt_in`.
The standalone command without `--expanded` retains the 53-case core suite.

This is a finite-suite acceptance gate, not a population error estimate,
qualitative calibration or independent engineering approval. Broader language,
domain/browser/voice/report coverage and production release remain pending.

See the committed [validation record](pilot-evaluation-validation.json) for the first integrated run and its explicit remaining scope.

The first user-approved live run passed five goldens (33 result slots) alongside the 53 deterministic cases on pinned source `53f738ca`. See [the live validation](pilot-live-validation.json). This is finite acceptance evidence, not live adverse/population calibration; future paid runs still require their own opt-in.
