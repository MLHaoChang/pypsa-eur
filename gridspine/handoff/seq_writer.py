"""
PSS/E sequence data (.seq) for the campus RAW: the unbalanced-fault half.

Written beside ``write_raw(campus.net, ...)`` and keyed on the same identities:
machines by the RAW's ``(I, ID)`` (``dyr_writer._machines``), branches and
transformers by the RAW's ``(I, J, CKT)`` (``raw_writer.branch_ids``). The
values are the ones ``static/island_sc_seq.py`` builds its sequence networks
from, read the same way and with the same drafting rules, but WITHOUT the
IEC 60909 voltage factor c or the K_G / K_T / K_S corrections: PSS/E applies
those itself when it runs an IEC 60909 calculation.

Layout (``version=35``, the default, or ``33``). The record layouts follow
.seq files PSS/E 34.0 and 35.0 wrote themselves (their ``@!`` header lines
carry the manual's field names); the transformer connection codes are
PSS/E's documented zero-sequence codes (generic engineering: the earth path
each winding connection gives). A v36 RAW takes a v35 .seq. v35 opens with a SYSTEM-WIDE section (RPTFORMAT, MOV, SCMODEL at
PSS/E 35.0's defaults) and adds REFDEG to the machine record; v33 has neither.

What each element is:

* machine ``I,'ID',ZRPOS,ZXPPDV,ZXPDV,ZXSDV,ZRNEG,ZXNEGDV,ZR0,ZX0DV,CZG,ZRG,ZXG[,REFDEG]``,
  pu on the machine's MBASE (CZG=1: the neutral impedance ZRG+jZXG is pu on
  MBASE too; PSS/E adds 3 Z_G in the zero sequence):

  - the grid (ext_grid, MBASE = SBASE): Z1 = Z2 = SBASE / Sk at the case's
    R/X (``case='max'`` or ``'min'``); Z0 from the PCC's ``x0x_<case>`` and
    ``r0x0_<case>``, else X0 = X1 and R0/X0 = R/X (assumed); solidly earthed;
  - a genset: Z1 = rx·x''d + j x''d, written for the subtransient, transient
    and synchronous reactance alike (assumed: the island data carry x''d
    only); Z2 from ``x2_pu`` (else x''d), Z0 from ``x0_pu`` (else 0.4 x''d);
    neutral by its island ``neutral_earthing``: solid ZRG = 0, resistance
    ZRG = ``r_n_ohm`` on MBASE, isolated (or no island data) ZRG = OPEN_PU;
  - a converter (every other unit, and any sgen that is not a unit): positive
    and zero sequence OPEN_PU (its fault current is a controlled source, not
    an impedance); negative sequence j / ``neg_seq`` on its rating when the
    island data give ``neg_seq`` > 0, else OPEN_PU.

* zero-sequence line ``I,J,'ICKT',RLINZ,XLINZ,BCHZ,GI,BI,GJ,BJ,IPR,SCTYP``, pu on
  SBASE at the from-bus voltage: ``r0_ohm_per_km`` / ``x0_ohm_per_km`` /
  ``c0_nf_per_km``, else R0 = R + 3 x 0.727 ohm/km, X0 = X, C0 = C (the
  island_sc_seq draft, assumed).

* zero-sequence transformer ``I,J,K,'ICKT',CZ0,CZG,CC,RG1,XG1,R01,X01,RG2,XG2,R02,X02,RNUTRL,XNUTRL``,
  winding 1 = HV (as the RAW writes I = hv bus). CZ0 = CZG = 1: pu on SBASE at
  the winding's bus voltage. R01 + j X01 = the transformer's Zk, as the RAW
  converts it (vk0 = vk, assumed, as island_sc_seq); RG1 / RG2 = each star's
  neutral resistance (``r_n_<side>_ohm``, 0 when solid). The connection code
  from ``Campus.windings`` (the island_sc_seq default Dyn11, LV solidly
  earthed, when a transformer has none), impedance-earthed codes 11-14:

  ======  ==========================================  =====
  HV      LV                                          CC
  ======  ==========================================  =====
  YN      yn                                          11  series path, earthed both sides
  YN      d                                           12  earth path on winding 1 (HV)
  D       yn                                          13  earth path on winding 2 (LV)
  other   other (unearthed star, D-d, Y-y ...)        14  no zero-sequence path
  ======  ==========================================  =====

Not written: loads (IEC 60909 leaves them out; PSS/E's defaults apply), the
campus's earthing transformers (a zero-sequence fixed shunt needs a RAW fixed
shunt, which the RAW does not carry yet), mutual couplings and the
non-conventional-source tables (empty sections). ``write_seq`` returns the
assumptions it applied, one line each, for the pack's README.
"""

import math

from gridspine.handoff.dyr_writer import _machines
from gridspine.handoff.raw_writer import SBASE_MVA, branch_ids, trafo_impedance
from gridspine.schema.contracts import ContractError
from gridspine.static.island_sc import _param
from gridspine.static.island_sc_seq import GENSET_X0_RATIO, R_SCREEN_OHM_PER_KM, _p

