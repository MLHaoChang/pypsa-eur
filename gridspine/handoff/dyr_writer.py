"""PSS/E .dyr writer — the handoff contract's dynamics half.

One record per synchronous machine, model class from the template, and a
REGCA1 + REECA1 pair per converter that has a group (below)::

    I 'GENROU' ID  T'do T''do T'qo T''qo H D Xd Xq X'd X'q X''d Xl S(1.0) S(1.2) /
    I 'GENSAL' ID  T'do T''do T''qo H D Xd Xq X'd X''d Xl S(1.0) S(1.2) /

Identity is the whole risk. A record attaches to a machine by (bus number,
machine ID), and both are authored by the RAW writer: bus numbers are
bus-table order, 1-based; machine IDs are a per-bus counter run over gen,
then ext_grid, then sgen. This module therefore does not reproduce that
convention — it CALLS ``raw_writer._bus_numbers`` and ``_IdCounter`` and walks
the machine tables in the same order, and
``tests/gridspine/test_dyr_writer.py`` parses both emitted files to prove the
(I, ID) sets agree. A .dyr whose numbering disagrees with its .raw imports
cleanly and attaches machines to the wrong buses, which is worse than no
.dyr at all.

Base: H and every reactance in a .dyr are on MBASE, the machine base the RAW
record declares (``sn_mva`` when finite, else the 100 MVA system base). The
template states its own base in ``mbase_mva``; a mismatch RAISES. Rescaling
H silently would produce a plausible, wrong swing curve, and the template is
the ledger — if the base is wrong, the ledger is wrong, and that is the thing
to fix.

Inverters (``model: inverter``) get the WECC second-generation pair,
``REGCA1`` (converter interface) then ``REECA1`` (electrical controls), when
their template carries the REGCA1/REECA1 group (``ibr_params``)::

    I 'REGCA1' ID  Lvplsw  Tg Rrpwr Brkpt ... Accel /                 1 ICON, 14 CONs
    I 'REECA1' ID  BUSR PFFLAG VFLAG QFLAG PFLAG PQFLAG  Vdip ... Ip4 / 6 ICONs, 45 CONs

ICONs precede CONs, both in ``unit_params``' tuple order, which IS the PSS/E
library layout. BUSR is written 0 (regulate the unit's own terminal): it is a
RAW bus number, and no template can author one. The two records are wrapped,
five CONs to a line — REECA1 is 51 values — and are otherwise free-format as
PSS/E reads them. Their base is the same MBASE rule as a synchronous
machine's: ``raw_writer.sgen_mbase`` is the one function both files use.

An inverter WITHOUT the group, or any inverter when no ``ibr`` frame is
passed, is omitted rather than handed a synchronous record, and is absent
from the returned mapping so the caller can ledger the omission. Legacy
H-only units cannot be written either — a GENROU built from H alone would be
invented.

Nothing named reaches the file: records carry numbers and counter IDs only,
so the canonical-charset guard has no work to do here.

Record order follows the RAW's machine records (gen table, then ext_grid,
then sgen), not bus-number order, so a reader can walk the two files side by
side.

**Governors and exciters** (island plan I8): ``DYR_CONTROLS`` holds the
layouts of the PSS/E library records a campus genset needs, ICONs then CONs
in library order, and ``format_record`` writes any record in the same style
as the machine and converter records::

    I 'DEGOV1' ID  M  T1 T2 T3 K T4 T5 T6 TD TMAX TMIN DROOP TE /      1 ICON, 12 CONs
    I 'SEXS'   ID  TA/TB TB K TE EMIN EMAX /                            6 CONs
    I 'GENCLS' ID  H D /                                                2 CONs (H = 0: an infinite bus)

The WECC grid-forming records (REGFM_A1, REGFM_B1) are NOT written here:
their PSS/E CON order is not verified, and a record in a guessed order is a
plausible wrong converter. ``handoff/campus_bundle`` carries their
parameters by name instead (owner decision, I8).

**The rest of the WECC set** (GC-04 PR3), from ``unit_params.wecc_params``:

- ``REECC1`` (battery electrical control) in place of REECA1 when the unit
  carries the state-of-charge fields. A PSS/E library model from v35 only
  (REECCU1, a user model, before), so ``version=33`` refuses it.
- ``REPCA1`` (plant controller): ``BUSR BUS1 BUS2 'CKT'`` come from the RAW's
  own branch identity (``raw_writer.branch_ids``) of the plant's PoC branch
  (``PocBranch``): BUS1 the plant end, BUS2 the grid end and also BUSR, so
  BUS1 -> BUS2 flow is export, positive. A missing branch is refused, never
  written as 0. ONE controlled machine per PoC branch, unless the machines
  are a declared detailed plant (``plants``, GC-04b): N REPCA1 records each
  measuring the whole plant's flow on their own MBASE would multiply the
  loop gain by N, so undeclared sharing is refused, and a declared plant's
  REPCA1s are rescaled by each machine's MBASE share (``share_repca1``) so
  their sum acts as the aggregated machine's one controller.
- Type 3 wind: ``WTDTA1 WTARA1 WTPTA1 WTTQA1`` after the converter pair.

Every converter record goes through ``check_model`` and its ``MODEL_PIN``
counts. REGC_B and REEC_D are refused by name (``UserModelOnly``): PSS/E has
them only as user models up to 35.2. The pins and orders come from public
parser tables (``PIN_NOTE``), pending verification on the first PSS/E load.
``version`` is 33 or 35 and changes only which models may be written: a v35
file of a v33 set is the v33 file byte for byte.
"""
import dataclasses
import math

