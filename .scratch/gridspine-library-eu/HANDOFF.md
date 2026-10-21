# European library continuation handoff

Owner scope: deepen European tariff/commercial/technology/O&M/H2 research, inventory first, reconcile existing sources and integrate useful supported data. Work branch: `claude/eu-library-depth`; earlier cross-region publication remains PR #491 and is not rewritten by this package.

Start at README.md, then parameter-map.json, source-register.json and 06-INTEGRATION-TESTS.md. Read current repository AGENTS/steward and relevant domain/harness contracts before subsequent implementation. Baseline and executed verification are in VALIDATION.json.

Implemented: optional offline `services.library.europe_reference` reader and attributed CC BY selected-cell data, using native CostPart/PackValue. Five archetypes / 24 explicit years. It writes nothing, changes no default pack, and is not yet exposed to UI/chat. Preserve its source/applicability wrapper and exclusions in future proposals. Explicit selection and normal project approvals remain required.

Next bounded implementation is EU-2: expose the selected reference through existing Library find/review/use and the existing assumptions/apply path. Inspect current parallel Library/UI work before changing schemas or adding routes. No separate parameter database or engine. Do not silently replace generic defaults or old project pins.

Fresh parallel work inspected: PR #499 (head `ba0daf20b433a3d9ef38a17cf3178e67d51cce31`) adds explicit CA/UK/US regional overlays and skips them when choosing the default EU version; it is not merged at this check. It edits the shared defaults loader/pricing, which this package deliberately reuses without editing. Its overlay contract restricts rows to existing base keys; the new commercial-PV/AEC/PEM distinctions and projection-year axis require deliberate mapping before any UI/apply bridge. PR #498 records source clearances as a separate proposal, including scope limits; its `automatic_import_approved` flags stay false. These are observed PR records, not blanket legal/runtime permission for every new resource.

Then choose one Danish or DE/NL tariff subset. Danish metadata/API was inspected but terms pages did not render their substantive licence. DKK, charge-specific units, validity/DST, VAT and complete-bill semantics are real gates. Portugal's half-hour periods currently exceed native integer-hour windows. ETE PPA files are linked but website terms restrict commercial redistribution; no text copied. Source access failure is not a verified dataset.

Current numeric anomalies excluded: SOEC lifetime labelled years with 25,000-like values; H2 tank/cavern FOM power/energy/unit mismatch; v0.14 technology-data money-year discrepancy. Do not guess fixes or call these calibrated customer values. Follow up with source evidence in a separate scoped change; contact to publishers/reviewers requires the user's applicable authorization.

No new pricing/O&M engine dependency, paid model/media call, production deployment or reviewer contact occurred. No independent review of this new implementation is claimed by the previous research review. Keep human/domain review and real-use calibration pending.
