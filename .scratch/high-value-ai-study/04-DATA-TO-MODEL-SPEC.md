<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# HV-I: data-to-model onboarding assistant

Status: proposed; AI-567b includes documents as slice 2 after spreadsheet/equipment mapping.
Owning work: existing profile upload/TS-01/TS-04 for profiles, LC-16 for equipment mapping and subsequent document extraction, native asset/parameter editors and W9/W10. Profiles are not Library items. Does not create another data Library or a generic contract interpreter.

## User need and experience

“Here is my meter file and equipment table; help me set up this site” should produce reviewed mappings into the real model. It should not require a user to understand every internal field first, nor silently replace uncertainty with defaults.

Start inside the existing relevant card input/import surface. The single companion can explain candidate mappings and ask focused questions. A native preview shows source file/sheet/column/cell, target entity/field, original value/unit, converted value/unit, time or money basis, provenance, repairs, ambiguity and validation findings. The user can correct the mapping or open the exact native field. Both Guided and Expert operate on the same staged mapping and target.

Show partial progress without implying completion: for example, “meter profile mapped; timezone needs review; generator efficiency not provided.” A parser rejection has a visible reason and repair route. No second global Upload button, Save, Export or assistant is added.

## Functional requirements

1. **Read only owned, admitted files.** Current project ACL, upload MIME/size guards and bounded parsers apply. Uploaded text, filenames and embedded prompts are untrusted data; they cannot redefine tools, reveal secrets, grant access or trigger web/model calls. Never execute spreadsheet macros, formulas, attachments, embedded links or code. Refuse or safely handle archives/decompression limits under native policy.
2. **Start with narrow structured inputs.** Phase I1/I2 targets CSV/XLSX profiles and explicitly supported asset parameter columns. Existing measured-load ingest reads a full calendar year into hourly MW and documents repairs; Library series uses its own offset/naive timestamp rules. Preserve each parser's rules. Do not silently downsample rapid-load data into a planning profile or claim a short burst file is a valid full-year measured profile.
3. **Map against real schema.** Native types, enum allowlists, entity identity, dimensions, bounds and editable fields decide validity. AI proposes candidates, not schema or write permissions. Unsupported asset fields and ambiguous names remain unmapped. A quantity can require metadata beyond a unit, e.g. hydrogen LHV/HHV basis, annual versus lifetime cost, currency/price year, real versus nominal projection. Those adapters are gated on explicit semantics, not inferred from a plausible number.
4. **Keep exact lineage.** Bind full content hash, owned file ID, parser/version and sheet/cell or row/column ranges; for later document adapters use page plus bounded excerpt/region and extraction version. Retain original values and units, transformations/repairs, user decisions and target readback. The present upload ID is a truncated content address; do not call that alone a full provenance digest. Source replacement or a changed target invalidates the inspected preview.
5. **Make repairs and assumptions explicit.** Show native gap fill, rescale, interpolation and timezone assumptions and how many points change. The measured-load importer may infer/project/assume a timezone or repair small gaps; the assistant must expose those native notes rather than describe the upload as untouched measurement. Do not broaden repair thresholds. Unknown critical basis needs user resolution before the affected write. Measured, imported, generated, assumed and user-entered values retain distinct provenance.
6. **Preview before effects.** Parsing and proposal do not update model state, attach a live profile or start a run. Material mapping changes revalidate the preview. Require source digest, target/baseline digest, exact typed operations, validation state and ordinary native confirmation. A generic “use my file” chat message is not permission to write arbitrary discovered fields.
7. **Apply only through existing native tools.** Field edits, profile staging/apply and Library references use ordinary auth/locks/restrictions/confirmation and readback. Missing tool adapters mean unsupported, not direct filesystem edits by the model. Reuse exact native transaction/idempotency guarantees; a multi-row operation without a native transaction must report partial application and stop, not claim all-or-nothing or auto-rollback. Do not promise Safe restore until its independent defects are fixed and tested. A Saved snapshot is not a universal undo transaction.
8. **Correct and audit.** After apply, show the accepted operations, readback, lineage and remaining gaps. Repeated requests cannot duplicate entities or series through a new request ID; use source/target-bound native idempotency and explicit duplicate choice. Corrections use the same reviewed edit path; no hidden deletion of the original evidence. Deletes/retention changes use the existing policy, not a second unmanaged copy of sensitive source material.
9. **Stage rich documents by family.** PDF datasheets/tariff schedules/PPA clauses are the planned second extraction slice after spreadsheet/equipment-table mapping (AI-567b). Each extracted value appears beside its page/quote and needs confirmation before a native approved write. Demonstrate extraction, dimension/basis interpretation, unsupported clauses and native validation per family before enabling it. A successfully uploaded or readable PDF is not a billable tariff, executable PPA, grid asset or legally verified contract. Native unsupported tariff components keep the bill incomplete. External lookup feeds and source-terms clearance remain their own Library/owner decisions.

## Initial acceptance boundary

A useful first release need not parse every document. It should correctly map an admitted meter file, a small allowlisted equipment table and their gaps, with faithful native parse semantics and reviewed apply. It must demonstrate one EU DST/unit case and one North American interval-demand/power-versus-energy case using synthetic fixtures. No claim of live public-feed or measured industrial data coverage follows.

Expose existing limits rather than weakening them: bounded uploads (currently 25 MiB in the general store), per-parser point limits and the profile-family coverage requirements. Before provider disclosure, use existing user controls and permitted content handling; show which excerpts are sent and permit manual mapping/no-model continuation. No new paid calls are required for deterministic parsing or native preview.

## Slices and demonstration

- HV-I1 (profile upload/TS-01/TS-04 and LC-16/W9/W10): read-only schema/source mapping proposal and native validation; ambiguity, DST, units and source lineage.
- HV-I2 (existing field/profile apply/S7): exact reviewed operations, stale-preview refusal, partial-effect honesty, duplicate protection and readback.
- HV-I3 (LC-16 slice 2): PDF datasheet, tariff and PPA extraction in admitted families after spreadsheet mapping; source page/quote and per-value confirmation are required. Missing native tariff/PPA semantics remain incomplete, never enabled by extraction alone.

Tests I01–I16 are required before each corresponding slice can be called implemented. Information extraction accuracy alone is insufficient: wrong source, unit, target, hidden repair or unsafe application is a failure.