import pandas as pd

from gridspine.handoff.raw_writer import (
    SBASE_MVA,
    _bus_numbers,
    _IdCounter,
    branch_ids,
    sgen_mbase,
)
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import (
    REECA1_CONS,
    REECA1_ICONS,
    REECC1_CONS,
    REECC1_ICONS,
    REECC1_ONLY,
    REGCA1_CONS,
    REGCA1_ICONS,
    REPCA1_CONS,
    REPCA1_ICONS,
    WECC_GROUPS,
    WTARA1_CONS,
    WTDTA1_CONS,
    WTPTA1_CONS,
    WTTQA1_CONS,
    WTTQA1_ICONS,
)

#: PSS/E v33 CON order per model, in template field names.
DYR_CONS = {
    "GENROU": (
        "t_do_p", "t_do_pp", "t_qo_p", "t_qo_pp", "h_s", "d",
        "xd", "xq", "xd_p", "xq_p", "xd_pp", "xl", "s1", "s12",
    ),
    "GENSAL": (
        "t_do_p", "t_do_pp", "t_qo_pp", "h_s", "d",
        "xd", "xq", "xd_p", "xd_pp", "xl", "s1", "s12",
    ),
}


def _machines(net):
    """Every machine as (unit_id, bus_number, machine_id, mbase, synchronous),
    in RAW record order, IDs from the RAW's own per-bus counter."""
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    ids = _IdCounter()
    out = []
    for _, g in net.gen.iterrows():
        sn = g.get("sn_mva", float("nan"))
        mbase = float(sn) if (sn is not None and math.isfinite(float(sn))) else SBASE_MVA
        num = nums[name_of.at[g["bus"]]]
        out.append((g["name"], num, ids.next(num), mbase, True))
    for _, e in net.ext_grid.iterrows():
        num = nums[name_of.at[e["bus"]]]
        out.append((e["name"], num, ids.next(num), SBASE_MVA, True))
    sgen = getattr(net, "sgen", None)
    if sgen is not None:
        for _, s in sgen.iterrows():
            num = nums[name_of.at[s["bus"]]]
            out.append((s["name"], num, ids.next(num), sgen_mbase(s), False))
    return out


