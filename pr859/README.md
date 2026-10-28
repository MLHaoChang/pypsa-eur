Screenshots for PR #859 @0e4ec47f (SC-02b, Site 3D "Model" part and the context footer), real app at 1440x900, Guided and Expert (`NN-state-guided.png` / `NN-state-expert.png`).

Setup: project from the `eh_ammonia_staged` template (Loads on AC and NH3, `haber_bosch` process Link, `h2_storage` and `nh3_tank` Stores). The `eh_site` template has only two AC Loads and no process Links or Stores, so it could not show these states. Bus `hub` was placed inside the Site, coordinates confirmed (48.14, 11.58), Site "Campus" given a boundary and adopted. Before adoption, `site_battery` (12 x 3 m, entered size) and `h2_storage` (6 x 6 m, catalogue size) were placed. Three greenfield buildings were generated: hall = Building 1, shed = Building 2, office = Building 3. Attachments came from a `bind_targets` proposal through the API. Building 2 was attached to a Load `chiller`, and the Load was then deleted, which leaves the attachment unbound. Building 3 was attached to a heat Load the model does not have, so that attachment is unavailable. Overpass is offline here, which produces the footer in state 12.

| File | State |
|---|---|
| 01-unbound | Building 2: "Not attached: chiller · load was deleted or replaced › Attach again" |
| 02-attached | Building 1 attached to process_load and haber_bosch (Expert adds enclosures, capacity and "Not split") |
| 03-unavailable | Building 3: "Unavailable: no heat load in the model › Loads" |
| 04-picker-open | The "Attach to…" list. The native drop-down was turned into a list box (`size`) because headless Chromium does not draw native pop-ups. |
| 05-shares-85 | nh3_tank shared by Buildings 3 and 1 at 50 % and 35 %: "Total 85 %: shares should add up to 100 %". Preview is disabled. |
| 06a-add-asset | Right after "› Add model asset": Guided opens Hub design at Assets & sizing, Expert opens Capacity bounds |
| 06b-back | Right after "Back to Site 3D": Guided reopens Hub design over the canvas (see the PR report), Expert shows Site 3D |
| 06-place-it | After returning: "roof_pv · generator is in the model and not placed › Place it". roof_pv was added through the API because neither landing page has an add editor. |
| 07-placement-pass | h2_storage (catalogue size) previewed on the ground: "No overlap with buildings found" with Apply and Discard |
| 08a-overlap-finding | site_battery previewed at (0, 5): finding "Overlaps Building 1" |
| 08-overlap-highlight | The same preview after clicking the finding: site_battery and Building 1 are selected |
| 09-no-rule-checked | bess_new_2027 (no size): "No rule checked: overlap with buildings uses the centre point only…", form and preview |
| 10-approximate-size | site_battery (entered size): "Approximate size" on the item |
| 11a-confirm | roof_pv on the roof of Building 1, previewed with Apply |
| 11b-refusal | roof_pv on the roof at 100 m east: server refusal `off_roof` shown as "Off the roof of Building 1" |
| 12-context-footer | "Site context unavailable: the map data service could not be reached › Retry Details" with Details open. No empty pill under "Library dimensions…". |
