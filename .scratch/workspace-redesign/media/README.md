# Industry illustrations

Six original SVG industrial dioramas, authored as local vector geometry for the GridSpine design preview. Regenerate with `node ../create-industry-art.mjs` (the script resolves its own output directory). No stock imagery, copied screenshot elements, external downloads or model/media inference calls were used.

- `datacenter.svg`: campus halls, rooftop equipment, solar, batteries and electrical connection infrastructure.
- `microgrid.svg`: island, homes, renewables, storage and local supply.
- `hydrogen.svg`: industrial production, tanks, renewable supply and energy coupling.
- `ammonia.svg`: proposed process plant, columns, storage and piping.
- `mining.svg`: terraced excavation, process buildings, conveyor, trucks and energy supply.
- `ipp.svg`: a renewables IPP: wind turbines, a PV field, a battery row by the substation, and the export line to a pylon (IPP renewables + BESS ticket 06, spec §8.4).

The scenes are illustrative, not exact template topology, asset inventories, scale drawings or engineering evidence. The meeting screenshot supplied by the owner inspired an image-led card presentation only. People, meeting content, third-party logos and source images were not reused.

The build script embeds the images it lists (`build-preview.mjs` `mediaNames`; W2 adds `ipp` when it places the card) in the portable HTML. Keep scenario quantities and readiness labels in accessible HTML, not inside the artwork. Ammonia is owner-reported in development in #418 and unmerged; mining follows ammonia. Verify current canonical backend availability before application integration.
