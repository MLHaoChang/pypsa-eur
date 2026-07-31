# Carrier-aware map icons + length-dependent line parameters — design

**Date:** 2026-07-31
**Branch:** `feature/local-app-impl`
**Follows:** `2026-07-30-unplaced-buses-map-design.md` (click-to-place is what makes Part B urgent)

## Goal

Two independent parts, requested together:

**A.** A solar generator shows a sun, not a wind turbine.
**B.** When a line's length changes, its length-dependent parameters follow it —
or the user is told they didn't, and decides.
**C.** `0 tCO2` never again means "nobody told me what this fuel emits".

They share no code and could ship separately. B and C are correctness fixes; A
is cosmetic. All three are built together at the user's request.

A and C turn out to share a shape: a default value that is indistinguishable
from a deliberate one. B's is `length`; C's is `co2_emissions`. The same
remedy applies to both — detect it, say so, and offer the fix rather than
applying it silently.

---

# Part A — carrier-aware pictograms

## Why now

[`MapCanvas.tsx:66-71`](../../../pypsa-gui/frontend/src/pages/MapCanvas.tsx) hard-codes one icon per *category*:

```ts
Renewables: { Icon: Wind, color: '#16a34a', dx: 110, dy: -70 },
```

So every renewable asset group is drawn as a wind turbine. The reporter's own
`3_nodes_system` has generator carriers `['gas', 'solar']` — its solar plant is
displayed as wind. Confirmed by reading the netCDF.

A per-carrier table already exists — [`CARRIER_BADGES`](../../../pypsa-gui/frontend/src/pages/TopologyCanvas.tsx)
(`TopologyCanvas.tsx:459-479`) with a substring-matching `getCarrierBadge()`.
Two problems: it is **inside** `TopologyCanvas.tsx`, so the map can only get it
by copying; and it has no `solar` entry (nor hydro, nuclear, coal, biomass,
geothermal, wave), so `getCarrierBadge('solar')` falls through to a generic
`Zap`. Fixing the map by copying the table would reproduce exactly the
copy-and-drift that `utils/carriers.ts` exists to prevent.

## Constraints

The map's asset bubble is **one per bus × category**, aggregating every
generator in that category at that bus ([`MapCanvas.tsx:755-768`](../../../pypsa-gui/frontend/src/pages/MapCanvas.tsx)).
A bus with solar *and* wind gets a single bubble, and no single icon is honest
for it.

## Decisions

**A1. The table moves to `utils/carrierBadges.tsx`**, imported by both canvases.
`.tsx`, not `.ts`, because it holds icon components and the inline `H2Icon`
SVG. Same remedy as `carriers.ts`, applied before the drift rather than after.

**A2. Uniform by BADGE, not by carrier string.** A bus with `onwind` +
`offwind-ac` is uniform — both resolve to `Wind`, so a turbine is honest and
specific. `solar` + `onwind` is mixed and keeps the generic category icon.
Testing badge identity rather than string equality resolves strictly more
groups to a specific icon without ever over-claiming.

**A3. The resolution is a pure exported function**, so it is testable without
React or Leaflet:

```ts
export function uniformBadge(carriers: string[]): BadgeDef | null
```

`null` means "mixed, or nothing to show" and the caller falls back to the
category icon. Same shape as `utils/geo.ts` and `utils/placement.ts`.

**A4. Applies to all four categories**, not only Renewables. Thermal is just as
wrong today (gas, coal and nuclear all render as one flame).

## What changes

New entries, using icons **verified present** in the installed `lucide-react`:

| carrier | icon | | carrier | icon |
|---|---|---|---|---|
| `solar`, `solar-rooftop` | `Sun` | | `nuclear` | `Atom` |
| `onwind`, `offwind-ac/dc` | `Wind` | | `coal`, `lignite` | `Factory` |
| `hydro`, `ror` | `Droplets` | | `oil` | `Fuel` |
| `biomass` | `Leaf` | | `geothermal` | `Mountain` |
| `wave`, `tidal` | `Waves` | | | |

`categoryCountsByBus` (`MapCanvas.tsx:755`) already iterates every generator,
load, storage unit and store with its carrier. Its value type becomes
`{ count: number; badge: BadgeDef | null }` — the badge resolved once, in the
memo, via `uniformBadge`. Its three read sites (the bubble at `:288` and the
two context-menu counts) update accordingly. Consumers then render
`badge?.Icon ?? CATEGORY_STYLE[cat].Icon`.

Keeping one map rather than adding a parallel carrier map is deliberate: two
structures derived from the same inputs is the same drift risk one level down.

---

# Part B — length-dependent line parameters

