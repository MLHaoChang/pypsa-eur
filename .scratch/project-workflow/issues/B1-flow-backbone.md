# B1. Flow backbone: the section state endpoint

Status: ready-for-agent
Type: task
Blocked by: #426 merged (this design)
Gated: no engine change. Send the gatekeeper the plan first (steward "Before a plan"), because it reads `validation_service` and `services/adequacy/eh_readiness.py`. Touching `harness/catalogue.py` needs the coordinator's go.
Model tier: opus
Size: M–L

Read [the design](../design.md) first: §2.1 (the sections), §3 (the completion model) and §8 (owner decisions W1–W4).

## Goal

One read-only source of truth for "which flow sections are done". Later tickets build the UI on it: B2 (the Guided rail and the Expert sidebar), the Run gate, and the assistant. B1 builds **no UI** beyond the one client function and its type.

## What to build

1. **Section registry**, `pypsa-gui/backend/services/flow_sections.py`:
   - `SECTIONS`: the ten ids in order: `project`, `carriers`, `connection`, `loads`, `assets`, `profiles`, `commercial`, `scenarios`, `readiness`, `results`, each with its label (§2.1).
   - `CODE_SECTION`: issue code → section, for every code `validation_service` can emit. Then a fallback on `component_class` as in design §3.1: Load → loads; Generator, StorageUnit, Store and Link → assets, except the import/export Links that `archetypes.select_import_links_with_rule` picks, which go to connection; Bus, Line and Transformer → connection; snapshot and weighting codes → project; `carrier_*` → carriers; `discount_rate_*` → commercial; solver-mode, `sclopf_*`, `rolling_*`, `myopic_*`, `voll_*` and `co2_*` → scenarios.
   - **Presence rules.** A short table of required inputs, each read from data that already exists, with no new physics. Each is tagged with its kind from design §3.3 R2: (a) an existing server check, filed only; or (c) display only. B1 adds no kind-(b) validator error (R3):
     - project: snapshots non-empty;
     - connection: at least one import Link, unless the project is islanded (`not_needed`);
     - loads: at least one Load or product demand;
     - assets: at least one Generator, StorageUnit, Store or Link that isn't the connection;
     - scenarios: VOLL > 0 when the reliability study is requested.
     - Site location missing is a **warning**, not an error: the validator doesn't require it today.
2. **The collector**, `project_flow(project) -> list[SectionRow]`, built from:
   - `validate_for_run(n, config)`, taken under the lock exactly as `POST /simulation/preflight` does: the same three gates, the same `deferred` answer. While deferred, every section's state is `unknown`. It is never `complete`.
   - `eh_readiness(...)`, only when the project carries an EH template or archetype (`eh_template.json`), with the same arguments the hub-design Site card uses. Its `import`, `critical_buses`, `scr`, `outage_units.missing` and `warnings` are filed per design §3.1. A 422 refusal becomes one connection item, not a failed endpoint.
   - The renewable-profile readiness feed (RP-02), when it is on master. Otherwise skip it and say so in the PR.
   - The project's bind issues (`routers/projects.py`: commercial, finance, library pins, tariff reference load) → commercial.
   - Each item: `{code, severity, message, source, target: {section, panel, entity, field}}`. `target.panel` uses the existing `SlidePanel` ids; `entity` and `field` are left empty when they aren't known.
3. **States** (design §3.2):
   - `incomplete` = a presence rule fails, or an item has `severity: error`;
   - `warning` = there is an unacknowledged warning, or a template default marked `assumed` that the user hasn't confirmed;
   - `complete` otherwise;
   - `not_needed` from the template or an explicit user choice (islanded).
   - `readiness` and `results` carry no state of their own. `readiness` summarises 1–8. `results` reports `not_run` / `running` / `fresh` / `stale`.
   - Also return `run_ready: bool`, `run_blocker: {section, message, target} | null` and `notes: [{section, message, target}]`.
     - `run_ready` is **exactly the server's verdict** (design §3.3 R1): the `validate_for_run` result, plus, when the study is requested, the study start endpoint's own refusal function, called with the same `eh_readiness` arguments (one source for those arguments, the gatekeeper's point 3).
     - `run_blocker` is the first server refusal, filed by section in flow order.
     - `notes` lists the display-only (kind-c) gaps. They never make `run_ready` false.
     - Warnings never block (W3).
4. **Acknowledgements.** `POST /api/projects/{name}/flow/ack {code, entity}` stores `{code, entity, value_hash, at}` in a project sidecar `flow_acks.json`. A changed value (a different hash) re-raises the warning. Errors can't be acknowledged (422).
5. **Routes.**
   - `GET /api/projects/{name}/flow` → `{sections: [...], run_ready, run_blocker, deferred}`.
   - Add both rows to the route inventory fixture.
6. **Assistant.**
   - A read tool `get_project_flow`, returning the same payload. Append-only in `harness/catalogue.py`, after the coordinator's go.
   - `flow_section:` on each step of `harness/workflows/hub-design.md` and `build-network.md`. The registry parses it, and an unknown id fails the test.
7. **Frontend client only.** `api/flow.ts` (`getProjectFlow`, `ackFlowItem`, types), and the query key `nk(project, 'flow')`, invalidated with the network queries after every asset write. No component.
8. **Glossary.** Add Project flow, Flow section, Section state and Instantiation to `pypsa-gui/CONTEXT.md` (design "Words used here").

## Out of scope

- Any UI (B2).
- New validation checks. If a presence rule needs a fact no source gives, return the section as `unknown` with the reason and list it in the PR.
- Changing what `/run` refuses. `/run` keeps its own `validate_for_run` (single source).

## Tests

- `test_flow_sections_complete.py`: every code that `validation_service` can emit (collect them from the module's `_err` / `_warn` calls, as the release review did) has a `CODE_SECTION` row.
- Per section, the three cases: empty → `incomplete`; filled → `complete`; broken (an error item) → `incomplete`; plus a warning → `warning`, and acknowledged → `complete`, re-raised after the value changes.
- Deferred preflight (a solver in flight) → every state `unknown`, `run_ready: false`.
- The five templates (data centre, generic site, microgrid, H2 hub; ammonia when #418 is on master): the expected state per section at create, matching the instantiation table, design §2.3. Record any mismatch in the PR; don't paper over it.
- An islanded microgrid → connection `not_needed`.
- **R4, equivalence:** `run_ready` == the server's verdict, in both directions and on broken inputs. Cover the five templates, IEEE 14-bus, an islanded or off-grid network with no import Link (`run_ready` true, connection display-only `incomplete`, or `not_needed` when declared islanded), `snapshots_empty`, a study request with VOLL ≤ 0, and an in-flight solve (`unknown`, `run_ready` false).

## Done when

- `GET /flow` answers for the five templates within the preflight's time (report the measured time per template in the PR; `eh_readiness` copies the network, so cache it per network revision if it's slow).
- The tests above pass, along with the route-inventory and assistant-inventory checks.
- The UX reviewer's OK, as an API contract review: B2 must be able to render the Guided rail, the Expert sidebar dots and the Run reason from this payload alone.
