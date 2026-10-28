Screenshots for PR #859 @d13ce539 (SC-02b, Site 3D "Model" part and the context footer), taken in the real app at 1440x900. Each state has a Guided and an Expert shot (`NN-state-guided.png` / `NN-state-expert.png`). The add-asset trip (06a to 06c) runs one way, from Guided into Expert, so each of those steps has one shot.

Setup: a fresh project `pr859c` from the `eh_ammonia_staged` template (Loads on AC and NH3, `haber_bosch` process Link, `h2_storage` and `nh3_tank` Stores). Bus `hub` was placed inside the Site, coordinates confirmed (48.14, 11.58), and Site "Campus" was given a boundary and adopted. Before adoption, `site_battery` (12 x 3 m, entered size) and `h2_storage` (6 x 6 m, catalogue size) were placed. Three greenfield buildings were generated: hall = Building 1, shed = Building 2, office = Building 3. The attachments came from a `bind_targets` proposal sent through the API. Building 2 was attached to a Load `chiller`, and the Load was then deleted, which leaves that attachment unbound. Building 3 was attached to a heat Load the model does not have, so that attachment is unavailable. `roof_pv` was created through the real Expert creation form at fb5081c7; at d13ce539 `roof_pv_2` was created the same way (state 06). For the placement-unbound banner, a generator `temp_pv` was added without saving, placed, and then discarded by reopening the project. Overpass is offline here, which produces the footer in state 12. In Expert shots 07 to 12 from the earlier set the bottom table is collapsed; the shots retaken at d13ce539 have it open, and the Model part body now scrolls inside itself (max 40vh), so the Map / Generate / Model / Results tabs stay in view.

| File | State |
|---|---|
| 01-unbound | Building 2: "Not attached: chiller · load was deleted or replaced › Attach again" (Guided also shows "Next: … · 2 to fix") |
| 01b-placement-unbound | The placement banner, singular: "1 placed item is no longer in the model: the scene keeps its place until you attach it again" with "temp_pv · generator › Attach again" (Expert: "· Generator"). temp_pv was then attached again to roof_pv_2 |
| 02-attached | Building 1 attached to process_load and haber_bosch. Expert adds the enclosure count, the capacity and "Not split" |
| 03-unavailable | Building 3: "Unavailable: no heat load in the model › Loads". nh3_tank shows "Ammonia" as the carrier. Expert shows "Capacity: optimised" |
| 04-picker-open | The "Attach to…" list. The native drop-down was turned into a list box (`size`), because headless Chromium does not draw native pop-ups |
| 05-shares-85 | nh3_tank shared by Buildings 3 and 1 at 50 % and 35 %: "Total 85 %: shares should add up to 100 %" appears once, and Preview gives the reason "Make the shares add up to 100 % first" |
| 06a-add-asset-guided | Guided Place equipment with the link "› Add model asset in Expert" |
| 06b-kind-to-add-expert | After clicking it: the app is in Expert, with "Kind to add" open beside Site 3D and "Back to Site 3D" shown |
| 06c-add-form-expert | Renewable chosen: the creation form in the Properties panel beside Site 3D, filled in as roof_pv_2, hub, solar, 0.5 MW. "Back to Site 3D" is still shown after choosing the kind |
| 06-place-it | After "Add to Network": "roof_pv_2 · Generator is in the model and not placed › Place it" (Expert; the trip has ended, so "Back to Site 3D" is gone). Then switched to Guided and closed Hub design: "roof_pv_2 · generator … › Place it" (Guided). Named roof_pv_2 because roof_pv from the earlier set is already placed |
| 07-placement-pass | h2_storage previewed at 80 m east, 70 m north: "Exact size" (catalogue size) and "No overlap with buildings found", with Apply and Discard |
| 08a-overlap-finding | site_battery previewed at (0, 5): the finding "site_battery: overlaps Building 1" |
| 08-overlap-highlight | The same preview after clicking the finding: site_battery and Building 1 are selected |
| 09-no-rule-checked | bess_new_2027 (no size) previewed at "80 m west, 20 m south": only "No rule checked: no size entered for bess_new_2027, so overlap with buildings uses the centre point only", no "No overlap" pass beside it |
| 10-entered-size | site_battery (12 x 3 m entered in the footprint editor) chosen in Place equipment: "Entered size". h2_storage reads "Exact size" (07). No item in this project has a library-fit size, so no "Approximate size" shot |
| 11a-confirm | roof_pv on the roof of Building 1 at "10 m west, 5 m south", previewed with Apply (and the no-size line, no pass) |
| 11b-refusal | roof_pv on the roof at 100 m east: the server refusal shown as "roof_pv: off the roof of Building 1" |
| 12-context-footer | "Site context unavailable: the map data service could not be reached › Retry Details", with Details open. There is no empty pill under "Library dimensions…" |
| 12b-overpass-toast | After "› Retry": the plain toast "Site context unavailable: the map data service could not be reached" |

Retaken at d13ce539 (second browser-pass fixes): 01b-placement-unbound (both), 06a-add-asset-guided, 06b-kind-to-add-expert, 06c-add-form-expert, 06-place-it (both), 07-placement-pass (both), 09-no-rule-checked (both), 10-entered-size (both; replaces 10-approximate-size), 11a-confirm (both). The other files are from fb5081c7.