#: Governor and exciter layouts: model -> (ICON fields, CON fields), in the
#: PSS/E model library's order.
DYR_CONTROLS = {
    "DEGOV1": (("m",), ("t1", "t2", "t3", "k", "t4", "t5", "t6", "td", "tmax", "tmin", "droop", "te")),
    "SEXS": ((), ("ta_tb", "tb", "k", "te", "emin", "emax")),
    "GENCLS": ((), ("h", "d")),
}


def machine_records(net):
    """``(unit_id, bus_number, machine_id, mbase, synchronous)`` for every
    machine, in RAW record order with the RAW's own IDs (public for the
    campus bundle, which attaches its own records to the same identities)."""
    return _machines(net)


def format_record(num, mid, model, values):
    """One record of a ``DYR_CONTROLS`` model: ``values`` maps each ICON and
    CON field to its number; a missing one raises (a zero is a plausible
    wrong control)."""
    icon_names, con_names = DYR_CONTROLS[model]
    missing = [n for n in icon_names + con_names if values.get(n) is None or pd.isna(values.get(n))]
    if missing:
        raise ContractError(f"{model} at bus {num}: {missing} missing; a zero here is a plausible wrong control")
    head = f"{num:>6d} '{model}' '{mid:<2s}'" + "".join(f"{int(values[n]):6d}" for n in icon_names)
    return head + " " + "".join(_con(float(values[n])) for n in con_names) + " /"


#: Every model this writer emits, pinned: model -> (ICON count, CON count,
#: earliest PSS/E version shipping it as a library model, sources). Counts
#: include ICONs the writer authors (REECA1/REECC1 BUSR; REPCA1 BUSR, BUS1,
#: BUS2, CKT). The counts and orders come from PUBLIC PARSER TABLES, not from
#: the PSS/E manual (which is not public); ``PIN_NOTE`` says so in the pack.
_ANDES = "andes andes/io/psse-dyr.yaml (PSS/E dyr input tables)"
_GRIDPACK = "GridPACK src/parser/parser_classes/{} (dyr parser class)"
_VERAGRID = "VeraGrid src/VeraGridEngine/IO/raw/versioned/v35/dyr.py (v35 dyr tables)"
MODEL_PIN = {
    "REGCA1": (1, 14, 33, (_ANDES, _GRIDPACK.format("regca1.hpp"))),
    "REECA1": (6, 45, 33, (_ANDES, _GRIDPACK.format("reeca1.hpp"))),
    "REECC1": (5, 45, 35, (
        _VERAGRID,
        "OpenIPSL OpenIPSL/Electrical/Renewables/PSSE/ElectricalController/REECCU1.mo (model documentation)",
    )),
    "REPCA1": (7, 27, 33, (
        _ANDES, _GRIDPACK.format("repca1.hpp"),
        "PowerSystemsTestData psse_dyr/240busWECC_2018_PSS.dyr l.800-807 (REPCA1 record)",
    )),
    "WTDTA1": (0, 5, 33, (_ANDES, _GRIDPACK.format("wtdta1.hpp"))),
    "WTARA1": (0, 2, 33, (_ANDES, _GRIDPACK.format("wtara1.hpp"))),
    "WTPTA1": (0, 10, 33, (_ANDES, _GRIDPACK.format("wtpta1.hpp"))),
    "WTTQA1": (1, 15, 33, (_ANDES, _GRIDPACK.format("wttqa1.hpp"))),
}
PIN_NOTE = (
    "dyr ICON/CON counts and order from public parser tables; "
    "pending verification on first PSS/E load"
)
#: .dyr targets: the set written is the same for both; v33 refuses REECC1.
DYR_VERSIONS = (33, 35)
#: WECC models PSS/E ships only as user models (USRMDL) up to 35.2.
USER_MODEL_ONLY = {"REGCB1": "REGC_B", "REECD1": "REEC_D"}


