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
  written as 0. ONE controlled machine per PoC branch: the plant controller
  attaches to the aggregated machine (GC-01), because N REPCA1 records each
  measuring the whole plant's flow on their own MBASE would multiply the
  loop gain by N; two machines on one PoC branch are refused.
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
    return head + " " + "".join(f"{float(values[n]):10.5f}" for n in con_names) + " /"


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


def _con(c):
    """A CON in ten columns, with a leading blank even when the value fills
    all ten (``-998.00000``): PSS/E reads free format, and two values written
    edge to edge would read as one."""
    text = f"{c:10.5f}"
    return text if text.startswith(" ") else " " + text


def _quoted(ckt):
    """A CKT as the RAW writes it: two characters, quoted."""
    return "'" + f"{ckt:<2s}" + "'"


def _record(num, mid, model, icons, cons, version):
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
        "  " + "".join(_con(c) for c in cons[k:k + _CONS_PER_LINE])
        for k in range(0, len(cons), _CONS_PER_LINE)
    ]
    body[-1] += " /"
    return [head] + body


def _has(row, names):
    return all(n in row.index and pd.notna(row[n]) for n in names)


def _ibr_lines(unit_id, num, mid, row, version=33, repc=None):
    """The converter's records. ``repc`` is ``(busr, bus1, bus2, ckt)`` when
    the unit carries a REPCA1 group."""
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
        icons = list(repc) + [int(values[n]) for n in REPCA1_ICONS]
        lines += _record(num, mid, "REPCA1", icons, [values[n] for n in REPCA1_CONS], version)
    if _has(row, WECC_GROUPS["wt3"][:1]):
        for model, icon_names, con_names in WT3_RECORDS:
            values = _values(unit_id, model, icon_names + con_names, row)
            lines += _record(
                num, mid, model, [int(values[n]) for n in icon_names],
                [values[n] for n in con_names], version,
            )
    return lines


def _poc_ids(net, poc):
    """unit_id -> (BUSR, BUS1, BUS2, CKT) from the RAW's own branch identities.
    BUS1 is the plant end, BUS2 the grid end (the PoC, also BUSR), so branch
    flow from BUS1 to BUS2 is export, positive. One controlled machine per PoC
    branch: N REPCA1s each measuring the whole plant's flow on their own MBASE
    would multiply the loop gain by N, so a plant is aggregated (GC-01) first."""
    if not poc:
        return {}
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    lines, trafos = branch_ids(net)
    tables = {"line": (net.line, lines, "from_bus", "to_bus"), "trafo": (net.trafo, trafos, "hv_bus", "lv_bus")}
    out, seen = {}, {}
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
        key = (bus1, bus2, ckt)
        if key in seen:
            raise ContractError(
                f"units {seen[key]} and {unit_id} share PoC branch {key}: one REPCA1 per "
                "plant attaches to the aggregated machine (GC-01); several would multiply its gain"
            )
        seen[key] = unit_id
        out[unit_id] = (bus2, bus1, bus2, ckt)
    return out


def _check_base(unit_id, template_base, mbase):
    if not math.isclose(float(template_base), mbase):
        raise ContractError(
            f"unit {unit_id}: template mbase_mva {float(template_base)} does not match the "
            f"RAW MBASE {mbase}; H, reactances and converter limits are on machine "
            "base and are not rescaled"
        )


def write_dyr(
    net, unit_params: pd.DataFrame, path, ibr: pd.DataFrame = None, version=33, poc=None,
) -> dict:
    """Write the .dyr; return unit_id -> bus_number for every machine written.

    ``ibr`` is ``unit_params.ibr_params(templates)`` (REGCA1 + REECA1) or
    ``wecc_params(templates)`` (also REECC1, REPCA1, type 3 wind): the
    inverters that get converter records. An inverter not in it is omitted
    (see module docstring). ``version`` is 33 or 35; ``poc`` maps a unit with
    a REPCA1 group to its ``PocBranch``."""
    if version not in DYR_VERSIONS:
        raise ContractError(f".dyr version {version!r} not supported; supported {DYR_VERSIONS}")
    repc = _poc_ids(net, poc)
    lines, written, controlled = [], {}, set()
    for unit_id, num, mid, mbase, synchronous in _machines(net):
        if not synchronous:
            if ibr is None or unit_id not in ibr.index:
                continue
            row = ibr.loc[unit_id]
            _check_base(unit_id, row["mbase_mva"], mbase)
            lines += _ibr_lines(unit_id, num, mid, row, version, repc.get(unit_id))
            if _has(row, WECC_GROUPS["repca1"][:1]):
                controlled.add(unit_id)
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
            + "".join(f"{c:10.5f}" for c in cons)
            + " /"
        )
        written[unit_id] = num

    stray = sorted(set(repc) - controlled)
    if stray:
        raise ContractError(f"PoC branch given for {stray}, which write no REPCA1 record")
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return written
