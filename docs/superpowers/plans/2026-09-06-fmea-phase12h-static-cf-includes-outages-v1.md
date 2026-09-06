# Phase 12h — a static capacity factor is applied, and "it already includes outages" is a flag the asset carries (plan v1)

**Status:** plan v1, for adversarial review before a line is written.
**Adjudicates:** the fourteenth finding (Phase 12c-pre, plan v2 §1.3): the
engines and the reserve margin disagree about a generator that carries a
STATIC `p_max_pu < 1` and outage data, by two errors of different size in
opposite directions, and the recorded resolution is "a per-asset 'this CF
includes outages' flag — a data-model change".

## 0. The premise, measured

One fixture, 168 h, load 100 MW flat; `nuc` 100 MW, static `p_max_pu = 0.8`,
`q = 0.05` (EFORd, MTTR 100 h); `gas` 25 MW, `q = 0.05`. Three readings of the
same unit, through the shipped code (`fleet_and_residual` →
`screening_analysis`; `reserve_margin_facts`):

| how the unit is read | COPT LOLE | COPT EUE | margin derate |
|---|---|---|---|
| **today**: engines ignore the static CF — the row equals "nameplate 1.0, q = 0.05" to the digit | 8.40 h | 640.5 MWh | 0.76 (= 0.8 × 0.95, both applied) |
| CF is a typed availability: applied as a constant series **and** outages sampled | **16.38 h** | **800.1 MWh** | 0.76 |
| CF already includes outages: applied, rate zeroed | 8.40 h | 168.0 MWh | **0.80** |

So the engines' error is not "25 %" in the abstract: on this fixture it is a
**halved LOLE** (8.4 against 16.38) and a 20 % EUE understatement under the
first reading, while the margin is right under the first reading and 5 %
optimistic under the second. Neither surface can know which reading applies,
because the static column carries both meanings in the wild — 12a's "a flat
0.25 typed on a farm" and PyPSA-Eur's `nuclear_p_max_pu.csv`, a historical
capacity-factor table that already contains forced outages, written to the
static column by `add_electricity.py`. The one fact that makes this
decidable is that **the two readings differ only in whether the outage rate
is applied**, and the rate is resolved at exactly one place:
`occurrence.resolve_outage_params`. Every consumer — both engines, the
reserve margin's `derate`, the net-load window, the worksheet, the
disclosures — reads `q` from that frame (measured above: the margin's
derate moved 0.76 → 0.80 with nothing but `q` changed). A flag that zeroes
the rate there resolves both surfaces at once, by construction.

**Also measured:** a boolean custom column survives the project's netCDF
round trip (`[True, False]`, dtype `bool`); the MC already handles `q ≤ 0`
at two sites (`mc.py:341, :439`) and the COPT's two-state builder is exact at
`q = 0`; the margin reads `avail_static` from the static column already
(`solver_service.py:3489`), so its half needs no change.

## 1. What ships

**H1 — the flag.** A custom Generator column `p_max_pu_includes_outages`
(bool, default `False`), declared beside the four occurrence columns in
`occurrence.py` and exposed the way `outage_rate_value` is: `GeneratorCreate`
field, `_bulk` (a `null` clears it to `False`, not to `None` — the bool
branch's current `None` write is a pre-existing gap this phase closes for
this column), the properties panel's outage block (Generator only), `types.ts`,
`propertyDocs.ts`. Generator only: the finding is about generators, and the
other occurrence-bearing classes carry no availability the engines read
this way.

**H2 — the rule, at the one place.** `resolve_outage_params` returns `rate =
0.0` for a row whose flag is set **and** whose source is not `missing`
(there is nothing to zero otherwise), keeps `basis`/`mttr_hours`/`source`,
and adds a column `outages_in_availability: bool` so every consumer can
say why the rate is zero without re-deriving it. Nothing downstream changes
code: `q = 0` is already "no outages to sample" in the MC and a degenerate
two-state unit in the COPT; the margin's `derate = avail × (1 − q)` becomes
`avail`; the worksheet and the payload rows show `q = 0`.

**H3 — the engines apply a static CF.** 12c-pre left the static column
unread on purpose, because folding it in *and* applying `q` double-counts
the PyPSA-Eur case. With H2 that case has a name, so the fold ships:
`_occurrence_profile` returns `None` as today when the static value is 1
(or the series is informative), and otherwise the unit's **capacity is
scaled** by the static value — `capacity_mw × cf`, and `capacity_series ×
cf` where 12d gave the unit a per-period series. Scaling, not a constant
profile, because a constant availability is exact as a two-state unit on
the COPT's grid and costs nothing, while a constant *profile* would push
every PyPSA-Eur thermal unit with a CF into the `2^k` mixture and past the
eight-unit cap on the first import. The continuity 12c-pre's review demanded
holds by construction and is pinned: static `0.8` and a constant series
`0.8` give the same COPT metrics to the grid's resolution and the same MC
draws under one seed.

**H4 — preflight tells the truth again.** `static_p_max_pu_not_applied` is
retired (its sentence is false after H3). Two codes replace it, both from
the same membership walk so they reach a carrier-default-only import:

| code | fires when | says |
|---|---|---|
| `availability_may_include_outages` (warning) | occurrence unit, `q > 0`, flag unset, static `p_max_pu < 1` and no informative series — **the old warning's exact population, so the noise is unchanged** | both the CF and the outage rate are applied (nameplate × CF × (1 − q)) by the engines and the margin alike; if the value is a historical capacity factor that already contains forced outages — PyPSA-Eur's nuclear table is one — set `p_max_pu_includes_outages` on the asset so the rate is not applied twice |
| `outages_folded_into_availability` (warning) | flag set on an occurrence unit | N generator(s) are modelled without sampled outages because their availability is declared to include them; the reserve margin credits them at the availability alone |
| `includes_outages_flag_has_no_effect` (warning) | flag set and (no outage data, or `p_max_pu` is 1 with no informative series) | the flag discards the outage rate of a unit whose availability is 1 — a fully firm unit — or has no rate to discard; almost certainly not intended |

`profile_and_outage_modelled` keeps its population and loses its false
remedy ("remove the outage rate", which a carrier-default unit cannot do);
it now names the flag.

**H5 — what does not change.** The portfolio population and the net-load
window are decided by the *series* rule (`series_is_informative` on the
column) and a static value creates no series, so neither moves. ELCC prices
the unit at its scaled capacity, which is what its best hour now is. The LP
is untouched: PyPSA has always applied the static `p_max_pu`.

## 2. What moves for a user (stated, not hidden)

Every project with a static `p_max_pu < 1` on an outage-bearing generator
sees its COPT, MC, ELCC and both certifying loops move after this — LOLE
*up* (the unit was credited at nameplate). A PyPSA-Eur import with nuclear
moves from the engines' large error (CF ignored) to the margin's small one
(rate applied twice) **until the flag is set**, and the preflight names the
asset and the flag on every such run. That is the trade the finding
recorded: the engines and the margin now agree, and the remaining question
is answered by the one party who can — the person who knows what the number
is.

## 3. Tests (every ★ with its bite)

- ★ H1a: `resolve_outage_params` returns `rate 0.0` + `outages_in_availability
  True` for a flagged row with asset data; unchanged for a flagged row with
  `source == "missing"`. Bite: ignore the flag.
- ★ H2a: the margin's derate reads 0.80 with the flag and 0.76 without, on
  the §0 fixture, through `reserve_margin_facts`. Bite: as H1a.
- ★ H3a: static `0.8` ≡ constant series `0.8`: `screening_analysis` metrics
  equal to 1e-9 and `mc_adequacy` LOLE/EUE identical under one seed. Bite:
  drop the capacity scaling — static reads the nameplate row (8.40 h).
- ★ H3b: the §0 table, all three rows, hand values pinned (`16.38`, `800.1`,
  `168.0`). Bite: as H3a.
- ★ H3c: 12d interaction — a static CF on a unit with a per-period capacity
  series scales the series, not only the scalar. Bite: scale the scalar only.
- ★ H3d: the COPT's split is unchanged by a static CF — a fleet of nine
  static-CF units stays `k_exact`-exact with no netted rows. Bite: fold the
  static value as a constant profile.
- ★ H4a–H4c: one test per code through `validate_for_run`, plus the retired
  code absent. Bites: fire the old code; drop the no-effect check.
- ★ H1b: `_bulk` `null` → `False`, not `None`; `POST /generators` accepts the
  bool; a netCDF round trip keeps it.
- Frontend: checkbox present on Generator only, round-trips through
  `outagePayload`; two vitest cases, bitten.
- The three 12c-pre tests that pin `static_p_max_pu_not_applied` are
  rewritten to the new codes.

## 4. Live — S31

| id | check |
|---|---|
| S31.1 | the §0 fixture over the API; preflight names `nuc` under `availability_may_include_outages` and the retired code is absent |
| S31.2 | `/results/copt` EUE reads **800.1 MWh** (before 12h: 640.5, the nameplate row) |
| S31.3 | `PATCH /_bulk` sets the flag; preflight now carries `outages_folded_into_availability` and not the first code |
| S31.4 | `/results/copt` EUE reads **168.0 MWh**; `/results/reserve_margin` derate for `nuc` reads **0.80** (before: 0.76) |
| S31.5 | clearing the flag through `_bulk` (`null`) reads back `false`, and the numbers return to S31.2's |

Bitten live by dropping the capacity scaling (S31.2 reads 640.5) and by
ignoring the flag in `resolve_outage_params` (S31.4 reads 800.1 / 0.76).

## 5. Out of scope, recorded

- A per-**series** "includes outages" for the dynamic column is covered by
  the same flag (H2 zeroes the rate whatever carries the availability), so
  nothing is deferred there.
- Inferring the flag from a carrier name or an import source (e.g. "nuclear
  from PyPSA-Eur") — not built: a guess in the data model is the thing this
  program keeps being wrong with.
- StorageUnit/Link/Line occurrence rows have no availability the engines
  read this way; the flag is not offered there.
