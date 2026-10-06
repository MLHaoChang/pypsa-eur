# gridspine increment 10: connection-point assessment

Written before the code, as with every increment. It will be amended where
building it proves it wrong.

## The question

Increment 9 answers how many MW a bus can take. Once a facility has a size
and a bus, a data centre, large C&I or industrial site faces the network
operator's next questions, and today's tool answers none of them:

1. **When the whole facility trips, what happens to the grid?** This is the
   data-centre question since the 2024 Virginia event, when about 1,500 MW of
   data-centre load transferred to backup at once. A large load that
   disappears in one step raises voltages and pushes power back through the
   network.
2. **What voltage step does the facility cause when it connects or
   disconnects?** Grid codes cap this rapid voltage change at the point of
   connection.
3. **Can the grid hold voltage while the facility uses the reactive-power
   range the code requires of it?** A demand or generation code asks the
   facility to absorb or deliver Q across a range. That is only meaningful if
   the POC voltage stays in limits at both ends of the range.
4. **Is the connection strong enough for converter-based plant?** This means
   the short-circuit ratio at the POC for the facility's own MVA. The
   existing SCR check only looks at the RES sites.

These are **steady-state screening checks** that a connection engineer runs
before the dynamic studies, not a compliance certificate. Fault ride-through
and dynamic grid support need RMS/EMT simulation, which is what the handoff
bundle, with increment 8's REGCA1/REECA1 records, is for.

## Scope

**In.** One **facility** is specified by:
- bus;
- kind (load or generation);
- P in MW;
- power factor;
- converter-interfaced or not.

The facility is assessed at each selected hour of a completed study. Every
check runs on that hour's snapshot plus the facility, with the same pro-rata
balancing as increment 9.

| check | what is computed | limit (see owner question) |
|---|---|---|
| **Facility trip** | Solve with the facility in, then with it removed and **no re-dispatch** beyond the slack (an instantaneous step). Report POC and worst-bus voltage change, and any new thermal or voltage violation after the trip. | voltage within the steady-state band after the trip, and step ≤ the rapid-voltage-change limit |
| **Rapid voltage change** | ΔV at the POC between "facility in" and "facility out", both directions. | e.g. 3 % (code-specific) |
| **Reactive range** | Solve at the leading and lagging ends of the required Q/Pmax range; report POC voltage and violations at each end. | POC and every bus within the band at both ends |
| **SCR at the POC** | IEC 60909 minimum Sk'' at the POC over the facility's MVA, from the existing fault-level stage. | reported against bands, as the existing SCR check does |

**Out, and ledgered:**
- Dynamic FRT, RoCoF and grid-forming behaviour (RMS/EMT; the bundle
  carries the models).
- Harmonics and flicker (the power-quality increment).
- Protection coordination.
- Frequency response, which needs a system inertia and frequency model per
  event. The ranking already reports inertia; a frequency-nadir estimate for
  facility trip is a candidate next step, not this increment.

## Design sketch

- `static/connection.py`: `assess_connection(net, facility, contingencies,
  limits) -> dict`. It reuses `capacity.apply_connection` for placement and
  balancing, and the N-1 raw outcomes where a post-trip N-1 is asked for.
- `schema/connection.py`: the result table (one row per hour × check), with
  pass/fail against the limit, the value, and the code-clause label the limit
  came from.
- **Limits** live in a small tagged table, `templates/data/grid_codes.yaml`,
  one profile per code family. Every limit carries its clause reference and a
  provenance tag, like the unit templates, so a report states which rule it
  applied. A study selects one profile.
- **Surfaces:** on-demand, like AC capacity. `POST /api/gridspine/{name}/connection`
  with the facility, and a GET for the stored assessments, the result kept in
  the run and the bundles. There is a panel section, entered from a capacity
  row ("assess a facility here"), and read and write chat tools, as in
  increment 9.

## Tests that decide it

