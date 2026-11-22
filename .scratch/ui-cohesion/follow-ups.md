<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# UI-COHESION-1 follow-ups

- **Header search placeholder clips** (owner: PR B header work, with #1627/#1630's Run-strip rework). At 1280 px in Expert, with the Run button reading "Run LOPF (1 out of date)", the search field shrinks to about 97 px and shows "Search con". A 150 px floor was tried in PR A and pushes the avatar off screen (42 px overflow), so something else in the header has to give way. UX 2 agreed to leave it for PR B.
- **Glyphs outside the shipped font subsets** (PR B to check next to Inter): `₂`, `₁`, `₀` and arrows (`→ ← ↑ ↓ ↔ ⇒`) fall back to the system font.