VERSIONS = (33, 35)
CASES = ("max", "min")
#: An open circuit in a field that has no "absent" value: 9999 pu (generic engineering, assumed).
OPEN_PU = 9999.0
_DEFAULT_WINDINGS = {"hv": "D", "hv_earth": None, "lv": "Y", "lv_earth": "solid"}
_EARTHED = ("solid", "resistance")

SECTIONS = {
    33: [
        "GENERATOR",
        "LOAD",
        "ZERO SEQ. NON-TRANSFORMER BRANCH",
        "ZERO SEQ. MUTUAL",
        "ZERO SEQ. TRANSFORMER",
        "ZERO SEQ. SWITCHED SHUNT",
        "ZERO SEQ. FIXED SHUNT",
        "INDUCTION MACHINE",
    ],
    35: [
        "SYSTEM-WIDE",
        "GENERATOR",
        "LOAD",
        "ZERO SEQ. NON-TRANSFORMER BRANCH",
        "ZERO SEQ. MUTUAL",
        "ZERO SEQ. TRANSFORMER",
        "ZERO SEQ. SWITCHED SHUNT",
        "ZERO SEQ. FIXED SHUNT",
        "INDUCTION MACHINE",
        "NON-CONVENTIONAL SOURCE TABLE",
        "NON-CONVENTIONAL SOURCE MACHINE",
    ],
}
_SYSTEM_WIDE = [
    "RPTFORMAT, AMPOUT=0, POLROU=0, AMPOUTZ=0, POLROUZ=0",
    "MOV, ITERATIONS=20, TOLERANCE=0.01, MOVALPHA=0.3",
    "SCMODEL, SCNRML=1",
]


def connection_code(windings: dict) -> int:
    """
    The PSS/E zero-sequence connection code (11-14) of a two-winding
    transformer with winding 1 = HV, from its ``Campus.windings`` entry.
    """
    hv = windings["hv"] == "Y" and windings["hv_earth"] in _EARTHED
    lv = windings["lv"] == "Y" and windings["lv_earth"] in _EARTHED
    if hv and lv:
        return 11
    if hv and windings["lv"] == "D":
        return 12
    if lv and windings["hv"] == "D":
        return 13
    return 14


def _f(v) -> str:
    return f"{v:.6g}"


def _machine_record(campus, uid, bus_kv, mbase, case, assumed):
    """``(z1, z2, z0, zg)`` complex, pu on MBASE, for one RAW machine."""
    net = campus.net
    if uid in set(net.ext_grid["name"]):
        e = net.ext_grid[net.ext_grid["name"] == uid].iloc[0]
        rx = float(e[f"rx_{case}"])
        x1 = SBASE_MVA / float(e[f"s_sc_{case}_mva"]) / math.sqrt(1.0 + rx**2)
        pcc = str(net.bus.at[e["bus"], "name"])
        x0x, r0x0 = _p(campus, pcc, f"x0x_{case}"), _p(campus, pcc, f"r0x0_{case}")
        if x0x is None:
            assumed.append(
                f"seq: grid {uid}: no x0x_{case}; X0 = X1, R0/X0 = R/X (assumed)"
            )
        x0 = (1.0 if x0x is None else x0x) * x1
        z1 = complex(rx * x1, x1)
        return z1, z1, complex((rx if r0x0 is None else r0x0) * x0, x0), 0j
    if uid not in campus.units.index:
        if uid in set(net.gen["name"]):
            raise ContractError(
                f"seq: machine {uid} is a synchronous gen with no campus sequence data"
            )
        assumed.append(
            f"seq: {uid} is not a campus unit; written as a converter with no sequence path (assumed)"
        )
        return complex(0, OPEN_PU), complex(0, OPEN_PU), complex(0, OPEN_PU), 0j
    block = campus.island["units"].get(uid)
    vals = block["values"] if block else {}
    if campus.units.at[uid, "kind"] == "genset":
        xd, rx = _param(campus, uid, "xd_pp"), _param(campus, uid, "rx_sc")
        if "x2_pu" not in vals or "x0_pu" not in vals:
            assumed.append(
                f"seq: genset {uid}: x2 = x''d, x0 = {GENSET_X0_RATIO} x''d where not given (assumed)"
            )
        x2 = vals.get("x2_pu", xd)
        x0 = vals.get("x0_pu", GENSET_X0_RATIO * xd)
        if not block:
            assumed.append(
                f"seq: genset {uid}: no island data; neutral unearthed (assumed)"
            )
        earthing = block["neutral_earthing"] if block else "isolated"
        if earthing == "isolated":
            zg = complex(OPEN_PU, 0.0)
        else:
            rn = vals["r_n_ohm"] if earthing == "resistance" else 0.0
            zg = complex(rn / (bus_kv**2 / mbase), 0.0)
        return complex(rx * xd, xd), complex(rx * x2, x2), complex(rx * x0, x0), zg
    k2 = vals.get("neg_seq", 0.0)
    z2 = complex(0.0, 1.0 / k2) if k2 > 0 else complex(0, OPEN_PU)
    return complex(0, OPEN_PU), z2, complex(0, OPEN_PU), 0j


