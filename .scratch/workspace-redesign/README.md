# GridSpine three-section workflow design

Status: ready-for-agent — implementation handoff; native execution remains blocked by the separability gate.

This revision incorporates the owner's feedback: direct guided entry, three large work sections with selectable activities, reliability targets configured only within the optional assessment, example/generic starting models, Expert and one companion throughout, and a cool graphite/steel visual system with bright red actions. It replaces the earlier question-first entry.

- Open **`GridSpine-modern.html`**. It is a single portable file with embedded styles, script, five original industrial illustrations, logo, Manrope font and its OFL license. Only the latest generated HTML is versioned; earlier local preview aliases are omitted.
- The three sections are Energy & system planning, Grid & electrical design, and Controls & dynamic validation. Reliability has a planning objective in the first, with network resilience and operational-security assessment across the others; no fourth peer Reliability card.
- Microgrid/data-center examples preselect relevant electrical/control work; the hydrogen example emphasizes capacity/process work. All recommendations are editable and require native eligibility. Use example preset restores a recommendation; Select all/Clear changes the draft scope only.
- Home groups six templates into three industry cards (single-period and staged growth): data center, island microgrid and hydrogen hub. Ammonia (#418, unmerged) and mining (planned after ammonia) have separately labeled preview cards. Legacy IEEE/Belgian/tutorial grids are absent from this focused library; native templates/projects are preserved.
- Stress weeks and synthetic office/two-shift load shapes are draft Model & inputs options, not new workflow sections or executed profile transformations.
- Home offers a generic/own-model start and existing examples. Both lead to one builder. Examples suggest analyses; the user may remove them or add others.
- The nine choices have no separate dispatch/economics/load-flow checkboxes. Capacity includes dispatch and cost detail; grid-code assessment includes its required load-flow detail. Process and reliability are optional additions.
- Try capacity alone, capacity with optional process/reliability work, or grid-code assessment using an existing source. Select only the analyses you need, then enter Guided.
- Expert and the same companion remain available for detailed parameterization. The preview prepares chat drafts but never sends or applies them.
- Read `spec.md` for dependencies, source compatibility, admission/evidence constraints, production integration stages and detailed acceptance tests.
- `previews/` contains Home, Builder, Guided and Expert desktop/mobile screenshots, dark variants, the companion and the no-script fallback.
- `browser-checks.json` records only design journey checks. They do not certify native application E2E, scientific correctness, domain coverage or remote CI.

To regenerate in this environment:

```bash
cd <repository-checkout>
/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node .scratch/workspace-redesign/create-industry-art.mjs
/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node .scratch/workspace-redesign/build-preview.mjs
/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node .scratch/workspace-redesign/preview.mjs
```

Editable sources: `template.html`, `styles.css`, `prototype.js`, `sections-preview.js`, `expert-preview.js`, `create-industry-art.mjs` and `media/*.svg`. Regeneration reads the bundled `reference-assets/` font/license and mark, recoloring only the embedded preview mark. It works without the separate, uncommitted application-branding changes and does not modify application assets. Open the generated HTML; `template.html` is a build input, not the portable preview.

The browser serves only the bundled HTML at an isolated loopback URL and blocks all other HTTP resources. It checks interactive composition, drafts/mode/source behavior, conditional availability, responsive layouts and a styled fallback when JavaScript execution is disabled. It closes the browser/server afterward; normal loopback permission is required. This does not certify every attachment viewer's embedding policy. Download the HTML and open it in a browser if the viewer disables interactive scripts.

All models, values and state are illustrative. No API calls, inference, project creation, parameter writes, approvals, jobs or reports occur. The native typed dependency validator and orchestration adapters are future implementation work. Specialized frequency screening is distinct from general time-domain execution; generic control editing and simulator requirements remain explicit.

Required backend gate: complete `backend-separability-assessment.md` before admitting composed native execution. Source reading has found shared-source/state and mandatory-stage dependencies; it does not prove independent runnability. Mining is planned after the unmerged ammonia template. Both are explicit design-only presets, not invented bundled template IDs. Scenario details and development order come from the owner; GitHub status and native readiness need fresh verification before production integration.

Start with [HANDOFF.md](HANDOFF.md), then [spec.md](spec.md) and [backend-separability-assessment.md](backend-separability-assessment.md).

Next implementation: overview/assistant consolidation, Expert regrouping, project-owned composition and guided shell, then a native capacity-only vertical slice before connecting optional network/reliability stages. Preserve the pre-existing dirty branding implementation. This package publishes the design for implementation. The prior application-branding work is separate and is not included.

Browser verification requires Node, Playwright and Chromium. The supplied runner uses the managed runtime at `/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright` and `/usr/bin/chromium`; adapt those two runtime locations for another machine. Only a loopback server is used.
