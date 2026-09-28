# gridspine increment 8 — inverter dynamics in the handoff

Written before the code, as with every increment. Amended where building it
proved it wrong.

## Why this, and why now

The product is being pointed at the **grid edge**: data centres, large C&I
sites and industrial connections. The equipment behind those connection points
is overwhelmingly converter-interfaced: UPS rectifiers, on-site BESS and PV,
VFD-driven process load. The question a network operator asks at connection is
how the site behaves during a fault. Does it ride through, inject reactive
current, and recover active power at an acceptable rate? The 2024 Virginia
event, in which about 1,500 MW of data-centre load transferred to backup
power at once, is the reason operators now ask it every time.

Today every inverter in a handoff bundle is a **static injection**. The .dyr
writer omits them and the ledger says so. A PowerFactory or PSS/E engineer
who runs a fault on the bundle therefore sees the inverters hold their
pre-fault current through the dip, which is the one behaviour that answer
depends on and the one no real converter shows. The omission is honest, but it
makes the dynamics half of the deliverable silent about everything
grid-edge work needs.

## Scope

**In:** WECC second-generation generic models, the PSS/E library pair per
converter-interfaced unit:

- `REGCA1`: the converter/grid interface (current source, LVPL, HV reactive
  current management). 1 ICON, 14 CONs.
- `REECA1`: electrical controls (voltage-dip reactive current injection with
  gain `Kqv` and deadband, P/Q priority, current limit `Imax`, VDL tables).
  6 ICONs, 45 CONs.

The CON orders are the PSS/E model library's. They were cross-checked against
an independent reader, ANDES `andes/io/psse-dyr.yaml` (read, not vendored),
before any code was written. The test hard-codes the layout field by field,
the same way increment 2 wrote the GENROU/GENSAL records by hand before the
writer existed.

**Out, and ledgered:**

- `REPCA1`, the plant controller. It needs a measured branch (BUS1, BUS2, CKT)
  and a regulated bus, and case39's invented RES sites have no plant topology:
  no collector, no connection transformer, no point of connection distinct
  from the HV bus. Without it the Q/V references are held at their initial
  values, meaning local REECA1 control only. That is stated in the bundle's
  ledger. A later increment that models a facility's connection point is
  where REPCA1 belongs.
- Wind is modelled as **Type 4** (full converter): REGCA1+REECA1 with no
  drive-train model (`WTDTA1`). This is ledgered as an assumption.
- Load-side converter models (data-centre UPS/rectifier behaviour, `CMLD`
  composite load) are not in scope. That is the connection-compliance
  increment's question, not this one.

## Design

**Template.** An inverter's parameter group gains 65 optional fields (REGCA1: 1 ICON + 14 CONs; REECA1: 5 of its 6 ICONs + 45 CONs), the
REGCA1 and REECA1 ICONs and CONs, named `regc_*` and `reec_*` (the prefix
keeps REECA1 `Tp` apart from a later REPCA1 `Tp`). The group is
**all-or-nothing**: an inverter with no IBR fields is still a valid template
and is omitted from the .dyr as today, while an inverter with some but not all
of them is refused at load. A partial record would import cleanly with the
missing CONs read as zero, which gives a converter with zero current limit.
Every field carries its own provenance tag, as for synchronous machines. The
case39 values are **generic typical values, tagged `assumed`**, and are not a
vendor model. The YAML shares them through an anchor so five units do not
repeat 65 lines each. `yaml.safe_load` resolves anchors and merge keys, and
the loaded table still has one row per (unit, param).

ICON flags must be exactly 0 or 1. The following are checked at load, because
a typo in any of them imports cleanly and produces a plausible, wrong
response: `Tg > 0`, `Imax > 0`, `Iqrmin < 0 < Iqrmax`, `Iolim < 0`,
`Lvpnt0 < Lvpnt1`, `Zerox < Brkpt`, `Vdip < Vup`, `dbd1 <= dbd2`,
`Iql1 < Iqh1`, `Qmin < Qmax`, `Vmin < Vmax`, `Pmin <= Pmax`,
`dPmin <= dPmax`, and strictly increasing VDL voltages. REECA1's `BUSR` (the
remote regulated bus) is not a template field. It is a RAW bus number, which
only the RAW writer authors, and it is written as 0 (the terminal).

**Base.** Current limits and ramp rates are per unit on MBASE, so the
synchronous rule applies unchanged: the template's `mbase_mva` must equal the
RAW's MBASE, or the writer raises. Building this surfaced a bug the rule would
otherwise have hidden (see below).

**Identity.** Unchanged in principle. The records attach by (bus number,
machine ID), both authored by the RAW writer. `_machines` already counted
sgens so the IDs stay the RAW's. It now also reports each sgen's real MBASE,
through one function shared with the RAW writer so the two cannot drift. The
cross-module test parses both files and extends the (I, ID) agreement to
the IBR records.

**Bundle.** `dyr_units_written` counts IBR units, and `dyr_units_omitted` lists
only the inverters without an IBR group. The ledger's omission line names
those that remain (none on case39). A new study-assumption paragraph states
the model pair, the Type-4 treatment of wind, the absence of a plant
controller, and that the values are generic.

## A bug found before any feature code

Reading real bundles to check MBASE showed the RAW writer exported an
inverter's MBASE as **that hour's dispatched MW**. W_BUS_33 (600 MW) was
written at 449.994 in hour 3 and 352.569 in hour 17, and a curtailed solar
site at the 100 MVA system base. `_apply_res` overwrites sgen `p_mw` with the
hour's output, and with no `sn_mva` on the row the writer's documented
"installed `p_mw`" fallback read the dispatch instead. Nothing noticed because
no .dyr record sat on an inverter's base. With REGCA1/REECA1 records, `Imax`
would have scaled with the weather. The fix is at ingest: `load_case39_res`
writes each site's rating as `sn_mva`. It is committed separately, test first.

## Amended while building

- **A hand-written record text does not pin a layout.** The toy's expected
  REGCA1/REECA1 text was written from the library order before it was
  compared, and it passed. A mutation that swapped VFLAG and PQFLAG still
  passed, because the toy set both to 1, and a dozen CONs share 0.02 or 0.0.
  The layout is now pinned by a second test that gives every field a distinct
  value and checks the parsed record against the library order, typed out
  again in the test. Mutations swapping two flags, or two equal-valued CONs
  (Trv and Tp), are caught only there.
- **The writer came before its tests**, against the TDD order, and eight
  mutations (missing BUSR, transposed flags, unwrapped records, a restarted
  per-bus counter, an unchecked base, the MBASE fix reverted, NaN written,
  swapped VDL points) were run afterwards to show each test fails for its own
  reason.
- **The records are wrapped.** GENROU's 170-character line has always been
  accepted, but REECA1 would be about 520 characters. Five CONs per line keeps
  every converter line at 80 characters or fewer, and PSS/E reads records
  free-format up to the `/`.

## Not done, flagged

- Neither PSS/E nor PowerFactory has imported these records. REGCA1/REECA1 are
  PSS/E standard library models. Whether PowerFactory's PSS/E dynamic import
  maps them onto its WECC templates is **unverified**, and it joins the owed
  real-export check, which is blocked on a licence as before.
- No initialisation check. A dynamics engine initialises REECA1's `Vref0 = 0`
  and the Q reference from the load flow. A unit dispatched outside its
  `Qmin`/`Qmax` would fail to initialise, and nothing here runs a dynamics
  engine to catch that.