class UserModelOnly(ContractError):
    """A WECC model PSS/E has only as a user model: not written."""


def check_model(model, version):
    """Refuse a model this writer cannot write for ``version``: a user-only
    model (REGC_B, REEC_D), an unpinned one, or one the version does not ship."""
    if model in USER_MODEL_ONLY:
        raise UserModelOnly(
            f"{model} ({USER_MODEL_ONLY[model]}): user model only (USRMDL) up to 35.2; not written"
        )
    if model not in MODEL_PIN:
        raise ContractError(f"no pinned .dyr layout for {model!r}; pinned {sorted(MODEL_PIN)}")
    first = MODEL_PIN[model][2]
    if version < first:
        raise ContractError(
            f"{model} is a PSS/E library model from version {first}; not in v{version} "
            "(the earlier user model is not written)"
        )


@dataclasses.dataclass(frozen=True)
class PocBranch:
    """The plant's PoC branch for its REPCA1: a pandapower ``line`` or
    ``trafo`` row, and the bus at its grid end (the PoC)."""

    table: str
    element: int
    grid_bus: int


#: Converter record layout: (model, ICON fields, CON fields). REECA1's leading
#: BUSR ICON is written by the writer, not read from the template.
IBR_RECORDS = (
    ("REGCA1", REGCA1_ICONS, REGCA1_CONS),
    ("REECA1", REECA1_ICONS, REECA1_CONS),
)
#: Type 3 wind records, after the converter pair, in this order.
WT3_RECORDS = (
    ("WTDTA1", (), WTDTA1_CONS),
    ("WTARA1", (), WTARA1_CONS),
    ("WTPTA1", (), WTPTA1_CONS),
    ("WTTQA1", WTTQA1_ICONS, WTTQA1_CONS),
)
_CONS_PER_LINE = 5


def _values(unit_id, model, names, row):
    values = {}
    for name in names:
        v = row.get(name)
        if v is None or pd.isna(v):
            raise ContractError(
                f"unit {unit_id} ({model}): {name} is missing; a zero here is "
                "a plausible wrong converter"
            )
        values[name] = float(v)
    return values


def _con(c, decimals=5):
    """A CON in ten columns, with a leading blank even when the value fills
    all ten (``-998.00000``): PSS/E reads free format, and two values written
    edge to edge would read as one. A gain-shared REPCA1 takes
    ``SHARED_DECIMALS`` (its CONs are the aggregate's times or over a share,
    which five decimals would round off the aggregate's response)."""
    text = f"{c:{decimals + 5}.{decimals}f}"
    return text if text.startswith(" ") else " " + text


#: Decimals of a gain-shared REPCA1's CONs (GC-04b).
SHARED_DECIMALS = 8


def _quoted(ckt):
    """A CKT as the RAW writes it: two characters, quoted."""
    return "'" + f"{ckt:<2s}" + "'"


def _record(num, mid, model, icons, cons, version, decimals=5):
    """One wrapped record: ICONs on the head line (ints, or a quoted CKT),
    CONs five to a line. The ICON/CON counts are checked against the pin."""
    check_model(model, version)
    n_icon, n_con = MODEL_PIN[model][:2]
    if (len(icons), len(cons)) != (n_icon, n_con):
        raise ContractError(
            f"{model}: {len(icons)} ICONs / {len(cons)} CONs, pinned {n_icon} / {n_con}"
        )
    head = f"{num:>6d} '{model}' '{mid:<2s}'" + "".join(
        f"{i:6d}" if isinstance(i, int) else f"{_quoted(i):>6s}" for i in icons
    )
    body = [
        "  " + "".join(_con(c, decimals) for c in cons[k:k + _CONS_PER_LINE])
        for k in range(0, len(cons), _CONS_PER_LINE)
    ]
    body[-1] += " /"
    return [head] + body


def _has(row, names):
    return all(n in row.index and pd.notna(row[n]) for n in names)


