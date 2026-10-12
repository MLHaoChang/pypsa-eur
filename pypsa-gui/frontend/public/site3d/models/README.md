# 3D site view — hero models

Five models the 3D site view draws in place of its parametric boxes
(Phase 2 design `docs/superpowers/specs/2026-09-29-3d-site-view-phase2-design.md` §5).

| File | Stands in for | Source model |
|---|---|---|
| `windmill.glb` | wind turbines | `windmill.glb` |
| `shipping-container-a.glb` | BESS, genset, electrolyser, fuel-cell and heat-pump containers | `shipping-container-a.glb` |
| `solar-panel-landscape-group.glb` | PV tables | `solar-panel-landscape-group.glb` |
| `detail-tank.glb` | H₂ bullet tanks | `detail-tank.glb` |
| `building-s.glb` | data halls | `building-s.glb` |

## Source and licence

- Pack: **Kenney, City Kit (Industrial) 2.0** — https://kenney.nl/assets/city-kit-industrial
  (downloaded 2026-09-29; the pack's `License.txt` is copied here as `LICENSE-Kenney.txt`).
- Licence: **CC0 1.0** (public domain dedication). Verbatim: *"License: (Creative
  Commons Zero, CC0) http://creativecommons.org/publicdomain/zero/1.0/ … You can use
  this content for personal, educational, and commercial purposes. Support by
  crediting 'Kenney' or 'www.kenney.nl' (this is not a requirement)."*
- The app credits the pack anyway in the 3D view's attribution line: "Models: Kenney (CC0)".

## Processing (to regenerate a file)

From the pack's `Models/GLB format/` directory, for each file:

```
npx -y @gltf-transform/cli@4.5.1 optimize <name>.glb <name>.glb \
  --compress false --texture-compress webp --join-named false --flatten false
```

- **No Draco, no meshopt** (`--compress false`): the desktop app works offline and must
  never fetch a decoder; meshopt also rewrote the turbine's `blades` transform.
- `--join-named false --flatten false` keep the `blades` node separate (it spins).
- Each file then embeds its own 2.6 kB WebP copy of the pack's colour map
  (`EXT_texture_webp`, required). WebP is decoded by Chromium and by WKWebView on
  macOS 14, the packaged app's minimum.
- `src/site3d/heroes.ts` records each model's bounds, root scale and rotor pivot as
  measured from these files; `src/site3d/heroes.test.ts` fails if a regenerated file
  no longer matches.
