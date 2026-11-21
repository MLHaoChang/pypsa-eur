<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Self-hosted fonts

The app ships its two typefaces so it renders the same offline (the desktop build) as online.

| File | Typeface | Source | Licence |
| --- | --- | --- | --- |
| `inter-{latin,latin-ext,greek}-wght-normal.woff2` | Inter, variable weight 100–900 | `@fontsource-variable/inter` 5.3.0 (https://github.com/fontsource/font-files), from https://github.com/rsms/inter | SIL OFL 1.1, `OFL-Inter.txt` |
| `jetbrains-mono-{latin,latin-ext,greek}-wght-normal.woff2` | JetBrains Mono, variable weight 100–800 | `@fontsource-variable/jetbrains-mono` 5.3.0, from https://github.com/JetBrains/JetBrainsMono | SIL OFL 1.1, `OFL-JetBrainsMono.txt` |

The files are the upstream subsets, unmodified. Latin and Latin extended cover the app's Latin text, the euro sign and the
degree and multiplication signs; Greek covers Δ, Ω, Σ, μ. Subscript digits and arrows are not in these subsets and fall back
to the system font, as before. `src/index.css` declares the faces, with local fallback faces sized to them
(`size-adjust`) so the swap does not shift layout; `spa.html` preloads the Latin Inter file.

To update: copy the new `files/*-wght-normal.woff2` and `LICENSE` from the package, and update the versions here.
