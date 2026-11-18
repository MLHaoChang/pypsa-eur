<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-04 "Export for Blender": UX shots

Real app (local backend + Vite, 1440x900), project `shotdemo`, Site "North campus", Site 3D canvas.
The button sits after Snapshot in the Site control strip, in Guided and Expert.

| State | Guided | Expert |
|---|---|---|
| Disabled (Site not yet a 3D scene): reason shown as text under the strip | guided-disabled.png | expert-disabled.png |
| Enabled (revision 2) | guided-enabled.png | expert-enabled.png |
| Exporting… (request held 4 s by a Playwright route delay) | guided-exporting.png | expert-exporting.png |
| Success toast after a real download of `North campus-scene-r2.zip` | guided-success.png | expert-success.png |
| Error toast (413 resource_limit mocked by a Playwright route) | guided-error.png | expert-error.png |

The "Site context unavailable" toast and caption are the sandbox's blocked map-data service, unrelated to this change.
The disabled shots were retaken after the switch to the server's `export_scene` verdict, in a fresh project (`exportdisabled5`, Island Microgrid template, Site saved but not adopted): the reason under the strip is the server's string. The map-data toast is hidden before the shot; the footer caption still names the sandbox's blocked map-data service.
