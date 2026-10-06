# Campus live probe, 2026-10-06: grid-code extraction and copilot tools

ADR-0002 says a green, mocked suite does not verify a chat or LLM change.
This is the live probe for the campus study's grid-code extraction (plan
C10) and the campus copilot tools (C6, C9, C10), run against the vendor API
from `feat/gridspine-campus-invest-ui` at `5824341`.

**Result:** both probes ran on the live API. The extraction's call contract
works: the forced `tool_choice` was accepted and the draft validated. The
probes found **two defects**, described under "Bugs found". No production
code was changed on this branch.

## What was run

The probes are in `pypsa-gui/backend/tests/test_campus_grid_code_live_probe.py`
and are opt-in. Each skips unless `PYPSA_GUI_TEST_LIVE_ANTHROPIC=1` and
`ANTHROPIC_API_KEY` are both set. The extraction probe also needs
`PYPSA_GUI_TEST_LIVE_GRID_CODE_PDF`.

```bash
cd pypsa-gui/backend
env -u ANTHROPIC_BASE_URL ANTHROPIC_API_KEY=… PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 \
  PYPSA_GUI_TEST_LIVE_GRID_CODE_PDF=/path/to/dcc.pdf \
  PYPSA_GUI_TEST_LIVE_PROBE_OUT=/some/dir \
  python -m pytest tests/test_campus_grid_code_live_probe.py -v
```

| Probe | Live API calls | Outcome |
|---|---|---|
| `test_live_probe_grid_code_extraction`, run 1 | 1 | **failed** after extracting: the campus run refused the published draft (bug 1) |
| `test_live_probe_grid_code_extraction`, run 2 | 1 | passed. The probe now records bug 1, applies a reviewer's edit and carries on |
| `test_live_probe_campus_copilot_tools` | 1 chat turn (5 tool calls) | passed (exit 0) |

`pytest.ini` sets `addopts = -q`, so `-q` on the command line becomes `-qq`
and pytest prints no "1 passed" line. Pass and fail were read from the exit
code and from whether a `FAILED` summary line appeared.

When the probes are skipped (no env flags), both report `ss` with the
"LIVE PROBE NOT RUN … UNPROBED" reason.

### How the production path was driven

* **Extraction.** `campus_grid_code_service.upload_document`, then
  `extract(hub, doc_id, "dcc_live")`. The client is built by the real
  `chat_service._build_anthropic_client`. A thin wrapper is installed around
  the builder's result. It records each `messages.create` call's `model`,
  `tool_choice`, `stop_reason`, response model and usage, and the class of a
  failed call. It then forwards the call unchanged. It never records the PDF
  bytes. The request is not altered.
* **Publish and run.** The draft was published with `allow_unconfirmed` and
  the study run with `campus_electrical_service.run(hub, {"k": 1,
  "profile": "dcc_live"})`. Every unconfirmed limit was then confirmed with
  `confirm`, the draft published without `allow_unconfirmed`, and the study
  run again. The hub is `tests/test_campus_electrical_service.py`'s
  `hub_network(priced=True)`: a 110 kV grid bus as the PCC and a 20 kV MV
  bus. The campus was drafted with `ce.draft`.
* **Chat turn.** Profile store → `_provider_for_profile` → `run_turn`, with
  the built-in `anthropic-sonnet` profile and no injected client or
  provider, as `test_live_probe_anthropic_wire` does. The hub was solved,
  drafted and run once (`invest` on) before the turn. The DCC PDF was
  uploaded, and a hand-made draft was saved with one `extracted` limit
  (`q_range_demand`). No API was used to make it. The draft's id is
  `gc_<sha[:12]>`, the extraction's default id, so a stray
  `campus_extract_grid_code` call would get a 409 before any API call.

## Environment