## Why now

`r`, `x` and `b` are stored by PyPSA in **absolute** Ω and S. The properties
form presents them **per kilometre**: it divides by length to display
([`PropertiesPanel.tsx:1799-1804`](../../../pypsa-gui/frontend/src/layout/PropertiesPanel.tsx))
and multiplies back on save (`:1732-1734`). So the UI's entire mental model is
that the per-km impedance is the physical constant and the absolute value
follows length.

Every backend path that changes length rewrites **only** length:

- `_recompute_lengths_for_bus` ([`network.py:330`](../../../pypsa-gui/backend/routers/network.py)) — fires on any bus coordinate change
- `recalculate_line_lengths` (`network.py:549`) — the map's Ruler button

Neither touches `r`, `x` or `b`. The displayed per-km value therefore changes
silently by the ratio of old to new length.

`3_nodes_system`, read from the netCDF:

| | L1 | L2 | L3 |
|---|---|---|---|
| length (km) | 1.780 | 1.437 | 2.007 |
| r (Ω, stored) | 3.0 | 6.0 | 4.8 |
| **r/km as displayed** | **1.69** | **4.17** | **2.39** |

`LineCreate.length` defaults to `1.0` (`models/schemas.py:60`), so every
hand-built network starts at nominal lengths. Placing those three buses at real
coordinates takes the lengths to hundreds of km while `r` stays 3.0 Ω, and the
displayed per-km resistance falls by two orders of magnitude.

**The click-to-place feature shipped in the previous spec is what makes this
easy to hit**, which is why it is being fixed now rather than later.

## Constraints

**Only `r`, `x` and `b` are length-dependent here.** Audited: `capital_cost` is
€/MVA, `fom_cost` is €/MVA/yr, and `solver_service.py` never multiplies either
by length. The `line.overnight_cost` doc string says "€/MVA·km or €/MVA", but
no code scales it. Designing for a broader set would be designing for something
that does not exist.

**There is no correct silent answer.** Either choice rewrites something the
user cares about:

- Hold **absolute** constant (today): the electrical model is untouched and
  flows do not change — but the displayed per-km value becomes unphysical.
- Hold **per-km** constant: the physics stays plausible — but `x` changes, and
  DC OPF splits flows inversely proportional to `x`, so **results move**.

That is why consent is part of the feature rather than a nicety.

**Recoverable.** `undo_snapshot_middleware` (`main.py:394`) pushes a snapshot
before every mutating network request, so an accepted rescale can be undone.

## Decisions

**B1. Length is geometry; impedance is a modelling choice.** Length keeps being
rewritten automatically — it follows from coordinates and is not a matter of
opinion. Impedance is never rewritten without consent.

**B2. Per-km is the quantity held constant** when a rescale is accepted:
`new_r = (old_r / old_length) × new_length`, likewise `x` and `b`. This matches
what the edit form already implies.

**B3. Preview, then apply — two separate calls.** The length-changing endpoints
report what a rescale *would* do and write nothing. A separate endpoint applies
it to an explicit list of line names. Mirrors the preview-then-import pattern
already used by "New project → From folder".

**B4. The threshold is 5% relative change, and it is a *length* test.** For a
pure per-km-preserving rescale, `new_r / old_r == new_length / old_length`
exactly, so the relative change is identical for `r`, `x` and `b` and equals
the relative change in length. Stated here so nobody re-derives it and
concludes the three must be tested separately. Changes at or below 5% are
applied automatically; anything above opens the dialog.

**B5. Placement batches; a single drag does not.** Prompting on every
click-to-place click would make the mode unusable, so during placement the
affected lines accumulate and prompt **once**, at the completion moment that
already exists. A bus dragged outside placement prompts immediately.

## What changes

### API

`RescalePreview` — one per line whose length changed:

```jsonc
{
  "name": "L1",
  "old_length": 1.78, "new_length": 476.3,
  "old": { "r": 3.0,  "x": 17.5,  "b": 0.00015 },
  "new": { "r": 802.7, "x": 4682.6, "b": 0.04013 },
  "rel_change": 266.6,          // |new_length - old_length| / old_length
  "skipped_reason": null        // or "old_length<=0" | "new_length<=0" | "all-zero"
}
```

- `POST /api/network/lines/recalculate_lengths` → gains `"rescale": [...]`
- `PUT /api/network/buses/{name}` → gains `"rescale": [...]`
- `POST /api/network/lines/rescale_impedances`, body `{"names": [...]}` →
  `{"updated": n, "skipped": [{"name", "reason"}]}`

