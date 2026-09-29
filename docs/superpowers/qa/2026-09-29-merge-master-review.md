# Independent review: merge of `origin/master` into `claude/epic-allen-k2t1c4`

**Date:** 2026-09-29.
**Merge reviewed:** `f44ae8f33`. Its parents are the branch at `65135991b` and master at `6e9fd4bb0`; the merge base is `ec2330274`.
**Also on the branch:** `2970552b3`, which records the owner's answers. It changes docs only.
**Merger's record:** `2026-09-28-merge-master-decisions.md`, plus the spec §4 and §9 amendments.
**Method:** the runbook `runbooks/2026-09-12-auditing-a-merge-into-a-moving-master.md`.

## Verdict: **NO-GO** (two blockers, both small)

The merge is careful and mostly right:

- No function or route from either side was silently lost.
- Master's security hardening survived.
- Guided-mode safety survived.
- The EH semantic decisions match the code.

Two defects remain. Each has a repro below. Fix both test-first, then re-run the checks in §5. Neither fix needs a redesign.

| # | Blocker | Kind |
|---|---|---|
| B1 | The chat tool `put_stress_scenarios` crashes for every real project in server mode. The adapted test hides this. | A semantic merge defect: branch P22 code meets master's 68e5f62c3 lock gate. |
| B2 | The owner's Q3 rule (a knee needs 3 points) is not enforced anywhere. An existing test pins the opposite. | The owner made this a precondition for merging into master. |

---

## 1. Blockers

### B1. The chat tool `put_stress_scenarios` crashes with `AttributeError` in server mode

**Where**

| Location | What happens |
|---|---|
| `pypsa-gui/backend/services/chat_tools.py:1515-1524` | The branch's P22 tool calls the router handler directly with only `body=` and `project=`. |
| `pypsa-gui/backend/routers/adequacy_worksheet.py:127-138` | Master's 68e5f62c3 added `db: DBSession = Depends(get_db)` and `user = Depends(optional_user)` to this handler. It then runs `_check_project_lock(db, _lock, user)`. |
| `pypsa-gui/backend/routers/projects.py:716-720` | `user` is the raw `Depends` sentinel, not `None`, so there is no early return. `project_locks.get_lock(<Depends>, id)` then raises `AttributeError: 'Depends' object has no attribute 'get'`. |

**Why the suite is green**

The merger adapted `tests/test_energy_hub_review.py:237-241` (`test_get_eh_template_and_put_stress_scenarios_via_chat`) to give the project double `uuid="u-1"`.

`_lock_target` treats a non-uuid as "no lock row" and returns `None` (`adequacy_worksheet.py:63-78`). The lock branch, and the crash, is therefore never reached.

In production, `_authorized_project` returns a real DB uuid. The decisions doc says "No lock is bypassed". That is true, but the adaptation is what hides the defect.

**Impact**

- The P22 loop is broken for every server deployment: the assistant cannot apply a recommended Class-C scenario.
- A foreign-lock refusal surfaces as an internal error, not a 409 `project_locked`. For the lock holder the write is refused outright.
- Desktop or local mode is unaffected, because `_check_project_lock` returns early there.
- It is fail-closed: nothing is written. It is a correctness defect, not a bypass.

**Repro.** The file is `scratchpad/qamerge/test_repro_chat_stress_lock.py`. It uses the suite's own fixtures and runs as the bound seeded user in server mode.

```
cd pypsa-gui/backend && PYTHONPATH=…:. python -m pytest <scratch>/test_repro_chat_stress_lock.py -p tests.conftest -q -o addopts=""
FAILED test_holder_can_put_stress_scenarios_via_chat - AttributeError: 'Depends' object has no attribute 'get'
FAILED test_non_holder_chat_write_is_refused_with_409 - AttributeError: 'Depends' object has no attribute 'get'
1 passed (the control: _authorized_project returns a real uuid)
```