def _ibr_lines(unit_id, num, mid, row, version=33, repc=None, share=1.0):
    """The converter's records. ``repc`` is ``(busr, bus1, bus2, ckt)`` when
    the unit carries a REPCA1 group; ``share`` its MBASE share of a declared
    detailed plant (1.0: the aggregated machine, CONs as given)."""
    electrical = ("REECC1", REECC1_ICONS, REECC1_CONS) if _has(row, REECC1_ONLY) else IBR_RECORDS[1]
    lines = []
    for model, icon_names, con_names in (IBR_RECORDS[0], electrical):
        values = _values(unit_id, model, icon_names + con_names, row)
        icons = ([0] if model != "REGCA1" else []) + [int(values[n]) for n in icon_names]
        lines += _record(num, mid, model, icons, [values[n] for n in con_names], version)
    if _has(row, WECC_GROUPS["repca1"][:1]):
        if repc is None:
            raise ContractError(
                f"unit {unit_id}: carries a REPCA1 group but has no PoC branch; "
                "BUS1/BUS2 would be written as 0 (a controller measuring nothing)"
            )
        values = _values(unit_id, "REPCA1", REPCA1_ICONS + REPCA1_CONS, row)
        decimals = 5
        if share != 1.0:
            values, decimals = share_repca1(values, share), SHARED_DECIMALS
        icons = list(repc) + [int(values[n]) for n in REPCA1_ICONS]
        lines += _record(
            num, mid, "REPCA1", icons, [values[n] for n in REPCA1_CONS], version, decimals,
        )
    if _has(row, WECC_GROUPS["wt3"][:1]):
        for model, icon_names, con_names in WT3_RECORDS:
            values = _values(unit_id, model, icon_names + con_names, row)
            lines += _record(
                num, mid, model, [int(values[n]) for n in icon_names],
                [values[n] for n in con_names], version,
            )
    return lines


def _poc_ids(net, poc, plants=None):
    """unit_id -> (BUSR, BUS1, BUS2, CKT) from the RAW's own branch identities.
    BUS1 is the plant end, BUS2 the grid end (the PoC, also BUSR), so branch
    flow from BUS1 to BUS2 is export, positive. One controlled machine per PoC
    branch: N REPCA1s each measuring the whole plant's flow on their own MBASE
    would multiply the loop gain by N, so a plant is aggregated (GC-01) first,
    or declared in ``plants`` (name -> unit ids, GC-04b), whose members share
    one PoC branch and get gain-shared records (``share_repca1``)."""
    member = _plant_members(poc, plants)
    if not poc:
        return {}
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    lines, trafos = branch_ids(net)
    tables = {"line": (net.line, lines, "from_bus", "to_bus"), "trafo": (net.trafo, trafos, "hv_bus", "lv_bus")}
    out, seen, branch_of = {}, {}, {}
    for unit_id, b in poc.items():
        if b.table not in tables:
            raise ContractError(f"unit {unit_id}: PoC branch table {b.table!r} is not 'line' or 'trafo'")
        frame, ids, a, z = tables[b.table]
        if b.element not in frame.index:
            raise ContractError(
                f"unit {unit_id}: PoC branch {b.table} {b.element} is not in the network; "
                "REPCA1 is not written with a missing branch"
            )
        i, j, ckt = ids[frame.index.get_loc(b.element)]
        ends = {frame.at[b.element, a]: i, frame.at[b.element, z]: j}
        if b.grid_bus not in ends:
            raise ContractError(
                f"unit {unit_id}: grid bus {b.grid_bus} is not an end of {b.table} {b.element}"
            )
        bus2 = nums[name_of.at[b.grid_bus]]
        bus1 = i if bus2 == j else j
        key = (b.table, bus1, bus2, ckt)    # line and trafo CKTs count apart, as in the RAW
        plant = member.get(unit_id)
        if key in seen:
            other, other_plant = seen[key]
            if plant is None or plant != other_plant:
                raise ContractError(
                    f"units {other} and {unit_id} share PoC branch {key}: one REPCA1 per "
                    "plant attaches to the aggregated machine: export the GC-01 aggregated "
                    "model, or declare them one detailed plant (several undeclared "
                    "controllers would multiply the loop gain)"
                )
        else:
            seen[key] = (unit_id, plant)
        if plant is not None:
            first = branch_of.setdefault(plant, (unit_id, key))
            if first[1] != key:
                raise ContractError(
                    f"plant {plant!r}: units {first[0]} and {unit_id} have different PoC "
                    f"branches {first[1]} and {key}; a detailed plant's controllers measure "
                    "one branch"
                )
        out[unit_id] = (bus2, bus1, bus2, ckt)
    return out