* `ANTHROPIC_BASE_URL` was set in the container (for Claude Code). **It was
  removed for each probe process** (`env -u ANTHROPIC_BASE_URL`), so the SDK
  used its default, `https://api.anthropic.com`. `HTTPS_PROXY` was left as
  is (the container's agent proxy). TLS verification was not touched.
* The key was passed only on the probe process's command line as
  `ANTHROPIC_API_KEY`. It does not appear in the probe records, the logs or
  this report.
* Python: `/home/user/pypsa-eur/.pixi/envs/test` does **not** exist in this
  container, and `pixi` is not installed. The probes ran on the container's
  preconfigured `/root/.venv-pypsa-gui` (Python 3.12, `anthropic` 1.11.0,
  `pypdf` 6.19.0). `tests/test_campus_grid_code_service.py` passes on it
  (106 tests).
* Model: `claude-sonnet-5`, which is `llm_config.DEFAULT_MODEL` and the
  built-in profile's model. It comes from the review metadata, and the
  response's own `model` field agrees.

## The document

The probe used the real **Commission Regulation (EU) 2016/1388 (DCC)**,
English, from EUR-Lex
(`https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R1388`).
The file is 639,787 bytes and **45 pages**, so it is under the service's
100-page cap. **The whole document was sent and no page subset was needed.**
It is not committed. Its sha256 is
`655cf78adcf0757fca9bd660b7bda0b0b577c26c7b9c2b75a87b3514b2961147`.

The relevant pages, counted as PDF file pages from 1 by pypdf:

* **Article 15(1)(a)**, "48 percent": file page **13** (printed
  `L 223/22`);
* **Annex II**, both voltage tables: file page **44**.

## Probe 1: extraction results

### The call

```
model        : claude-sonnet-5   (request and response)
tool_choice  : {"type": "tool", "name": "record_grid_code"}  — ACCEPTED, no 400, no fallback to auto
stop_reason  : tool_use
calls        : 1 per extraction
usage run 1  : input 118936 | output 581 | cache_create 0 | cache_read 0
usage run 2  : input 118936 | output 564 | cache_create 0 | cache_read 0
```

A whole 45-page code costs about 119k input tokens per extraction, with no
caching.

### The draft (run 2; run 1 had the same values, pages and quotes, with slightly different clause wording)

| Limit | Value | Clause (model) | Page | Quote | `quote_found` |
|---|---|---|---|---|---|
| `voltage_bands[0]` | 110–300 kV (excl.): 0.90–1.118 pu | Annex II, Continental Europe synchronous area (110 kV to <300 kV) | 44 | `0,90 pu-1,118 pu Unlimited` | true (p. 44) |
| `voltage_bands[1]` | 300–400 kV (`kv_max_inclusive: true`): 0.90–1.05 pu | Annex II, Continental Europe synchronous area (300 kV to 400 kV included) | 44 | `0,90 pu-1,05 pu Unlimited` | true (p. 44) |
| `q_range_demand` | 0.48 | Article 15(1)(a), transmission-connected demand facilities | **21** | `the actual reactive power range specified by the relevant TSO for importing and exporting reactive power shall not be wider than 48 percent of the larger of the maximum import capacity or maximum export capacity (0,9 power factor import or export of active power)` | **false** |
| `rvc_limit_pct` | 3.0 (`assumed`) | `not stated in the document; generic placeholder until a real code is supplied: 3 % rapid voltage change` | – | – | – |

* `title`: `EU Network Code on Demand Connection (NC DCC) 2016/1388`
* `document.title`: `COMMISSION REGULATION (EU) 2016/1388 of 17 August 2016 establishing a Network Code on Demand Connection`
* `filled_from_template`: `["rvc_limit_pct"]`
* `campus_voltage`: not extracted. The DCC sets none, which is correct.
* Every extracted limit was tagged `source: extracted`.
* `unconfirmed`: `["voltage_bands[0]", "voltage_bands[1]", "q_range_demand"]`
* **The draft validated**: `extract` returned it and saved it.
* The probe's own check: the Q quote is verbatim, and normalised it is found
  on file page **13**. It is not on page 21, nor on 20 or 22. So
  `quote_found: false` is the service correctly flagging a **wrong page**,
  not a paraphrase. Both runs gave page 21 (bug 2).

### Against the shipped `eu_rfg_dcc_ce`

| Limit | Shipped | Extracted | |
|---|---|---|---|
| band 0–110 kV | 0.90–1.10, `assumed` ("not set by RfG/DCC below 110 kV") | — | **missing**: the DCC does not state one, so leaving it out is faithful. It causes bug 1 |
| band 110–300 kV | 0.90–1.118, `code` | 0.90–1.118 | **match** |
| band 300–400 kV incl. | 0.90–1.05, `kv_max_inclusive`, `code` | 0.90–1.05, `kv_max_inclusive: true` | **match** |
| `q_range_demand` | 0.48, DCC Art. 15(1)(a), `code` | 0.48, Art. 15(1)(a) | **match** in value and clause, but the page is wrong |
| `rvc_limit_pct` | 3.0, `assumed` (IEC TR 61000-3-7 note) | 3.0, `assumed` from `generic_assumed` | same value. The clause differs because it comes from the generic template |
| `campus_voltage` | — | — | both absent |

No extracted value differs from the shipped profile. The shipped profile's
`assumed` band below 110 kV is the only missing limit, and it matters (bug 1).

### Publish and run

| Step | Result |
|---|---|
| Publish as extracted (`allow_unconfirmed`) and run | **422** `grid-code profile 'dcc_live' sets no voltage band for 20 kV` (`HTTPException`, from `ContractError`; bug 1) |
| Reviewer edit via `save_draft`: add the shipped profile's `assumed` 0–110 kV band; publish `allow_unconfirmed`; run | ran |
| Confirm all three extracted limits; publish without `allow_unconfirmed` | `unconfirmed: []` |
| Run again | ran |

The report rows, as expected:

| Check | Unconfirmed publish | Confirmed publish | Clause |
|---|---|---|---|
| requirement / `pcc_reactive` | `extracted` | `code` | Article 15(1)(a), transmission-connected demand facilities (Q limit 21.82 Mvar = 0.48 × P_ref 45.45 MW) |
| `pcc_voltage` (110 kV PCC) | `extracted` | `code` | Annex II, Continental Europe (110 kV to <300 kV) |
| `campus_voltage` (20 kV) | `assumed` | `assumed` | not set by RfG/DCC below 110 kV; +/-10 % used (the band added by hand) |
| `transformer_loading`, `switchgear` | `assumed` | `assumed` | equipment ratings |

An unconfirmed limit stays `extracted` on every report row, and a confirmed
one becomes `code`, so nothing unconfirmed is laundered into `code`.

## Probe 2: copilot tools, one live chat turn

The prompt, verbatim except for the project's name:

> For my hub project "Live Copilot Hub c898a3": summarise its campus electrical study results, list the grid codes available to it, and tell me roughly what the last run bought. Then publish the draft grid code for me so I can run the study against it.

The model called these tools, in order. Every one returned a `tool_result`
with no `tool_error`:

| # | Tool | Arguments | Tier |
|---|---|---|---|
| 1 | `list_projects` | `{}` | read |
| 2 | `campus_get_study` | `{"project_id": "542fdf5f-…"}` (the hub's UUID, found by step 1) | read |
| 3 | `campus_list_grid_codes` | same | read |
| 4 | `campus_get_investment` | same | read |
| 5 | `ui_open_panel` | `{"panel_id": "hubDesign"}` | read |

* **The tool results reached the model.** Its answer quotes values that
  exist only in the results:
  * the draft id `gc_655cf78adcf0` and its title;
  * the 45-page `dcc.pdf`;
  * exactly one unconfirmed limit, `q_range_demand`;
  * "Published: none";
  * the purchased items `TR_110_20_63`, `SG_110_31p5` and `SG_20_25p0`,
    with capex and annualised costs.
* **The model respected the descriptions.**
  * It **did not attempt to publish or confirm.** No such tool was
    requested, and none exists. It said: "I can't do this for you: there's
    no tool that publishes or confirms a draft on your behalf, by design.
    Confirming is a limit-by-limit human check against the quoted page in
    the source PDF, done in the Campus electrical panel."
  * It flagged the unconfirmed limit before any reliance on it.
  * It called the costs "placeholders tagged `assumed` … order-of-magnitude".
  * It did not call `campus_extract_grid_code` or any write-tier tool, and
    it said "no network changes were made".
* One claim in its answer was not checked by the probe: "N-1 max S came back
  0.0, i.e. the N-1 screen found no feasible … path". The hub has a single
  transformer, so a zero under N-1 may be expected. The campus session
  should confirm that `campus_get_study`'s N-1 field means what the model
  took it to mean.
* Frames: `session_init`, `thinking`×2, then `tool_preparing` /
  `tool_request` / `tool_running` / `tool_result` ×5, `token`×245,
  `ui_event`, `turn_done`. The turn ended on `turn_done` and there were no
  `error` frames.
* Usage, from the `turn_done` frame: `input_tokens 13009 | output_tokens
  2854 | cache_read 134496 | cache_create 44832 | reported: True`.

## Bugs found (not fixed here)

### Bug 1: an extracted profile that is faithful to the DCC cannot be used by the campus study

**Failing input:**
* a profile whose `voltage_bands` start at 110 kV: exactly what
  `extract` drafts from the real DCC, since the DCC sets no band below
  110 kV;
* no `campus_voltage`, which the DCC does not set either;
* a campus with an MV bus: `hub_network()`'s 20 kV `mv` bus, which every
  realistic campus has.

**Error:** `campus_electrical_service.run` → 422
`grid-code profile 'dcc_live' sets no voltage band for 20 kV`.
The traceback:

```
campus_electrical_service.py:506  run -> cs.size_campus
gridspine/drivers/campus_study.py:313  size_campus -> campus_compliance
gridspine/static/campus_compliance.py:104  v = _voltage(bus[sel], profile, bus_kv, own)
gridspine/static/campus_compliance.py:67   band = fixed_band or band_for(profile, bus_kv[r.bus])
gridspine/templates/grid_codes.py:266      band_for -> ContractError
```

When `campus_voltage` is absent (`own` is None), the campus-voltage check
looks up each interior bus's band by its nominal voltage. Nothing below
110 kV exists in the profile, so the lookup fails. The run fails the same
way with `invest: false`.

**Why the gap is not caught earlier:**
* The draft passes `validate_profile`.
* `publish` accepts it, with or without unconfirmed limits.
* The error appears only when the study is run.
* `_draft_from` fills `voltage_bands` from the template only when the model
  returns *no* bands at all. It never fills a voltage range that the
  extracted bands leave uncovered, and it never fills `campus_voltage`.
* The shipped `eu_rfg_dcc_ce` avoids the gap only because it carries a
  hand-written `assumed` band from 0 to 110 kV.

**Workaround the probe uses:** add the shipped profile's `assumed` 0–110 kV
band by `save_draft` before publishing.

**Possible fixes, for the campus session to choose from:**
* fill uncovered voltage ranges, or `campus_voltage`, from the template as
  `assumed` and list them in `filled_from_template`;
* or check coverage at publish or run time, with a message that says what
  to add;
* or let `campus_compliance` fall back to an `assumed` design band for
  interior buses.

### Bug 2: wrong page for the Article 15 quote (extraction quality; the guard worked)

In both runs the model gave `page: 21` for the Article 15(1)(a) quote. The
quote is verbatim, and it is on file page **13** (printed `L 223/22`).
Page 21 is neither the file page nor the printed page. The system prompt's
"counting the file's first page as 1 (not the page number printed on the
page)" did not prevent it.

The service handled it as designed: `quote_found: false`,
`found_on_page: null`, flagged for the reviewer.

Two consequences for the campus session:

* **A misleading flag.** A reviewer sees "quote not found", while the quote
  is exact and only the page is wrong. A lookup across all pages, run when
  the quote is not found near the stated page, could report the real page,
  `found_on_page: 13`, and turn this into an actionable correction.
* **A wrong page can be confirmed.** `confirm` accepts a limit whose
  `quote_found` is false, and the confirmed `code` limit keeps `page: 21`.
  The probe confirmed blindly, as a script does; a person would be warned
  by the flag. Whether `confirm` should refuse or warn on
  `quote_found: false` is a design choice to make deliberately.

## What this does and does not establish

It establishes, on the live vendor API, through the production code:

* a real 45-page grid code is extracted in one call, with the forced tool
  accepted by `claude-sonnet-5`;
* the extracted values match the shipped DCC profile;
* every quote is verbatim;
* the quote check catches a bad page;
* the draft validates, publishes, and drives the campus run once covered;
* `extracted` and `code` tags flow through to the report rows correctly;
* the campus read tools are called with sensible arguments, their results
  reach the model, and the model respects their descriptions, including
  refusing to publish.

It does not establish:

* extraction from other codes (the RfG, national TSO codes), or from
  scanned PDFs or PDFs near the 100-page cap;
* the 400 → `auto` fallback, which was not triggered (no 400 occurred);
* the copilot's write tools (`campus_run_study`, `campus_draft_campus`,
  `campus_set_library`, `campus_extract_grid_code`), which were not called
  live;
* Guided mode's confirmation cards.