**Fix.** Call the handler through `_route`, which injects `db` and `user` and fails loudly on any `Depends` it cannot satisfy:

```python
return _route(_h, body=StressScenariosPut(scenarios=scenarios),
              project=_authorized_project(name))
```

**Red tests to add first.** Add the two failing cases above to `tests/test_energy_hub_review.py` or `tests/test_worksheet_foreign_lock.py`:

- the holder succeeds through chat;
- a non-holder gets 409 `project_locked`.

Keep the existing `uuid="u-1"` case as the no-lock-row control.

**Guard.** A scan found one other direct handler call with an unsatisfied `Depends`: `update_solver_config` → `routers.simulation.update_solver_config` (`db`, `actor`). That call is identical on master, so it is pre-existing and outside this merge (see N6).

The scan is `scratchpad/qamerge/scan_direct_calls.py`: an AST walk of `chat_tools.py` that finds calls to router handlers and checks for `Depends` defaults left unsupplied. Worth adding as a test.

### B2. The owner's Q3 knee rule is not enforced (backend, review or panel)

The owner ruled on 2026-09-29 (decisions doc, final table): "A curve needs at least 2 points; the knee needs 3. Below 3 points the knee is `not_established` (never reported). This must be verified in code before the merge into master."

**The merged code reports a knee from two points**

| Layer | Location | Behaviour |
|---|---|---|
| Engine | `services/adequacy/frontier.py:290` | `knee_index` returns `None` only when `len(ok) < 2`. With 2 OK points and a VOLL crossing it returns `0`. |
| EH stage | `services/adequacy/eh_study.py:621` | `"knee_index": fr.knee_index(points, voll)` is passed through with no ≥ 3 check and no knee status. The section is `ok` from 2 points, which is correct for the curve. |
| Review | `services/adequacy/eh_review.py:247-266` | Emits a `frontier_knee` finding, including a "re-plan at the knee target" action, from any `knee_index`. |
| Panel | `frontend/src/pages/results/EhReferenceDesignPanel.tsx:1116-1117` and `:1784-1797` | Marks `data-knee="true"` and the "knee" label whenever `knee_index != null`. |
| **Test pinning the opposite** | `frontend/src/pages/results/EhReferenceDesignPanel.test.tsx:510-553` ("renders the frontier and FMEA top-N tables…") | Its fixture has **2** OK points plus 1 infeasible, with `knee_index: 0`, and asserts `eh-frontier-row-0` shows "knee" (line 553). |

No backend test pins the 3-point rule. `tests/test_adequacy_frontier.py:385-398` covers only one point and no crossing.

**Repro.** The file is `scratchpad/qamerge/test_repro_knee_rule.py`. The stage test stubs `run_frontier_sweep` to return 2 OK points with a crossing.

```
FAILED test_frontier_with_two_points_reports_the_curve_but_no_knee - assert 0 is None
FAILED test_review_never_reports_a_knee_from_two_points - AssertionError: ['frontier_knee']
```

A direct call reproduces it too: `knee_index([{ok,cost 100,ens 10},{ok,cost 1e9,ens 9}], 1000.0)` returns `0`.

**Exact change**

1. **Backend stage (authoritative).** Define the constant `MIN_EH_FRONTIER_KNEE_POINTS = 3` beside `MIN_EH_FRONTIER_POINTS` in `eh_stages.py:46`. Then, in `eh_study._stage_frontier`:
   ```python
   if len(ok_points) >= MIN_EH_FRONTIER_KNEE_POINTS:
       knee, knee_status, knee_note = fr.knee_index(points, voll), "ok", None
   else:
       knee, knee_status = None, "not_established"
       knee_note = f"a knee needs at least 3 solved points; {len(ok_points)} solved"
   ```
   Write `knee_index`, `knee_status` and `knee_note` into the payload. The section status stays `ok` from 2 points.

   Leave `frontier.knee_index` itself alone: the standalone adequacy frontier (`frontier_loop_runner.py:102`) is outside the ruling. If the owner wants the rule everywhere, move the `< 3 → None` into `knee_index` instead. That single change also covers the stage.