- **Hand-built grids with worked answers:**
  - A radial line where tripping a 50 MW load raises the far-end voltage by a
    hand-computed amount.
  - A strong bus (big fault level) where the same trip barely moves it.
  - A weak bus where the Q range cannot be delivered without leaving the
    band.
- **The code table.** Every limit has a clause and a tag, and an unknown
  profile or a missing limit is refused at load.
- **Consistency with increment 9.** A facility sized at the capacity figure
  passes the thermal parts of the post-connection state by construction. The
  new checks can still fail it: a facility can fit thermally and still fail
  the voltage step. That is the point of the increment, and a test will show
  one.
- **Browser run at the end.**

## Decided by the owner (2026-09-29)

1. **The first profile is EU RfG/DCC generic, Continental Europe.**
2. **A facility is a load plus a separate on-site unit** (BESS or generator)
   at one connection point. The load-trip check drops the load while the
   on-site unit stays connected, closer to real data-centre behaviour than a
   single net injection.

## The limits, verified against the regulations' own text

Read from the **as-adopted EU text** on legislation.gov.uk
(`/eur/2016/1388/.../adopted`, `/eur/2016/631/.../adopted`). The retained
post-2020 UK version has deleted the Continental Europe rows, and EUR-Lex
could not be fetched from this environment.

| limit | value | clause | tag |
|---|---|---|---|
| Steady-state voltage band, 110 to under 300 kV | **0.90–1.118 pu** unlimited (1.118–1.15 pu for a TSO-set 20–60 min) | DCC Annex II, first table, Continental Europe; RfG Art. 16(2)(a), Table 6.1 | code |
| Same, 300–400 kV (case39 is 345 kV) | **0.90–1.05 pu** unlimited (1.05–1.10 pu for a TSO-set 20–60 min) | DCC Annex II, second table; RfG Table 6.2 | code |
| Below 110 kV | 0.90–1.10 pu | not set by RfG/DCC; ±10 % used | assumed |
| Reactive range a TSO may require of a transmission-connected demand facility | **±0.48 Q/Pmax** of the larger of max import or export (the "0.9 power factor") | DCC Art. 15(1)(a): "shall not be wider than 48 percent" | code (the **maximum**; the TSO specifies the actual range) |
| Shared connection point, demand facility plus a generating unit | requirement met at the point set by agreement or national law | DCC Art. 15(1)(f) | code |
| Rapid voltage change on connection or trip | **3 %** at the POC | not in RfG/DCC. IEC TR 61000-3-7 gives indicative planning levels, about 3–5 % at HV/EHV for infrequent changes, but its text is not freely available, so it was not checked here | **assumed** |

**Not used, and why.** RfG Art. 21(3)(b), Table 9 gives a power park
module's U-Q/Pmax envelope: at most 0.75 wide in Continental Europe, within
0.225 pu. That profile's position is the TSO's choice, so it cannot be
defaulted honestly as a symmetric ±. It also applies to the on-site unit on
its own terms, not to the facility at a shared POC. The facility's reactive
check therefore uses DCC Art. 15. The on-site unit's own RfG compliance is
out of scope and ledgered.

## Checks, as they will be built

For a facility {bus, load_mw, load_pf, onsite_mw, onsite converter or not}
at each selected hour:

1. **Connection.** The facility is connected, net import balanced pro-rata
   (increment 9's rule). No new or worsened violation, intact and N-1.
2. **Energisation step.** Facility off to on with **no re-dispatch** (the
   slack takes it: an instantaneous step). ΔV at the POC must stay within the
   RVC limit.
3. **Load trip.** From the connected state, the load drops and the on-site
   unit stays, with no re-dispatch. Checked: ΔV at the POC within the RVC
   limit; every bus within the steady-state band (no new or worsened
   violation relative to the connected state); no new thermal violation.
4. **Whole-facility trip.** As 3, with the on-site unit dropping too.
5. **Reactive range.** The facility at +0.48 and −0.48 × max(import, export)
   Q. POC and every bus within the band at both ends (no new or worsened
   violation).
6. **SCR at the POC.** IEC 60909 minimum Sk'' over the on-site converter
   MVA, and over the load's MVA (converter-fed data-centre load). This is
   reported against the existing bands, not gated, in line with
   `static/strength.py`.

## Stage A, as built (2026-09-29)

`templates/grid_codes.py` and `templates/data/grid_codes.yaml` hold the
profile: every limit has a clause and a `code`/`assumed` tag, bands may not
overlap, and an unknown profile or a missing limit is refused.
`static/connection.py` and `schema/connection.py` hold the eight checks.
They reuse increment 9's solve path, its no-new/no-worse comparison and its
pro-rata balancing, with the profile's per-bus voltage band in place of the
screen's generic one.

**Tests.** 26 tests: every voltage step against pandapower solved directly,
plus the profile's validation. Seven mutations were run and six were caught
at once. The seventh, **energisation re-dispatching the fleet, survived**,
because none of the hand-built grids had a generator to re-dispatch. A grid
with one was added, and the step is now pinned to the instantaneous value
(the slack takes it all).

**A hand estimate corrected.** The 50 MW trip was estimated at about 2.7 %.
The engine and pandapower agreed on 3.24 %, because the estimate ignored the
5 MW already at the bus and the non-linearity. The passing case was re-sized
to 40 MW.

**On case39** (all 345 kV, so the 0.90–1.05 pu band applies): a 300 MW data
centre with a 100 MW BESS at BUS_16.

| check | result |
|---|---|
| connection | **fails**: it deepens the known `BUS_02-BUS_03` N-1 overload |
| energisation | passes: −0.73 % step at the POC |
| load trip | **fails**. The step at the POC is only +0.69 %, but losing 300 MW with the BESS still exporting pushes BUS_19, BUS_22 and BUS_26 above 1.05 pu. This is the data-centre load-loss effect, and it would be missed by looking only at the POC. |
| reactive range | fails injecting 144 Mvar; passes absorbing |
| SCR | 90 and 29.4, reported |

## Stages B and C, as built (2026-09-29)

**Stage B.** `drivers/connection.py` assesses a facility at every selected hour
of a screened run, on the same rebuilt hour state the handoff pass used. The
minimum Sk'' at the POC comes from that hour's bundle (`fault_levels.csv`).
The rows are stored in `connection.csv`, in the run and in each bundle, keyed
by a stable `assessment_id`, so assessing the same facility again replaces
its rows. The backend has `GET`/`POST /api/gridspine/{name}/connection`: 409
while a study is queued or running, 404 before a screened run, 422 for a bad
facility. There are two chat tools, `gridspine_assess_connection` (write) and
`gridspine_get_connection_assessments` (read). The write tool's description
says the assessment is a screen, not a compliance certificate.

**Stage C.** The panel has a *Connection-point assessment* section. Each
capacity row has an *Assess* button that fills in the facility bus. Each
check shows:
- the result;
- the value;
- the limit it was held to (≤ for a step or for the injecting end, ≥ for the
  absorbing end);
- the detail;
- the clause, with its `code`/`assumed` tag.

The section says in its body, not only in its hint, that it is not a
compliance certificate. Eight mutations were run and all eight were caught.
One of them, removing the lock, first survived, because an empty form also
disables *Assess*. The test now fills the form first.

**Browser run** (case39 UC study, hours 3, 17 and 19): a 300 MW data centre
with a 100 MW BESS at BUS_16 took 2.4 s for the three hours. The stored
assessments survive a fresh page. A second facility adds a picker, and an
unknown bus is refused inline.

**A bug the run found.** An absorbing end that passes on a POC that was
already above the band (for example BUS_04 at 1.061 pu) was reported as
"within 0.9-1.05 pu". Under the no-new-no-worse rule it does pass, because
absorbing pulls the voltage down. But the wording was false. It now reads
"outside 0.9-1.05 pu already, not worsened", pinned by a test in both
directions.
