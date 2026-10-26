<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Optional asset footprints — implementation contract

Status: in progress; no review, CI or release approval claimed
Base: master aba252d2384f55116bbd15516a5b025951ba265e

## Bounded delivery

Add optional, project-scoped rectangular footprint dimensions (width_m, depth_m, optional height_m) for Generator, StorageUnit, Store, Transformer and Link equipment. These are total occupied-envelope dimensions for the named asset, not per-unit dimensions. Lines use routes rather than footprints. Existing Load/building geometry remains unchanged; campus/building polygons and AI-generated proposals belong to PR #711. ShuntImpedance/STATCOM rendering is not currently supported by the Site view: do not imply new renderer support.

Store overrides in the existing sites.json presentation sidecar under optional asset_footprints, keyed Class:name, separate from positions and native model inputs. No new route, solver input, chat tool, result invalidation or paid call. Empty/absent overrides preserve the existing layout exactly. Use a shared optional editor in the existing Properties panel and selected Site asset, equally in Guided and Expert; no new mode or workflow card. Width/depth are a pair, finite positive metres with bounded magnitude; height is optional and also positive. Save validates frontend and backend. Reset restores the existing library estimate. Existing saved-project ACL/read-only/lock/persistence paths remain in force; unsaved/offline/failed persistence is disclosed rather than reported durable.

An override creates an explicitly approximate rectangular physical proxy, rather than distorting detailed meshes. The proxy, packer, boundary/clearance checks, result anchors and area use the same dimensions. Exact dimensions remain fixed under installed/optimised sizing: changing capacity requires reviewing the user envelope, not silently rescaling measured data. No automatic electrical length update. Existing no-override templates remain unchanged and labelled library estimates. User-entered does not imply surveyed, manufacturer-verified, safe or compliant. Site area sums are estimates, not polygon unions or planning permission.

Rename rekeys the metadata through the existing native/frontend rename seam. Deletion/cascade cleanup removes footprint metadata through native side-data cleanup to avoid same-name recreation inheriting old dimensions. Test those paths; retain existing electrical behavior.

## Source research / scaling policy

NREL/TP-6A20-56290 (2013), https://docs.nrel.gov/docs/fy13osti/56290.pdf, distinguishes direct area from total site land and AC-rated capacity, with broad variation among utility-scale technologies. It is a candidate for an explicitly selected land-area assumption, not a universal PV enclosure default or MWdc conversion. Tesla's product specification at https://www.tesla.com/megapack/design lists discrete unit dimensions and energy/power ratings: a battery campus comprises counted units plus access/inverter/service space, not arbitrary linear scaling of every dimension. Reviewed 2026-10-24. No source data/mesh copied or new numerical default shipped.

Reuse the existing library's type-specific unit counts and dimensions for now. Future sourced defaults need provenance, capacity basis (MWac/MWdc/MWh/MVA), applicability, area-versus-equipment-envelope classification and explicit user opt-in. Scaling area linearly implies square-root scaling of both dimensions at constant aspect ratio; equipment modules normally scale in discrete counts. Transformers/STATCOMs need actual vendor/project dimensions and clearances; no universal MW-only law is asserted.

## Test seams and acceptance

- Existing owned sites HTTP/service: legacy documents pass; overrides round-trip; invalid class/key, incomplete pair, bool/string/null/nonfinite/nonpositive/out-of-range dimensions refuse; no network/result mutation. Existing ACL/locks/copy/bundle regressions remain applicable.
- Pure Site layout: omitted override is identical; exact 12×6 m equipment envelope gives 72 m², correct proxy/anchors, rotated fit and fixed dimensions under capacity changes; unsupported/invalid external overrides cannot inject malformed geometry. Native components remain unchanged.
- Shared editor/store: save/reset, cancelled drafts, invalid input, selection/project change, load failure, read-only and persistence error; both user modes use the same control. Site Arrange/Reset placement retains dimensions.
- Native and frontend rename/delete cleanup: metadata follows exact class/name, other classes/projects unaffected; same-name recreation gets no old override.
- Regressions: Site library/layout/fit/placement/store/model, property panel, asset-write, backend site service/routes and side-data cleanup; frontend type check, applicable semantic tests and exact-head CI. Record actual outcomes rather than invented counts. UI review remains required; no official reviewer was contacted from this session.

## Review/handoff

This implements optional footprint metadata/proxies only. PR #711 remains the separate campus/AI/Blender/photo specification. Current engine/UX gatekeeper references and actual master overlaps must be assessed before merge; coordinator owns merge. No broad engineering/domain approval or site reconstruction is claimed.

## Advisory review corrections

Two independent local review axes on initial implementation 0a36ec3815cc42b0ad9bc66cf9c188f268fc92f1 found a shared stale-cache deletion issue; Spec additionally found rooftop-proxy positioning, and Standards required visible disabled explanations. Corrections add exact successful-delete cache cleanup, guarded complete-list reconciliation preserving unrelated dirty edits and newly edited values, pre-cascade identity capture and project-change refusal, correct rotated free-roof positioning and visible loading/saving/read-only text. Public API/store and actual layout regressions cover the fixes. Initial verdicts do not certify a later head; renewed advisory review and official UX review remain separate gates.

The legacy sites.json store retains its existing whole-document persistence/concurrency model; this PR does not claim revision/CAS or cross-session asset-generation identity guarantees. Those broader guarantees remain in PR #711's Native contracts/identity milestones. Native deletion undo restores the model through its ordinary path; deleted footprint metadata is not part of native network undo. Library fallback remains explicitly labelled and dimensions can be re-entered.

## UX follow-up to c117493f (PR #712)

The owner relayed UX NOT OK at https://github.com/MLHaoChang/pypsa-eur/pull/712#issuecomment-6422564547. The bounded correction keeps the estimated-height hint only for a blank Height field; keeps the safety disclaimer for either height state; dims both disabled actions equally; clears saved/local status on read-only entry and prevents a late save response restoring it there. Project and selection remounts retain their status-reset behavior. Fallback/draft sentences use colons. The optional validation wording is clearer and displayed envelope area is rounded to one decimal place, with `<0.1` for small positive areas; stored geometry is unchanged.

The real app reproduced two footprint editors for one selected battery in Site 3D. App owns the placement decision: a Guided component drawer takes priority over ordinary Properties; otherwise visible Properties owns the editor, with the Site overlay as a fallback. Creation forms and specialized limit drawers are not treated as equipment editors. The leaf components consume this decision rather than duplicating the app layout policy. The note over the canvas remains visible. The real browser check observed two editors before the correction and one after it. These UI placement rules do not change solver availability or server run verdicts.

New screenshots must show the real authenticated application with its ordinary local backend and the illustrative data-centre template, in Guided and Expert: collapsed/open Properties, and selected-asset Site 3D with one editor and the estimate note. Prior synthetic component-card images are historical component coverage only, not the current UX evidence. No reviewer OK or new-head CI outcome is claimed by this contract; actual evidence belongs in the PR body and the shared pr712 evidence folder.
