<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Research step: provider web search, cost cap, source ledger

Status: ready-for-agent
Type: task
Blocked by: 04
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-RESEARCH-1, TOOLSEL-1, AI-567b (values shown beside their source and confirmed before they are written)
Design: [design.md](../design.md) §8
Plan verdict: GK B GO, 28 Oct (B3)
Owner decision: SH-RESEARCH-CAP-1 (€2 default per run, raisable on the envelope up to a €10 hard cap)

## What to build

1. A provider capability for web search/fetch in `harness/providers` (no vendor words outside it), offered only in research turns (stage 2 `researchable` gaps, stage 6 sourced ranges). Not a catalogue tool.
2. Cost cap from `envelope.research.cost_cap_usd`, counted from provider usage; at the cap research stops and the gaps are listed.
3. Source ledger rows (design §8); a value enters the model only through the edit card (or `ledger.apply_user_row` for a decision-study row) with its source beside it, always carded; run `needs_user` with `research_confirm` while values wait.
4. Unit check: refuse a value in another unit.
5. A provider without web search: `not_available`, defaults used, gap listed.

## Acceptance criteria

- [ ] A chat turn never offers web search; a research turn does.
- [ ] A researched tariff value is shown with url and quote and is written only after the card click; inside a granted envelope it is still carded.
- [ ] The envelope proposes €2; raising it above €10 is `invalid_envelope`; cost over the cap stops research mid-stage; `run.budget.used.research_eur` is from provider usage, converted server-side at the stated rate.
- [ ] A rejected value stays `rejected` in the ledger and the report.
- [ ] Layout test: no vendor word outside `harness/providers`.
- [ ] B3: a research turn offers read-only tools plus the source-ledger write; it cannot call `update_component` even when the envelope would allow it.
- [ ] Fetched content is framed as data: an injected instruction in a fetched page does not change the tool subset (test with a fake provider page).
- [ ] An `apply_user_row` from a research value is the card's effect, never envelope-admitted.
- [ ] Source integrity: a `SourceRow` with a URL not fetched in that turn, or a quote not verbatim in the fetched content, shows "unverified source" and is never presented as sourced (fabricated URL and fabricated quote tested).

## Likely files

`pypsa-gui/backend/harness/providers/`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_envelope.py`, `pypsa-gui/backend/tests/test_harness_research.py` (new)

## Comments

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov). HH-07 (sourced research, values the user confirms) adds checks to this issue's source ledger. Nothing below touches SH-RESEARCH-CAP-1.

1. **Verified quotes.** A source row records the fetched content's hash and where the quote sits in it (`FetchedSource`: url, fetched time, content sha256, retained content, locator). Only server code writes the verification field; the model only proposes a source. A quote the retrieved text does not contain is `unverified` and is never shown as a source for a value. A plausible-looking citation is not engineering truth.
2. **Value, unit and basis.** The proposed value carries its unit and basis (EUR/MWh versus EUR per litre of fuel, nameplate MW versus configured MW). A value in another unit is refused, as step 4 above says; a fuel price is never reinterpreted as an electricity tariff.
3. **Conflicts and ranges.** Competing sources for one gap are kept side by side with their ranges; the user picks one or rejects all. The pick, the rejected rows and the assumption left uncertain all enter the report (issue 07's source ledger chapter).
4. **Proposal first.** A proposal the user selects is useful on its own, so the first delivery is proposal-only. A value is applied only where the target has a qualified reader and edit adapter: for eh_site@1 the tariff (`attach_tariff`) and the weather choice, each through its card. #970's first application (`Generator.p_nom` from a datasheet) is not an eh_site@1 gap; decide whether to add it only on an owner request.
5. **Cap.** The research step's cost cap stays €2 by default and €10 at most (SH-RESEARCH-CAP-1). Issue 24's durable reservation counts research sends against this cap and the run's total; it must not lower or block it.

HH-07 also lists HH-10 (immutable solves, issue 05's folded section) as a prerequisite before any automatic solve follows a researched value; here every researched value is carded anyway, so nothing automatic follows until 05's section is built.

## GK B plan re-confirm (4 Nov, against master 2501ae2c)

GO WITH CHANGES; the 28 Oct B3 GO still holds.

- Q1: eh_site@1 is changed in place (no @2; the pilot has no shipped runs, and L2 lands in @1 too). Research runs only when the run's granted envelope has `research.allowed`; a run granted before research existed never researches (tested).
- Q2: the source-ledger write is a `record_source` catalogue tool, built after #1079 merges.
- Q3: never overshoot (SH-RESEARCH-CAP-1 is a hard cap). No own metering stop: the research provider wraps SH-24's reservation wrapper, `Research(Reserving(base))` with `scope="research"` reading `envelope.research.cost_cap_eur`, so the reservation sees the server tools; SH-24 bounds search/fetch fees by max_uses × fee. `run.budget.used.research_eur` comes from SH-24's settled holds, not a second meter.
- R1: server tools are offered only on research turns; a test shows L1, L2 and chat requests never carry them.
- R2: the verification text is this turn's retained fetched text; the quote match is whitespace-normalised only (NFC, no case or other Unicode folding).
- R3: retained page text is size-capped; fetched content offered back to the model is fenced as untrusted data.
- R4: a confirmed research value is written only through the gap tool's own card; an envelope covering attach_tariff still cards a `research_ref` call (tested).
- R5: `research_unit_mismatch`, `research_not_available` and `research_cap_reached` go in this branch's tool-error-kinds.d fragment.