def _plant_members(poc, plants):
    """unit_id -> plant name for every declared detailed plant; each member
    has a PoC branch and belongs to one plant only."""
    member = {}
    for name, units in (plants or {}).items():
        units = list(units)
        if not units:
            raise ContractError(f"plant {name!r} has no machines")
        for u in units:
            if u in member:
                raise ContractError(f"unit {u} is in plants {member[u]!r} and {name!r}")
            if u not in (poc or {}):
                raise ContractError(
                    f"plant {name!r}: unit {u} has no PoC branch; every machine of a "
                    "detailed plant measures the plant's PoC branch"
                )
            member[u] = name
    return member


def _shares(net, repc, plants):
    """unit_id -> MBASE_i / sum(MBASE) over its declared plant; 1.0 for a
    controlled machine outside any plant (the aggregated machine)."""
    mbase = {u: m for u, _n, _i, m, _s in _machines(net)}
    shares = {u: 1.0 for u in repc}
    for name, units in (plants or {}).items():
        missing = sorted(u for u in units if u not in mbase)
        if missing:
            raise ContractError(f"plant {name!r}: units {missing} are not machines of the RAW")
        total = sum(mbase[u] for u in units)
        for u in units:
            shares[u] = mbase[u] / total
    return shares


#: REPCA1 CONs scaled by the machine's share w (GC-04b), per path; the rest
#: (time constants, fdbd1/2, Pmax/Pmin, Qmax/Qmin, Vfrz) are unscaled.
_REPC_BY_W = ("repc_kpg", "repc_kig", "repc_kc", "repc_rc", "repc_xc")
_REPC_BY_INV_W = ("repc_ddn", "repc_dup", "repc_femax", "repc_femin")
_REPC_Q_MODE_BY_W = ("repc_kp", "repc_ki")
_REPC_Q_MODE_BY_INV_W = ("repc_dbd1", "repc_dbd2", "repc_emax", "repc_emin")