def write_seq(campus, path, version=35, case="max") -> list:
    """Write the .seq for ``campus.net`` (as ``write_raw`` numbers it); return the assumptions applied."""
    if version not in VERSIONS:
        raise ContractError(f"SEQ version {version!r} not in {VERSIONS}")
    if case not in CASES:
        raise ContractError(f"SEQ case {case!r} not in {CASES}")
    net = campus.net
    f_hz = float(getattr(net, "f_hz", 50.0) or 50.0)
    assumed = []
    sec = {name: [] for name in SECTIONS[version]}
    if version >= 35:
        sec["SYSTEM-WIDE"] = list(_SYSTEM_WIDE)

    kv_of_num = {
        k + 1: float(net.bus.at[i, "vn_kv"]) for k, i in enumerate(net.bus.index)
    }
    for uid, num, mid, mbase, _sync in _machines(net):
        z1, z2, z0, zg = _machine_record(
            campus, uid, kv_of_num[num], mbase, case, assumed
        )
        fields = [
            num,
            f"'{mid:<2s}'",
            z1.real,
            z1.imag,
            z1.imag,
            z1.imag,
            z2.real,
            z2.imag,
            z0.real,
            z0.imag,
            1,
            zg.real,
            zg.imag,
        ] + ([0.0] if version >= 35 else [])
        sec["GENERATOR"].append(
            ",".join(x if isinstance(x, str) else _f(x) for x in fields)
        )

    line_ids, trafo_ids = branch_ids(net)
    for (i, j, ckt), (_, ln) in zip(line_ids, net.line.iterrows()):
        name = str(ln["name"])
        length, par = float(ln["length_km"]), float(ln["parallel"])
        r0, x0, c0 = (
            _p(campus, name, f)
            for f in ("r0_ohm_per_km", "x0_ohm_per_km", "c0_nf_per_km")
        )
        if r0 is None or x0 is None or c0 is None:
            assumed.append(
                f"seq: line {name}: zero sequence drafted (R0 = R + 3 x {R_SCREEN_OHM_PER_KM} ohm/km, "
                "X0 = X, C0 = C; assumed)"
            )
        r0 = float(ln["r_ohm_per_km"]) + 3.0 * R_SCREEN_OHM_PER_KM if r0 is None else r0
        x0 = float(ln["x_ohm_per_km"]) if x0 is None else x0
        c0 = float(ln["c_nf_per_km"]) if c0 is None else c0
        zb = float(net.bus.at[ln["from_bus"], "vn_kv"]) ** 2 / SBASE_MVA
        b0 = 2.0 * math.pi * f_hz * c0 * 1e-9 * length * par * zb
        sec["ZERO SEQ. NON-TRANSFORMER BRANCH"].append(
            ",".join(
                [
                    str(i),
                    str(j),
                    f"'{ckt:<2s}'",
                    _f(r0 * length / par / zb),
                    _f(x0 * length / par / zb),
                    _f(b0),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                ]
            )
        )

    for (i, j, ckt), (_, tr) in zip(trafo_ids, net.trafo.iterrows()):
        name = str(tr["name"])
        w = campus.windings.get(name)
        if w is None:
            assumed.append(
                f"seq: transformer {name}: no vector group; Dyn11 with the LV star solidly earthed (assumed)"
            )
            w = _DEFAULT_WINDINGS
        r_pu, x_pu, _, _ = trafo_impedance(net, tr)
        rg = {}
        for side, bus in (("hv", tr["hv_bus"]), ("lv", tr["lv_bus"])):
            rn = (
                _p(campus, name, f"r_n_{side}_ohm")
                if w[f"{side}_earth"] == "resistance"
                else 0.0
            )
            rg[side] = (rn or 0.0) / (float(net.bus.at[bus, "vn_kv"]) ** 2 / SBASE_MVA)
        sec["ZERO SEQ. TRANSFORMER"].append(
            ",".join(
                [
                    str(i),
                    str(j),
                    "0",
                    f"'{ckt:<2s}'",
                    "1",
                    "1",
                    str(connection_code(w)),
                    _f(rg["hv"]),
                    "0",
                    _f(r_pu),
                    _f(x_pu),
                    _f(rg["lv"]),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                ]
            )
        )

    lines = (["@!IC, REV"] if version >= 35 else []) + [
        f"0, {version}   / gridspine seq export"
    ]
    order = SECTIONS[version]
    for k, name in enumerate(order):
        lines.extend(sec[name])
        nxt = f", BEGIN {order[k + 1]} DATA" if k + 1 < len(order) else ""
        lines.append(f"0 / END OF {name} DATA{nxt}")
    lines.append("Q")
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return sorted(set(assumed))
