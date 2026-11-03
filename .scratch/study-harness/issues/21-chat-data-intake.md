<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 — Chat data intake: attachments and pasted values feed setup, the run creates the project, and asks only for what is missing

Status: needs-triage
Type: task
Blocked by: 03, 04 (the L1 data-gap rule and its `needs_user` exit); 08 renders the card rows
Gated: yes (GK B)
Model tier: opus
Owner decisions: CHAT-DATA-SPEC-1, SH-ENVELOPE-1, SH-PILOT-1, SH-FOLD-1, AI-567b, COP-1
Design: [design.md](../design.md) §3 (stages 0–2), §6.1, §6.8 (new), §7, §9.3 "eh_site"

## Why

The owner's goal: a run takes a project end to end from data handed over in the
chat, and asks the user only when data is missing. Today nothing turns chat
data into a run's inputs:

- Chat attachments reach the model only
  (`harness/loop.py:889-960` `_build_user_content`; `routers/chat.py:82-94`
  `attachment_file_ids`). No stage reads them.
- `services/run_profiles.py:69-70`: "creating the project from the template
  stays the user's step for now". A run starts on the open project
  (`services/assistant_tasks.py:118-125` `_authority`, `:483-509`
  `_start_run`), and tasks refuse project-rebinding tools, which include
  `create_project_from_template` (`assistant_tasks.py:417`,
  `harness/loop.py:167-174`).
- Asking for missing data exists only as issue 04's L1 exit (`needs_user`).

## What exists and is reused (build none of it again)

| Need | Reused as is |
|---|---|
| Read an XLSX/CSV attachment with cell lineage (upload id, sha256, sheet, A1) and bounded, inert parsing | `services/equipment_table.py:214` `read_table` |
| Read a PDF attachment's text layer in a killed child process | `services/document_values.py:109` `read_pages` |
| Check that a cited quote is on the page and read exactly one number from it (ranges, ±, ambiguous separators refused) | `document_values.py:249` `locate`, `:275` `_value`, `:182` `numbers` |
| Offer citable lines for a wanted field and unit | `document_values.py:339` `find` |
| Accept a unit only from a field schema (stored unit, or display unit by `display_scale`); refuse missing or other units | `equipment_table.py:299` `_accepted_units`, `:304` `_column_verdict`; `document_values.py:326` `_unit_re` |
| Money: currency and money year stated, never assumed | `equipment_table.py:442` `money_needs`; the `money_year` confirmation |
| Write table/PDF values onto components after a card, digest-bound to the source | `apply_equipment_mapping`, `apply_document_values` (`harness/catalogue.py:4245`, `:4312`; `harness/edit_card.py:892-941`) |
| Write a load time series from a table | `apply_demand_from_excel` (`services/chat_tools.py:6717`) |
| Check the eh_site creation params, with bounds and the field that is wrong | `project_templates/eh_site.py:251` `site_params` (raises `SiteParamsError`); `:208` `form_schema` bounds |
| Tell entered params from preset defaults in the report | `eh_site.py:768` `entered_fields` |
| Create a project from the template | the route handler `routers/projects.py:1722` `create_from_template` (as `chat_tools.py:5361` calls it) |
| Content-addressed uploads, so a file keeps its id in another project | `services/upload_service.py:305` `_file_id_for` (id = sha256 prefix), `:389` `add_upload` |
| One click approves a listed set, recorded as a card decision | `services/run_envelope.py:268` `propose`, the grant at `:366-380` (`approvals.decided(by="user")`) |
| Waiting for the user, and resuming by re-checking the gate from native facts | `harness/runner.py:610-628` `_wait_for_user` / `_go_on`; `assistant_tasks.py:671-716` `resume_task` (`:694-699` failed stage → gate re-checked) |
| Inputs changed outside the run go back to the input gate | `assistant_tasks.py:545-571` `_inputs_moved`, `_back_to_input_gate` |

New code is only: the intake record and its checks, the param field table
(units), the create-at-grant hand-over, the data-request builder, and the
answer-binding rule in "Mid-run data". All of it is harness code; the model
proposes citations, never values (AI-567b, as `document_values.py:5-6`).

## What to build

### 1. Intake: chat data → setup inputs (stage 0)

The agent, in the intake turn, proposes **citations**, not values. The harness
checks each one and records it in `run.intake` on the task. Three source
kinds, one rule set:

| Source | Citation the agent proposes | Checked by | Lineage kept |
|---|---|---|---|
| File: XLSX/CSV attachment | `{param, upload_id, sheet?, cell}` or a column of rows | `read_table`; unit from the header cell through `_column_verdict` | upload id, sha256, sheet, A1, raw cell |
| File: PDF attachment | `{param, upload_id, page, quote, unit, value_text?}` | `read_pages` + `locate` + `_value` | upload id, sha256, page, quote (≤300 chars) |
| Pasted table or value stated in text | `{param, turn_id, quote, unit, value_text?}` | the stored user message of that turn (`harness/history.py:148` `read_all_turns`) as a one-page document through the same `locate` + `_value` | turn id, quote |

Images are not a source (no OCR, as `document_values.py:16`). A value the
model "saw" in an image or a PDF without a text layer is a gap, not a value.

**Param field table.** `run_profiles.EH_SITE["params"]` gains a `fields` map
(data) giving each creation param a field-schema-shaped entry, so the
unit checks above apply unchanged:

| eh_site param (`eh_site.py:251-358`) | Unit (stored) | Accepted from | Notes |
|---|---|---|---|
| `site_type` | enum `SITE_TYPES` | text only | the agent maps words to an id; the card shows the quote |
| `stages[i].year`, `stages[i].peak_mw` | year; MW (kW by `display_scale` 0.001) | table, PDF, text | a table of years and peaks fills the list in row order |
| `connection_mw` | MW (kW) | table, PDF, text | |
| `energization[i].mw` | MW | table, PDF, text | one per stage year, or absent (the template defaults it) |
| `critical_share` | fraction (`%` by 0.01) | table, PDF, text | a bare number with no unit is `unit_missing` |
| `load_profile` | enum `LOAD_PROFILES` | text only | |
| `existing.gensets.count` / `.unit_mw` / `.fuel` | count; MW; enum | table, PDF, text | |
| `existing.pv_mw`, `existing.battery.mw` / `.mwh` | MW; MWh | table, PDF, text | battery needs both or neither (`site_params`) |
| `existing.ups.mw` / `.minutes` | MW; min | table, PDF, text | |
| `candidates.<k>.enabled` / `.max_mw` | bool; MW | text; table, PDF, text | |
| `ride_through` | bool | text only | |
| `fault_level_mva` | MVA | table, PDF, text | |
| `horizon_end` | year | table, PDF, text | |

Stage-2 inputs from chat (after the project exists, §2): a load time series
(`apply_demand_from_excel`), component ratings or costs from a table or
datasheet (`apply_equipment_mapping`, `apply_document_values`), a tariff file
(`attach_tariff`). Intake records them as **planned writes** with their
source; they run in stage 2 (§2 below).

**Every intake row** is `{param|write, value, unit, source, status}`, where
`status` is `proposed`, `confirmed`, `struck` or `conflict`. Two sources for
the same param with different values are `conflict`, and neither is used: the
conflict goes into the data request (§3). After all rows, the harness runs
`site_params` over the confirmed values; a `SiteParamsError` makes its field a
gap with the error's own text.

**Confirmation, never silent trust.** The plan-review card (issue 08) gets an
"Inputs from the chat" section, one row per intake row: param, value with unit,
the source (file name + page and quote, or sheet!A1, or "you wrote: '…'"), and
a mark on every value read from a document. Params not given are listed as
"preset default" with their value (`entered_fields` tells them apart in the
report). The user can strike or correct a row before granting. Granting is the
one click of SH-ENVELOPE-1 and confirms exactly the rows shown, bound by a
digest over the rows and each source's sha256 (as the equipment card's digest,
`edit_card.py:941`). There is no path from a document to a param or a write
that skips this card. A step-by-step run shows the same rows on the same card
and then cards each write as today (COP-1).

### 2. Project creation inside the run (stage 1), envelope-listed

Recommended shape (open choice 1): **create at grant, hand over to the new
project.**