2. **Review.** Update `eh_review.py:249-250` to add `and len(ok_points) >= 3`, so a report stored before the fix never yields a knee finding or action.
3. **Panel.** At `EhReferenceDesignPanel.tsx:1116`, compute `kneeTarget` only when `okFr.length >= 3`. When the frontier is shown with fewer than 3 OK points, render a `data-testid="eh-frontier-knee-note"` line saying "Knee not established: needs at least 3 solved points". Add `knee_status` and `knee_note` to the frontier payload type in `api/simulation.ts`.

**Red tests (write first; each fails on `f44ae8f33`)**

| Test | Location | Asserts |
|---|---|---|
| `test_frontier_with_two_points_reports_the_curve_but_no_knee` | `tests/test_energy_hub_frontier_stage.py` | `completeness["frontier"] == "ok"`, `knee_index is None`, `knee_status == "not_established"` |
| a positive control | same file | 3 OK points with a crossing still give an integer `knee_index` and `knee_status == "ok"` |
| `test_review_never_reports_a_knee_from_two_points` | `tests/test_energy_hub_review.py` | no `frontier_knee` finding |
| "does not mark a knee with fewer than three solved points" | vitest | the existing 2-OK fixture with `knee_index: 0` shows no `eh-frontier-knee` and does show `eh-frontier-knee-note` |

The existing assertion at `EhReferenceDesignPanel.test.tsx:552-553` must change with it: it pins exactly the behaviour the owner ruled out. Record that edit as an owner-driven test change. The 3-OK-point case at `:1296-1412` (`knee_index: 1`) should keep passing unchanged.

The QA driver `tests/qa_eh_reference_design.py` should also assert, for its 5-point run, that `knee_status` is `ok` whenever `knee_index` is an integer.

---

## 2. Loss audit

### 2.1 `merge_audit.py`

**Self-test:** passes (29 assertions).

**Audit:** exit 1, 10 findings. Command:

```
merge_audit.py --ours 65135991b --theirs 6e9fd4bb0 --merged f44ae8f33 \
  --inventory '"error_kind":\s*"(\w+)"' --inventory 'yield\s+"(\w+)"' --inventory 'data-testid=…'
```

The audit took about 1 minute. Every finding was traced:

| Finding | Verdict |
|---|---|
| master `DtcConfig._refuse_per_load_attribution` absent | Master's edit was cosmetic (a return annotation). Its removal is recorded (§1: branch P16 `per_load`). OK. |
| `topology_analyzer._EXTRA_LINK_BUSES`, `_peak_load` (ours) | Master's #57 refactor removed them ("one bus-graph walk"). The branch never touched the file. OK. |
| `eh_stages.run_frontier_stage`, `run_mc_certify_stage`, `run_fmea_top_stage`, `frontier_targets_for` (theirs) | Removed per S1 and recorded. No remaining references in code. Two historical findings docs still name them. OK. |
| `test_adequacy_campaign::test_the_sweep_charges_its_contingencies_plus_the_closing_re_solve`, `test_energy_hub_archetypes::test_energy_import_field_warns_and_stays_power_only` | Present at the base and on master, and master never touched them. The **branch** replaced them before the merge: P10 `279ef54ed` and P17 `be8047dab`, each with a successor test. Not a merge loss. |
| `test_energy_hub_frontier_stage::test_a_custom_ladder_is_used_and_needs_three_factors` (theirs) | **Renamed** to `…_needs_two_points`, and its second assertion inverted (a 2-factor ladder is now accepted). This follows from Q3 and S11, but §3 of the decisions doc does not list the rename (N3). |

**Other checks:**

- The duplicate-definition check and the rewound-constants check report 0.
- `ruff --select F821,F811`: the merged tree has the same 20 findings as the branch parent. The merge introduced none.
- `test_tool_error_kind_manifest.py`, which covers `error_kind`, passes.
- No `yield "<frame>"` SSE name was lost.