The preview must be computed with the **old** length and **old** r/x/b captured
before the length is overwritten. `_recompute_lengths_for_bus` returns a count
today; it returns the previews instead.

### Frontend

Lines with `rel_change <= 0.05` and no `skipped_reason` are applied
immediately. Any line above it goes into a `Dialog`
([`components/Dialog.tsx`](../../../pypsa-gui/frontend/src/components/Dialog.tsx))
listing name, old→new length, and old→new per-km r/x/b. Nothing is written
until the user accepts. Declining leaves every value as it stands — including
the already-rewritten length.

The dialog states the consequence in one line: accepting changes `x`, and DC
OPF splits flows inversely proportional to `x`, so results will move.

### Edge cases that must not silently corrupt a model

| Case | Behaviour |
|---|---|
| `old_length <= 0` | per-km undefined → `skipped_reason: "old_length<=0"`, reported, never guessed |
| `new_length <= 0` (both buses on the same point) | would zero the impedance → `skipped_reason: "new_length<=0"` |
| `r`, `x`, `b` all zero | rescale is a no-op → no preview entry, no prompt |
| bus unplaced | length is not recomputed at all (previous spec's guard), so no rescale either |

## Success criteria

1. A bus whose only generators are solar shows a sun; one with solar and wind
   shows the generic renewables icon; one with `onwind` and `offwind-ac` shows
   a turbine.
2. Dragging a bus 1 m rewrites the length and rescales r/x/b with no prompt.
3. Placing the three buses of `3_nodes_system` at real coordinates prompts
   **once**, listing three lines with their old and new per-km values.
4. Declining that prompt leaves `r`, `x`, `b` exactly as they were, and the
   lengths as recomputed.
5. Accepting it leaves the per-km values exactly as they were, and the absolute
   values scaled by the length ratio.
6. A line whose stored length is 0 is reported as un-rescalable rather than
   being assigned an impedance.

## Tests

- `utils/carrierBadges.test.tsx` — `uniformBadge`: single carrier; two carriers
  sharing a badge (`onwind`/`offwind-ac`); two carriers with different badges;
  empty; unknown carrier.
- `utils/rescale.test.ts` — the ≤5%/above-5% partition, including exactly 5%.
- `backend/tests/test_line_rescale.py` — preview arithmetic; each of the three
  `skipped_reason` cases; that a preview call writes nothing; that
  `rescale_impedances` writes only the named lines.

## Out of scope

- Length-scaled **costs** — nothing in this codebase scales cost by length.
- Standard line types (`n.line_types`) — not used by this GUI.
- Changing the properties form's per-km presentation. It is the source of the
  invariant this spec makes the rest of the app honour.

---

# Part C — a zero that explains itself

## Why now

The reporter's Results tab shows `0 tCO2` for a network with a 300 MW gas
plant. **The number is correct.** `carriers_co2_emissions` is absent from
`3_nodes_system/network.nc`, so every carrier sits at PyPSA's default of `0.0`,
and the emissions formula (`routers/results.py:1705`) is

```
tCO2[g] = Σ_t (p[g,t] × weight_t) × co2_emissions[carrier(g)] / efficiency[g]
```

Multiply by zero and the answer is zero.

The intensity is missing because the generator's carrier is **`gas`**, and
`gas` is not in the 21-entry catalog (`services/carrier_catalog.py`):

```
AC DC onwind offwind-ac offwind-dc solar solar-rooftop ror hydro geothermal
biomass wave CCGT OCGT coal lignite oil nuclear H2 battery PHS
```

It has `CCGT` and `OCGT` — generator *technologies* — but not `gas`, the
*fuel*, which is an ordinary PyPSA-Eur carrier name. The netCDF carries the
fingerprint of the miss: `carriers_color = ['#70af1d', '', '#f9d002',
'#ace37f']` — `gas`'s colour is blank — and `carriers_nice_name` shows `gas`
where the catalog-matched carriers got `Solar PV (utility)` and `Battery`.

**Nothing warned, because both existing guards are conditional.**
`validation_service.py:438-443` fires only when `co2_price > 0`;
`validation_service.py:1148-1153` fires only when a global constraint exists.
The reporter's network has neither (verified: `global constraints: []`).

## Constraints

**The catalog lookup is exact and case-sensitive**, and `ensure_carrier`
returns early when the carrier already exists:

```python
if carrier_name in n.carriers.index:
    return
meta = CARRIER_CATALOG.get(carrier_name, {"nice_name": carrier_name, "color": "", "co2_emissions": 0.0})
```

Two consequences that shape the whole design: every spelling needs its own key
(`gas`, `Gas` and `natural gas` are three different misses), and **adding a
catalog entry can never repair an existing project** — the row is already
there, so the catalog is never consulted again.

**The catalog exists twice** — `services/carrier_catalog.py` and
`frontend/src/utils/carrierCatalog.ts` — kept in sync by a docstring asking
future editors to keep them in sync. Both must be edited together.

## Decisions

**C1. Add only the values this repo can already justify.** `gas` → `0.187`,
identical to the repo's own `CCGT`/`OCGT`, which burn natural gas. `diesel` →
`0.267`, identical to the repo's own `oil`. Anything further would be inventing
emission factors, and a wrong factor is worse than an absent one because it
looks authoritative. Coverage of the long tail is C3's job, not the catalog's.

**C2. A zero states its reason.** Where the Results tab reports `0 tCO2` and no
carrier in the network has `co2_emissions > 0`, it says so. A bare `0` cannot
distinguish "this system is clean" from "nobody told me what this fuel emits",
and those call for opposite actions.

**C3. The warning is ungated.** Warn whenever a generator's carrier has
`co2_emissions == 0` and the carrier name looks fossil — not conditioned on
`co2_price`, not conditioned on a global constraint. Those two conditions are
exactly why nothing fired here.

**C4. Offer, never rewrite** — the same consent model as Part B. The warning
carries a one-click action that sets the intensity, pre-filled with the catalog
value when one exists. This, not C1, is what repairs `3_nodes_system`. A user
who deliberately wants gas at zero keeps it by declining.

## What changes

Fossil detection is a case-insensitive substring match over `gas`, `coal`,
`lignite`, `oil`, `diesel`, `peat`, `waste`, `ccgt`, `ocgt`, `methane`, with
**`biogas` explicitly excluded** — it contains `gas` but its CO2 is biogenic
and conventionally counted as zero, so warning on it would be a false positive
that teaches users to ignore the warning.

The new warning is `carrier_zero_co2`, listing each affected carrier, the
generators on it, and the catalog value if there is one. The Results emissions
panel gains the C2 explanation. Both catalogs gain `gas` and `diesel`.

## Success criteria

7. A project whose only fossil carrier is `gas` raises `carrier_zero_co2`
   naming `gas`, with no CO2 price and no global constraint set.
8. Accepting the offered fix sets `co2_emissions = 0.187` on the `gas` carrier
   row, and the Results tab then reports non-zero tCO2 for `3_nodes_system`.
9. Declining leaves the row at `0.0`. The warning keeps appearing while the
   condition holds — `carrier_zero_co2` is an entry in an issues list, not a
   modal, and a list that stopped reporting a live issue after one glance would
   be hiding exactly the problem this part exists to surface.
10. A network whose generators are all `solar` and `onwind` raises no warning.
11. A generator on `biogas` raises no warning.
12. With no fossil carrier configured, the Results tab explains the zero rather
    than showing a bare `0 tCO2`.

## Tests

- `backend/tests/test_carrier_emissions.py` — `carrier_zero_co2` fires for
  `gas` with no co2_price and no global constraint (the exact gap that let this
  through); does not fire for `solar`/`onwind`; does not fire for `biogas`;
  the offered value matches the catalog where one exists and is absent where
  none does.
- The existing `ensure_carrier` behaviour — early return on an existing row —
  gets a test pinning it, because C4's necessity depends on it.

## Out of scope

- Emission factors for carriers this repo has no existing basis for (`waste`,
  `peat`, `shale`). C3 catches them; inventing numbers for them is worse.
- Deduplicating the two carrier catalogs. Real, and the same drift risk that
  motivated `utils/carriers.ts`, but a larger refactor than this change should
  carry. Recorded as a follow-up.

---

## Known limitations

**The 5% threshold will always trip on first placement.** Going from the 1 km
default to real geography is a change of thousands of percent. That is the
intended behaviour — it is exactly the change a user must see — but it means
the prompt is "once, on the big change", not a rare event.

**Accepting a rescale changes solver results.** By design, and stated in the
dialog. `undo` is the way back.

**Part C does not audit existing projects in bulk.** The warning fires for the
project you have open. A user with twenty projects fixes them one at a time.

**Not addressed here: the chat panel's missing API key.** Investigated during
this design and deliberately excluded. `chat_health()` reads
`os.environ["ANTHROPIC_API_KEY"]`; the packaged app has no `.env` (the
credential gate refuses any build containing one), a Finder-launched `.app`
inherits no shell environment, and the desktop launcher passes no key. That is
workstream K, and it needs its own design — where a user-supplied key is stored
(macOS Keychain vs. a mode-600 file in app-data), how it is entered, and how it
stays out of every future bundle.