1. Intake runs on the open project (the run's host), as today: the task file,
   brief, `run.intake` and the proposed envelope live there.
2. The proposed envelope gains a `setup` entry, the profile's §9.2 action made
   concrete: `{"tool": "create_project_from_template", "template": "eh_site",
   "name": "<name>", "params_digest": "<digest of the confirmed rows>"}`. The
   card shows it as its first row: "Create project <name> from the generic site
   template with the inputs above". §7 already lists "the run's own setup" as
   grantable; nothing else about project creation becomes grantable.
3. On grant, the harness (no model): creates the project through the route
   handler as the owner, without activating it in the foreground session;
   copies the cited uploads into it (same file ids, same sha256); starts the
   run's task on the new project with `run.origin = {project_id, task_id}` of
   the host task; and grants the envelope there, with the fingerprint taken
   after creation as the first link of the chain. The host task ends
   `completed` with reason `handed_over` and a link to the new run.
4. Stage 1's gate then checks `project.exists`, `project.template ==
   eh_site` and `params.accepted`: the created project's params (template
   meta) equal the confirmed rows.

So the task and envelope bind one project uuid from birth: "tasks never
rebind" (`run_profiles.py:69`) and §7's project binding hold unchanged.

Failure handling: if creation fails, nothing is started and the card says
why (the `SiteParamsError` text); if the project was created but the task did
not start, the project stays (deleting is destructive and never automatic) and
the host task names it with a "start the run there" action. A retried grant
with the same request id finds the created project by `run.origin` and does not
create a second one.

When the user starts from a project that already is an eh_site project and
asks to study it, setup binds the open project as today (§9.2 "or binds the
open project"); intake rows then become stage-2 planned writes only.

Stage 2, before the profile's fetch actions, runs the planned writes in
intake order: each through its own apply tool, admitted by the envelope only
when the plan the tool computes on the created network has exactly the rows
and the source digest the grant listed. Any difference (a renamed component, a
row the plan leaves out) gets a card. Each such tool joins
`run_profiles.BOUND_CONTEXT_TOOLS` only with its §6.6 sweep test.

### 3. Ask only for what is missing: one consolidated data request

- `missing = profile.params.required + inputs.required[*] − confirmed intake
  rows`, plus every `conflict` row and every row `site_params` refused.
  Params in `params.ask` are not asked: they take the preset default and are
  shown as such.
- **At intake** the missing set is one message from the harness (not composed
  by the model): one line per item with its unit and the accepted sources.
  The plan-review card cannot be granted while a `required` item is missing.
- **During the run** the request is issue 04's L1 data-gap rule, not a new
  stop: a gap L1 cannot fill within its tunables and the envelope ends L1 as
  04 defines (`needs_user`). This issue only shapes that exit's notice: the
  notice lists every gap at once (the failed `block` rules that name a data
  gap, minus what intake found), in the same one-line-per-item form, with
  `run.waiting = {"kind": "data", "ref": <request id>, "items": [...]}`
  through `_wait_for_user`.
- The same item is never asked twice in one run: the request id lists the
  items, and a later request names only items that were not answered.

### 4. A later chat message carrying new data, mid-run

Recommended (open choice 2):

- **The run is waiting on a data request.** The user's message with data goes
  through ordinary chat and its cards (COP-1): the harness does not write it
  under the envelope. A carded write by the run's owner, on the run's project,
  whose target is an item of the open request, is recorded as an answer row
  (`answers: <request id>`) and appended to the envelope's freshness chain, so
  it is not foreign drift. When every item is answered, or on the user's
  Resume, the run goes on through `resume_task`: the failed gate is checked
  again from native facts (`assistant_tasks.py:694-699`). A write that answers
  no open item is ordinary drift (next bullet).
- **The run is running, not waiting.** Any write is drift as today: the
  envelope pauses the run (§7 use rule 7) and, past the input gate, the run
  goes back to stage 3 (`_back_to_input_gate`). The pause notice names the
  change. Nothing is folded in by the harness.
- **New data that contradicts a creation param** (for example a new
  connection size) is not applied to the template params after creation; the
  user changes the project as any edit, which is `brief_stale` and needs
  re-review (§3 freshness).

### 5. Out of scope, and the other profiles

Out of scope:
- OCR, images as a data source, Word documents, and reading text in a file as
  instructions (it is data only, as `document_values.py:36-37`).
- Runs started with no project open: uploads are per project
  (`routers/uploads.py:184`) and `_authority` needs one. The host can be any
  project the user can open.
- Research to fill gaps (issue 09 owns it; a researched value is never an
  intake row).
- A new parser, unit system or money rule: all come from the modules above.
- Changing issue 04's L1 rule, bounds or stop; issues 22+ (the #970 fold).

Other profiles (issues 10–17): the mechanism is profile data. Each profile's
`params.fields` gives its creation params' units and accepted sources, and its
`inputs.required` gives the stage-2 items. Profile issues add their table; no
intake code changes. Known differences to record there: eh_datacenter's
grid-code profile is never an intake row (published by the user's own card,
D3-KEEP, issue 10); hydrogen and ammonia offtake and process demand are
`NEVER_TUNABLE` (`run_profiles.py:34-36`), so they are intake rows the user
confirms but never something L1 may fill; grid-only profiles have no creation
params beyond the network. Issue 17 generates workflow files from profiles and
picks up the "Inputs from the chat" step.

## Acceptance criteria

- [ ] Intake from an XLSX attachment with a stages table (`year`, `peak (kW)`)
      fills `stages` in MW with sheet!A1 lineage; a column with no unit is
      `unit_missing` and becomes a data-request item (test).
- [ ] Intake from a PDF attachment with "Grid connection: 12 MW" on page 2
      fills `connection_mw` = 12 with page and quote; a quote not on the page
      (`quote_not_found`) or a range ("10-12 MW", `ambiguous_value`) gives no
      value and a request item (tests).
- [ ] Intake from pasted text "critical load is 40 %" fills `critical_share`
      = 0.4 with the turn id and quote; "critical load is 40" is
      `unit_missing` (tests).
- [ ] Two sources disagreeing on `connection_mw` give a `conflict` row, no
      value, and one request item naming both sources (test).
- [ ] The plan-review card view lists every intake row with value, unit,
      source and the document mark, and the preset defaults as such; the grant
      digest changes when a row changes, and a stale digest is refused (test).
- [ ] No param or planned write is applied without the grant (envelope) or a
      card (step-by-step): a test drives intake with a document and asserts no
      project, no write and no task on the new project before the click.
- [ ] Grant creates exactly one eh_site project with the confirmed params,
      copies the cited uploads with unchanged sha256, starts the run there with
      `run.origin`, and ends the host task `handed_over`; a repeated grant
      creates no second project (test).
- [ ] The envelope on the new project binds its uuid and its first chain link
      is the fingerprint after creation; §7's refusal matrix (issue 03) still
      passes on it.
- [ ] A planned write whose plan on the created network differs from the
      granted rows gets a card, not an envelope admission (test).
- [ ] With `connection_mw` missing, the harness's one intake message lists it
      with its unit and the grant is refused until it is given (test).
- [ ] L1 exhaustion with two data gaps produces one `needs_user` notice
      listing both, `run.waiting.kind == "data"` (test, on 04's fixture).
- [ ] Answering both gaps by carded writes in chat records two answer rows,
      does not pause the run as drift, and the run continues at stage 3; a
      write that answers no item pauses it as drift (tests).
- [ ] No item is asked twice in one run (test).
- [ ] The model never supplies a value: an intake citation carrying a `value`
      field is refused (test).

## Likely files

`pypsa-gui/backend/services/run_intake.py` (new: intake rows, checks,
missing set, request text), `pypsa-gui/backend/services/run_profiles.py`
(`params.fields` for eh_site), `pypsa-gui/backend/services/run_envelope.py`
(the `setup` entry and grant hand-over), `pypsa-gui/backend/services/assistant_tasks.py`
(`run.intake`, `run.origin`, answer rows), `pypsa-gui/backend/harness/runner.py`
(the data notice through `_wait_for_user`),
`pypsa-gui/backend/harness/workflows/profiles/eh_site/intake.md` (new),
`pypsa-gui/backend/tests/test_run_intake.py` (new). Issue 08 renders the card
section.

## Open choices for the owner

1. **Where the run's project comes from.** (a, recommended) Create at grant
   and hand over to the new project: no rebinding, §7 unchanged. (b) Bind once
   at setup: the task moves from the host to the new project, which changes
   "tasks never rebind" and the envelope's project binding. (c) Creation stays
   the user's Guided-form step; intake only fills the open project.
2. **Mid-run data.** (a, recommended) Carded writes answering an open request
   resume the run; other writes are drift as today. (b) The harness folds new
   chat data in itself at the next stage boundary, which widens the envelope
   to unlisted writes. (c) Every new data message pauses the run until the
   user resumes.
3. **Numbers stated only in chat text.** (a, recommended) Accepted with the
   quote and a stated unit, shown on the card as "you wrote". (b) Numbers need
   a file; text gives only enums and yes/no.

## Comments