def share_repca1(values: dict, share: float) -> dict:
    """
    One machine's REPCA1 CONs for a detailed plant, so that N controllers,
    all measuring the plant's PoC branch, act as the aggregated machine's one
    controller. ``share`` is w = MBASE_i / sum(MBASE); ``values`` are the
    aggregate's CONs (pu on the aggregate's MBASE), ICONs included.

    Base: PSS/E REPCA1 works on the attached machine's MBASE. OpenIPSL's
    REPCA1, which follows PSS/E (commit a8c84de9), sets CoB = M_b/S_b
    (``BaseREPC.mo:73``) and divides pbranch, qbranch and the Rc/Xc drop by it
    (``REPCA1.mo:154-157``); WECC REMWG, "Clarification on Proper Use of REPC
    models" (Dec 2021), p.6: "All parameters, including compensation
    impedance, are on the specified model MVA base", and p.4 for Kc.

    Derivation: machine i reads the branch flow in its own pu, P/M_i = p/w
    (p in aggregate pu), so its error is e/w, and its output in its own pu must
    equal the aggregate's output in pu (then sum M_i x = sum(M) x, the
    aggregate's MW). Hence:

    - P path: Kpg, Kig x w (output = w Kpg e/w = Kpg e); Ddn, Dup, femax,
      femin x 1/w (the droop and the error limit on the error e/w, so every
      controller's droop is the same MW and the N integrators agree).
    - RefFlag = 1 (voltage control): Kp, Ki unscaled (the error is in pu
      volts, the same for every machine); dbd1/2, emax/emin unscaled.
    - RefFlag = 0 (Q control): Kp, Ki x w; dbd1/2, emax/emin x 1/w.
    - Kc, Rc, Xc x w (they multiply Q or I in pu_i, which is q/w).

    Exact when the machines start at equal pu dispatch (``unequal_dispatch``
    names a plant that does not); then the output limits bind together. No
    public source covers several REPCA1s monitoring one branch (PSS/E's
    REPC_B, for that case, has no confirmed public record layout); the
    derivation is checked against a reduced REPC_A model in
    ``tests/gridspine/repca1_reduced.py``.
    """
    if not 0.0 < share <= 1.0:
        raise ContractError(f"REPCA1 share {share!r} is not in (0, 1]")
    out = dict(values)
    q_mode = int(values["repc_refflag"]) == 0
    by_w = _REPC_BY_W + (_REPC_Q_MODE_BY_W if q_mode else ())
    by_inv_w = _REPC_BY_INV_W + (_REPC_Q_MODE_BY_INV_W if q_mode else ())
    for name in by_w:
        out[name] = values[name] * share
    for name in by_inv_w:
        out[name] = values[name] / share
    return out


#: Initial pu dispatch spread above which a detailed plant's equivalence to
#: the aggregate is no longer exact (GK A C5).
DISPATCH_TOL = 1e-6


def unequal_dispatch(net, plants) -> list:
    """
    One assumption line per declared plant whose machines start at different
    pu P or Q (on their MBASE): the gain-shared REPCA1s then match the
    aggregated model only while no output limit binds.
    """
    rows = {s["name"]: s for _, s in net.sgen.iterrows()} if hasattr(net, "sgen") else {}
    notes = []
    for name, units in (plants or {}).items():
        units = list(units)
        missing = sorted(u for u in units if u not in rows)
        if missing:
            raise ContractError(f"plant {name!r}: units {missing} are not converters of the network")
        pu = {u: (rows[u]["p_mw"] / sgen_mbase(rows[u]), rows[u]["q_mvar"] / sgen_mbase(rows[u])) for u in units}
        for k, label in ((0, "P"), (1, "Q")):
            vals = [pu[u][k] for u in units]
            if max(vals) - min(vals) > DISPATCH_TOL:
                listed = ", ".join(f"{u} {pu[u][k]:.4f}" for u in units)
                notes.append(
                    f"plant {name!r}: machines start at unequal pu {label} ({listed}); "
                    "its gain-shared REPCA1s match the aggregated model only while no "
                    "output limit binds"
                )
    return notes


def _check_base(unit_id, template_base, mbase):
    if not math.isclose(float(template_base), mbase):
        raise ContractError(
            f"unit {unit_id}: template mbase_mva {float(template_base)} does not match the "
            f"RAW MBASE {mbase}; H, reactances and converter limits are on machine "
            "base and are not rescaled"
        )