**Frontend.** `merge_audit` covers Python only, so I checked this by hand: every `export` and `data-testid` in either parent's non-test `src`. Only master's `eh-report-mc-lole` and `eh-report-verdict` are gone. That removal is recorded (one headline), and no smoke or e2e script references them.

**Hand edits isolated.** I recreated the mechanical merge (`git merge-tree` → `16120499c`) and diffed it against `f44ae8f33`. The hand edits touch 27 files, and every one appears in the decisions doc.

### 2.2 Master's security hardening

| Control | Status in merge |
|---|---|
| Chat session ownership (`be4d5ed2f`, `7a6edcc2f`) | `routers/chat.py` is byte-identical to master. Both creation sites record `owner_user_id`. `/confirm`, `/rewind` and `/abort` check `session_owner_allows`, fail-closed. |
| Untrusted-data fence and linear neutraliser (`70e2352c8`, `c671f5e83`) | `_neutralise_untrusted_delimiters` is intact. `_sanitise_ui_value` = work cap → master's linear neutraliser → the branch's P25 loop. The loop cannot iterate after a correct neutraliser, and it is bounded by the work cap. Whitespace collapse and truncation cannot re-form a delimiter. This keeps the stricter behaviour of the duplicate, and both test sets pass. |
| Lock-holder email kept from the LLM (`30d62065c`) | This is structural, in `_error_result_content`, so the branch's new tools inherit it. |
| Foreign-lock gate on `/api/results/` (`ac4bd8bfe`) | Prefix middleware. The branch added only GETs under `/api/results/`. (Pre-existing master gap: N5.) |
| Sidecar PUT lock (`68e5f62c3`) | The HTTP routes are intact. **The chat path is broken: B1.** |
| User-code admin gate and bundle bypass (`83d50f049`, `a33ee07a8`) | Unchanged from master. |
| Path containment (`85df84339`, `12e4fef74`, `a730db7bc`, `d058de452`) | The files are unchanged relative to master. The branch added no `iterdir()`-based lookups. |

### 2.3 Guided-mode safety (branch)

| Control | Status in merge |
|---|---|
| Write confirmation in Guided | `GUIDED_CONFIRM_TIERS` and `_confirm_tiers(guided)` are threaded through `_dispatch_tool_uses` → `_dispatch_real_tool_call` → `_confirm_destructive_tool`, and through the pre-dispatch validator. The code matches the branch. |
| `ui_context` allow-list | `_format_ui_context`: `ui_mode` is exactly `"guided"`, and `guided_step` is allow-listed via `_GUIDED_STEPS`. Unchanged. |
| The P25 sanitiser | Kept, as above. |
| Live-network isolation | `tests/test_live_network_untouched.py` is unchanged and passes. Master's retained `freeze_fixed_plan` runs on the study's private copy (`_fixed_plan(st)`), and it neither solves nor mutates. |

---

## 3. Test integrity

**Relative to master.** The removals come from two sources:

- The branch's own gated edits made before the merge (campaign, abort, stress, http, sandbox), which auto-merged.
- The merger's adaptations, which I read case by case.

**Relative to the branch.** The removals are master's #54 changes (asset economics) plus the adaptations.

Of the 23 adapted backend cases and 3 adapted frontend cases, none weakens a safety or correctness property without a reason. Most are vocabulary or shape re-pins. Several add assertions, such as `report.certified`, and "decision 2" in the note. The exceptions:

| Case | Finding |
|---|---|
| `test_energy_hub_review.py:237-241` (`uuid="u-1"`) | **Masks B1.** |
| `EhReferenceDesignPanel.test.tsx:552-553` | Pins a 2-point knee, which is now contrary to the owner's ruling (B2). |
| `test_energy_hub_frontier_stage.py::test_ladder_needs_a_positive_cap` | Master asserted the stage returns no targets for a cap of `None` or `0`. It now asserts only that `AvailabilityTarget(ens_cap_permyriad=0)` is refused, and cites another test for `None`. The substitution is reasonable (the model forbids `0`), but it is not the same assertion and is not in §3 (N3). |
| `test_ladder_skips_below_minimum_points` | Parametrisation reduced from `[0,1,2,3]` to `[0,1]`, and the "budget" reason is no longer asserted. Consistent with the 2-point floor. Not listed (N3). |
| `test_certification_verdict_rule` | Master's `(None, 3.0) → not_established` case (no MC) was dropped. The `not_established` section path is covered elsewhere. Acceptable, but unlisted (N3). |
| `test_frontier_skipped_when_budget_cannot_afford_three_points` | Now tests budget 2 (one point). The name is stale. |
| `test_energy_hub_fmea_top.py::_assert_ranked(…, ranked=False)` | Class-B `rows` carry no `rank`, so the rank check is skipped for them. The ordering by criticality is still asserted. Acceptable. |

"No test was deleted" holds. One test was renamed, though, and the doc's count does not mention it.

---

## 4. Semantic decisions checked against the code

The decisions doc numbers them S1–S16. The brief's "decision 3", "decision 5" and "decision 7" are S3, S7 and S10.

| # | Topic | Result |
|---|---|---|
| S1 | Stage bodies | Correct: one implementation per stage, in the branch's `_STAGE_HANDLERS`. |
| S2 | Boundary | Correct: `_fixed_plan` always passes `boundary=` from `hub_boundary_copy`, and master's `whole_network` fallbacks are skipped when a boundary is given (`eh_stages.py:375-420`). |
| **S3** | **Excluded Links** | **Correct.** With a boundary, a Link in `excluded_import_links` becomes `excluded` (`eh_stages.py:480-490`). So do a missing source and a rate without a finite MTTR (`:515-528`). None ever reaches `firm +=`. Common-mode entries on excluded Links become `applied: False` with the reason. `link_grid` drops them, so no zonal area is built. Sampling disabled with a boundary gives `excluded`, not firm. The only path to a firm block is `boundary is None`, which no EH-study caller uses. A bad rate still raises (refused). |
| S4 / S5 | Hourly cap; zonal area for counted Links only | Correct. |
| S6 | Common-mode event on an excluded Link | Correct; the owner confirmed it (Q1). |
| **S7** | **Certification payload** | **Correct.** `mc_lole_h = lole / horizon_years = report.mc_lole_h` (per year, `eh_study.py:490-531`). `lole_ci`, `eue_mwh` and `eue_ci` stay **per horizon**, beside `horizon_years`. The panel divides the CI by `horizon_years` (`EhReferenceDesignPanel.tsx:275-279`), and `eh_review` labels it `lole_ci_per_horizon`, so no consumer mixes units. The EUE is shown unlabelled per horizon (N7). The verdict uses the CI against `target × horizon_years`. A master-shaped stored report still loads, because the verdict sits inside the payload dict, not a `Literal` field. |
| S8 / S9 | Refusals before the MC; MC option precedence | Correct. |
| **S10** | **`fmea_top` plus `class_a`** | **Correct in the backend.** Class-B `rows` decide the status. `class_a` is zero-solve, uses the same fleet, and ranks the import Link once against all Class-B rows (not just the top-N). Two gaps: (a) on a sweep **exception** or **abort**, the payload is `None`, so `class_a` is dropped, although the doc says `class_a` is "also attached when the Class-B ranking is `not_established`" (N2); (b) the panel numbers class-A ranks after Class B, which reads as one joint ranking (N1, see Q2). |
| S11 | Frontier | Correct for the 2-point **curve**. The knee rule is missing (B2). A 1-factor ladder `(1.0,)` passes the pack validator and yields a single point (N4). |
| S12–S16 | LCOH, `ens_met`, chat copy, readiness ladder, QA driver | Correct. The chat copy and CHATBOT.md describe `excluded`, the P11 verdict, the ladder and `class_a`. |

