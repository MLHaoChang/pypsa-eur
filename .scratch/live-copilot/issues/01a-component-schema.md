<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-01A — bounded native component-schema discovery

Status: done (#372, 0b33685c)
Parent: [LC-01](01-discovery-and-scope.md)
Starting master: `97867adecd46098cd58c120728cf1eed693ed265` (PR370).
Branch: `claude/copilot-discovery`; worktree `/workspace/copilot-discovery`.
Owner: current owner-authorized implementation session.

## Concrete compatibility contract

Existing `find_capabilities` discovers names/required arguments and existing
`get_component`/`list_components` retrieve active-project values. Native HTTP
catalog/asset-schema routes describe fields but have no paged chat equivalent.
Add one read-tier `get_component_schema(component_class, query='', offset=0,
limit=8)` through the ordinary catalogue/dispatcher. Reuse existing native
request models, PyPSA defaults and asset field descriptors; no new HTTP route,
execution path, domain algorithm or parameter write. Query is a bounded
case-insensitive field-name/label filter; pages are sorted by exact field name.

Response v1: component class, metadata schema SHA256, items, total_count,
offset, returned, has_more. Each field reports native input/output type and
stored unit, request-model JSON validation schema where declared, asset display
unit/scale where known, default/nonfinite disposition and derived/editable
status. Missing units/bounds stay unknown; do not infer them. Derived/output
fields are read-only even if legacy write routes accept them. Editability is
schema metadata, never current permission or an approval. Native handlers remain
validation authority, including cross-field rules absent from JSON Schema.
The metadata digest is not an input/policy fingerprint or grant.

Bounds: query <=80 characters, offset0–1000000, limit1–50; never accept booleans as
integers. Pack pages inside the ordinary structured-result budget; no silent
field/schema clipping or non-advancing pages. Refuse an individually oversized
field explicitly. Classify `schema_field_too_large` in the shared tool-error
manifest as an inline per-field refusal with a rationale; discovery is incomplete
for that field, rather than silently clipped or claimed complete. No model values, arbitrary DataFrame columns, paths, settings,
provider credentials or actual project identifiers enter the schema response.
Values remain on-demand ordinary reads. Strict checked policy is unchanged and
must refuse this unmapped tool. All11 currently registered component classes are
in scope; pending profile-generation/process-unit PRs are not installed features.

Class metadata can also be inspected in a networkless planning project, whose
empty native network retains class definitions. This never asserts that an
instance or usable engine exists; an actual value read must not fall back to
the previously active project's assets. Domain tags/service-owned settings
outside request/PyPSA/asset descriptors remain pending additional adapters.

## Agreed test seam and first RED

The approved LC-T01/T02/T17 public chat/native-read seams apply: real ordinary
tool dispatch using the existing isolated authenticated project fixture;
provider-wire HTTP fixtures for reachability; native HTTP schema/value reads as
controls. No mocked schema composition or successful application HTTP.
First RED: ordinary dispatch of `get_component_schema` for StorageUnit
`max_hours` returns its real hours unit and writable input descriptor;
current source has no such tool. Then regress all11 classes, complete
byte-bounded pagination/filtering, stored/display cost bases, enum/bounds,
computed/output metadata, invalid requests, schema drift, no value/secret
export, unchanged project values and strict refusal. Verify both provider wires
can discover/offer/use the tool within existing offer caps.

## Remaining LC-01 scope

This slice does not finish LC-01: exact actor/project/case/run/entity resolution,
named-target shortcut repair, eligibility/refusal/checking metadata across all
capabilities and non-component service/schema families remain separate slices.
Domain release validation, independent review, real-use calibration, paid calls,
reviewer contact and deployment/activation remain pending/out of scope.