def write_dyr(
    net, unit_params: pd.DataFrame, path, ibr: pd.DataFrame = None, version=33, poc=None,
    plants=None,
) -> dict:
    """Write the .dyr; return unit_id -> bus_number for every machine written.

    ``ibr`` is ``unit_params.ibr_params(templates)`` (REGCA1 + REECA1) or
    ``wecc_params(templates)`` (also REECC1, REPCA1, type 3 wind): the
    inverters that get converter records. An inverter not in it is omitted
    (see module docstring). ``version`` is 33 or 35; ``poc`` maps a unit with
    a REPCA1 group to its ``PocBranch``; ``plants`` (name -> unit ids)
    declares detailed plants whose machines share one PoC branch, each with a
    gain-shared REPCA1 (``share_repca1``)."""
    if version not in DYR_VERSIONS:
        raise ContractError(f".dyr version {version!r} not supported; supported {DYR_VERSIONS}")
    repc = _poc_ids(net, poc, plants)
    shares = _shares(net, repc, plants)
    lines, written, controlled = [], {}, set()
    for unit_id, num, mid, mbase, synchronous in _machines(net):
        if not synchronous:
            if ibr is None or unit_id not in ibr.index:
                continue
            lines += _converter(
                unit_id, num, mid, mbase, ibr.loc[unit_id], version, repc, controlled, shares,
            )
            written[unit_id] = num
            continue
        if unit_id not in unit_params.index:
            raise ContractError(f"machine {unit_id} has no unit-parameter template row")
        row = unit_params.loc[unit_id]
        model = row.get("model")
        if model == "legacy":
            raise ContractError(
                f"unit {unit_id}: model 'legacy' carries H only and has no dynamic "
                "record; supply GENROU or GENSAL parameters"
            )
        if model not in DYR_CONS:
            raise ContractError(
                f"unit {unit_id}: no .dyr layout for model {model!r}; known {sorted(DYR_CONS)}"
            )
        _check_base(unit_id, row["mbase_mva"], mbase)
        cons = []
        for name in DYR_CONS[model]:
            v = row.get(name)
            if v is None or pd.isna(v):
                raise ContractError(
                    f"unit {unit_id} ({model}): CON {name} is missing; a zero here is "
                    "a plausible wrong machine"
                )
            cons.append(float(v))
        lines.append(
            f"{num:>6d} '{model}' '{mid:<2s}' "
            + "".join(_con(c) for c in cons)
            + " /"
        )
        written[unit_id] = num

    _check_stray(repc, controlled)
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return written


def _converter(unit_id, num, mid, mbase, row, version, repc, controlled, shares):
    """One converter's records; a unit with a REPCA1 group joins ``controlled``."""
    _check_base(unit_id, row["mbase_mva"], mbase)
    lines = _ibr_lines(unit_id, num, mid, row, version, repc.get(unit_id), shares.get(unit_id, 1.0))
    if _has(row, WECC_GROUPS["repca1"][:1]):
        controlled.add(unit_id)
    return lines


def _check_stray(repc, controlled):
    stray = sorted(set(repc) - controlled)
    if stray:
        raise ContractError(f"PoC branch given for {stray}, which write no REPCA1 record")


def converter_records(net, ibr: pd.DataFrame, version=35, poc=None, plants=None) -> tuple:
    """
    The converter records ``write_dyr`` would write, and nothing else, for a
    caller that writes its own synchronous records (the PSS/E pack appends
    these to the campus bundle's GENSAL/SEXS/DEGOV1/GENCLS records).

    Returns ``(lines, written, repc)``: the record lines in RAW machine order,
    unit_id -> bus number, and unit_id -> ``(BUSR, BUS1, BUS2, CKT)`` for each
    REPCA1. Same checks as ``write_dyr``: version, PoC branches, declared
    plants, MBASE, every field present.
    """
    if version not in DYR_VERSIONS:
        raise ContractError(f".dyr version {version!r} not supported; supported {DYR_VERSIONS}")
    repc = _poc_ids(net, poc, plants)
    shares = _shares(net, repc, plants)
    lines, written, controlled = [], {}, set()
    for unit_id, num, mid, mbase, synchronous in _machines(net):
        if synchronous or unit_id not in ibr.index:
            continue
        lines += _converter(
            unit_id, num, mid, mbase, ibr.loc[unit_id], version, repc, controlled, shares,
        )
        written[unit_id] = num
    _check_stray(repc, controlled)
    return lines, written, repc
