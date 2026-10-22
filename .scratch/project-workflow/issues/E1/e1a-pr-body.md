Closes # (n/a): slice E1a of workspace reconciliation §13 (E1, examples for any planning years; owner Q3 = B).

## Changes proposed in this Pull Request

The staged data-center example can be created for any planning years entered at create. With no planning entry, every template builds exactly as before.

**Input.** `POST /api/projects/from_template/eh_datacenter_staged` takes `{"planning": {"start_year", "end_year", "dates"?}}`, alone or beside `dc_load`.
- The periods are the start year plus the dates.
- With no dates, the example's spacing (+0 / +3 / +8) is shifted to the start year, and any stage at or after the end year is dropped. This is **owner decision E1-stages (2026-10-21)**.
- Years must be whole years from 2025 to 2060, with at most 3 periods, increasing and before `end_year`.
- `start_year == end_year` is refused on a staged example ("use the single-year example").
- A refusal is a typed 422 `planning_invalid` naming its field, and nothing is created. A build that fails the shipped-template rules (`process_template_rules.refusals`) is refused the same way.

**Weights (gatekeeper condition 1).** `ipw.years` run to the next period, the last to `end_year`, and `ipw.objective` stays equal to the years. The solve's existing `auto_discount_periods` discounts them; there is no new weighting code.
- Σ `ipw.years` == `analysis_years` == `end_year − start_year`.
- Each period's week weighs 8,760 h, so PyPSA's per-period `nyears` stays equal at unequal period lengths.

**Default unchanged (condition 2).** For every EH template, the default build and the build at the native planning were checked locally against master's network and sidecar files, and are canonically identical with byte-identical sidecars. The test pins this for the staged DC.

**Placeholders (condition 3).** An IT MW or energized MW the example has no figure for in an entered year takes the example's value for the same stage. It is never interpolated.
- `eh_template.json` gains `growth_inputs`, with `set: false` and `source: "unset"` for these values, plus a `planning` block.
- Each placeholder gets an `assumed` ledger row with a section target, so the flow warns until it is confirmed. There is no flow code change. Its note says which stage the value comes from ("the example's 2035 value is not used: this planning puts 2035 in stage 2, so it takes 24 MW"), and that it stays a placeholder: nothing sets one yet, which is follow-up [E1-set-growth-placeholders.md](https://github.com/MLHaoChang/pypsa-eur/blob/claude/e1a-planning-years-staged-dc/.scratch/project-workflow/issues/E1-set-growth-placeholders.md) (`ready-for-agent`).
- The flag format is `growth_placeholder:<kind>_<year>`, pinned by a test for E1b–E1d.
- `site_peak.peak_check(…, unset=)`, the DC-01 create check and the scorecard's `energization_peak:<period>` read unknown for those periods. They never refuse on a placeholder.
- The investment case gets `growth_placeholder:<kind>_<year>` flags. A live-solve test checks the IC report lists each flag, with one plain-words sentence per placeholder in its notes.
- The chat tool's `solve_ppa_price` builds its case with the same flags and lists them in its result's `flags`, as it lists the campus flags (`services/chat_tools.py`; a test pins it).
- The run is allowed. A test creates the project, reads the flow warnings and the unknown scorecard rows, and solves.

**Horizon (condition 4).** The case's `analysis_years = end − start`, `financial_close = start − 1` and every existing COD = start. A test checks that the operating years equal the periods' covered years. The pack's money year (2020) is not re-dated, and the ledger says so. The cost basis stays flat; per-period cost sourcing is the separate gated follow-up.

**Paths by year (condition 5).** The staged DC reads one path by year, the import tariff, and it is now valid from the start year. A start before 2027 holds the 2027 rates from the start year, with an assumed ledger row "assumed, held from 2027" that the flow lists as a warning in Commercial. Energization is read by stage, not by year: each period takes the example's same-stage value as a placeholder. So no "held from" step-back applies here. The CO₂ / H₂ / NH₃ paths come in E1b and E1c.

**DC-01.** `dc_load` phases must match the entered periods (the existing 422, now against the new years).

**Behaviour change.** A `{}` body on `eh_datacenter` or `eh_datacenter_staged` was refused before (`dc_load_invalid`). It now creates the default project, like no body.

Files:
- `project_templates/eh_templates.py`: `Planning`, `planning_spec`, `_dc_plan`, `_dc_staged_meta`, and `template_meta` / `solver_config` / `island_config` / `write_sidecars` taking a plan.
- `routers/projects.py`: the `planning` parameter.
- `services/adequacy/site_peak.py`: `unset_periods` and `unset=`.
- `services/adequacy/grid_readiness.py`.
- `routers/results.py`: `_ic_growth_flags`.
- `services/finance/baseline_text.py`.
- `services/chat_tools.py`: `solve_ppa_price` passes and lists the growth flags.
- The investment-case test fakes of `_ic_build_case` take the new `growth_flags` keyword.
- Test: `tests/test_e1_planning_staged_dc.py`.

**Review.**
- `mp-code-review` was run. The fixes it prompted are in this PR: `error_kind` on the new 422s, plain-word IC flag text, and the inverter-note and growth-row cleanups.
- One finding stays: the scorecard's ramp row is one row for the site, not per period. It reads unknown here because there is no AI-load envelope without `dc_load`; the test comment says so.

**Open items.**
- The report's assumptions appendix doesn't read the template ledger today. Listing placeholders there is a new report seam, split out with the gatekeeper's OK: [.scratch/project-workflow/issues/E1-report-template-ledger.md](https://github.com/MLHaoChang/pypsa-eur/blob/claude/e1a-planning-years-staged-dc/.scratch/project-workflow/issues/E1-report-template-ledger.md) (`ready-for-agent`).
- The chat tool's description doesn't mention `planning` yet: its own small PR after this one merges (coordinator, 2026-10-21).
- The Builder's preview of the resulting periods is W2's.
- E1b: H2 hub and microgrid staged. E1c: ammonia (with g2p). E1d: single-year templates and IPP (with the IPP session).

AI-generated: this description and the code were written by Claude Code.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked.

**If applicable:**
- [ ] Changes in configuration options are reflected in `scripts/lib/validation`.
- [ ] For new data sources or versions, [these instructions](https://pypsa-eur.readthedocs.io/en/latest/data_sources.html) have been followed.
- [ ] New rules are documented in the appropriate `doc/*.md` files.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01CCDKueCSjJ1M7W2uP254JF