---

## 5. Checks re-run on `f44ae8f33` (plus the docs-only `2970552b3`)

| Check | Result |
|---|---|
| `merge_audit.py --self-test` | PASS (29 assertions) |
| `merge_audit.py` (both parents, inventories) | 10 findings, all traced (§2.1) |
| `ruff F821,F811` | no new findings versus the parents |
| Targeted EH, chat, Guided, security, packaging set (`test_energy_hub*`, `test_eh_*`, `test_chat_*`, `test_guided*`, `test_live_network_untouched`, `test_packaging*`, the worksheet lock and stress tests, the error-kind manifest, and all 17 test files master's security commits touched) | 117 files: 2185 passed, 2 skipped, 0 failed (exit 0) |
| `tsc --noEmit` | clean |
| `vitest run` | 234 files, 2610 tests passed |
| EH QA driver `tests/qa_eh_reference_design.py` | 110/110 PASS, exit 0 |
| P26 smoke `smoke-guided.mjs --phase P26` | PASS, exit 0 (38 screenshots). Verdicts unchanged: datacenter `fail`, h2_hub none, microgrid `inconclusive` |
| Full backend `-m "not slow"` | FULL_RESULT |
| Repro B1 (`test_repro_chat_stress_lock.py`) | 2 failed, 1 control passed |
| Repro B2 (`test_repro_knee_rule.py`) | 2 failed |

All scratch artefacts and logs are in `/tmp/claude-0/…/scratchpad/qamerge/`.

## 6. Packaging

**`pypsa-gui.spec`.** Adding `gridspine.drivers.year_study` is right. Master's `gridspine_service.py:56` imports `check_external` from it inside the guard, and master's spec never listed it. PyInstaller follows its transitive imports.

**`check_bundle.ROOTED`.** It is complete for `__file__`-relative data reads:

- Master added **no** new data files and no new `__file__` reads since the base. Its gridspine data is covered by `EXPECTED` (`case39_units.yaml`, `case39.json`).
- The branch's two reads are both listed: `data/eh_class_c/…` and `data/guides/…`.

One branch-side, pre-existing gap. `routers/projects.py:1206/1234` loads `project_templates/eh_templates.py` by file path from the bundle root, and the `eh_*` sidecars are read from there too. `EXPECTED` only checks the basename `project_templates` anywhere in the bundle. Adding `"project_templates/eh_templates.py"` to `ROOTED` would anchor it. This is not merge-induced (N8).

---

## 7. Non-blocking notes

- **N1 (Q2 follow-through).** `fmeaTopModes` (`EhReferenceDesignPanel.tsx:464-476`) renumbers class-A rows as `b.length + i + 1`. The table and the CSV (`:530`, `:1861-1864`) then show one continuous rank column across two engines. A class-A mode with the higher €/yr can appear as rank 6 beneath a Class-B rank 1. That contradicts "two lists, kept separate". Rank within each class (for example `B1…`, `A1…`, or a rank that restarts per class with a class sub-header), and state in the header that the two criticalities come from different engines.
- **N2.** `_stage_fmea_top` sets the payload to `None` on a sweep exception (`eh_study.py` "sweep failed" branch) and on abort, so the zero-solve `class_a` screening is lost. Attach `_class_a_block(st, [], top_n)` on the failure path, or correct the decisions doc's claim.
- **N3.** Decisions doc §3 should list: the renamed test (`…needs_three_factors` → `…needs_two_points`); the re-targeted `test_ladder_needs_a_positive_cap`; the reduced parametrisations (`[0,1,2,3]` → `[0,1]`); and the dropped `(None, 3.0) → not_established` verdict case. Rename `test_frontier_skipped_when_budget_cannot_afford_three_points`.
- **N4.** `ArchetypePack._frontier_ladder_is_usable` accepts `(1.0,)`. The stage then sweeps 1 point, charges 1 solve, and reports "fewer than two frontier points solved", which is misleading. Either require at least one factor ≠ 1 in the validator, or refuse before sweeping with "the ladder yields one point".
- **N5 (pre-existing on master, not merge-induced).** `POST /api/results/eh_study/abort` existed at the base and on master, but it is missing from `_FOREIGN_LOCK_GATE_EXEMPT_EXACT` (`main.py:211-229`). A foreign lock taken during an EH study therefore traps it, which is the exact harm master's own comment describes. Master's "exactly ten write routes" comment (`main.py:130`) is wrong: there are twelve. Raise this on master.
- **N6 (pre-existing on master).** `chat_tools.update_solver_config` calls the handler without `db` or `actor` (`chat_tools.py:1281-1285`). It only bites when setting `extra_functionality_code`, and then fails closed. Route it through `_route` alongside B1.
- **N7.** The certification detail block shows EUE per horizon without a unit basis, while LOLE is shown per year beside it. Label it or convert it.
- **N8.** `ROOTED` lacks `project_templates/eh_templates.py` (§6).
- **N9.** Stale docstrings: `qa_eh_reference_design.py` says "a frontier of at least three points"; two historical findings docs name the removed `eh_stages` runners.

---

## 8. The three owner questions: recommendations

The owner answered these on 2026-09-29, after the brief was written. My independent recommendations agree with all three, with one follow-through on each of Q2 and Q3.

**Q1. Should a common-mode event alone count as "outages modelled"?** **No: exclude and disclose (agrees with the owner).**

A common-mode event models the *correlated* loss of the Link and its grid area. It says nothing about the Link's own independent forced outages. Counting such a Link as firm at its planning cap, plus a rare event, would certify import capacity whose ordinary failure rate is unknown. That is the copper-plate optimism that decision 6 and the P11 gate exist to refuse.

Master itself flagged the muddle. Its review note at `eh_stages.py:202-204` says "a firm block under a sampled common-mode event is not `planning_limit_only` any more", so master needed a special firmness label for the hybrid.

The merged disclosure (`applied: false`, "import Link not counted in the MC fleet — …") tells the user exactly what to add: `outage_rate_value` and `mttr_hours`.

**Q2. Should class-A and class-B FMEA be ranked together?** **Separately (agrees with the owner), and the panel should show it (N1).**

The two criticalities come from different engines with different fidelity:

- Class A is a zero-solve COPT screening of unit capacity deficits. It carries `copt_fidelity_note`, and its import is treated per `copt_import_model`.
- Class B is an LP re-dispatch ΔEUE × VOLL on the frozen plan.

Only Class B carries the Link-primary SCLOPF caveat that decision 14 makes the section's evidence.

The two use different bases: class A's severity comes from the COPT capacity-deficit distribution of the fixed fleet, while class B's comes from an LP re-dispatch. Neither is calibrated against the other. A joint top-N would let the cheaper, coarser engine displace LP-verified Link modes from a 5-row list. The P12 amendment already fixed the list at "top-5 Class-B Link modes".

Keep two lists and rank within each. Today the panel's single rank column implies a joint ranking.

**Q3. Frontier minimum: 2 points or 3?** **2 for the curve and 3 for the knee (agrees with the owner). Not yet enforced (B2).**

Two points give one segment, which is a valid, if coarse, cost–availability trade-off. That is why P12 gated 2 and budget-limited studies need it: `frontier_point_count` gives 2 at budget ≤ 7.

A knee, though, is where the *marginal* cost crosses VOLL × marginal ENS avoided. With one segment, `knee_index` can only answer "is the single step worth buying", and the answer is reported as a point on the curve. The review then turns it into an actionable "re-plan at the knee target".

Three points are the minimum for a crossing to lie *between* two alternatives. Below three the knee must be `not_established` with the reason, as B2 specifies.
