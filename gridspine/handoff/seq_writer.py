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
each winding connection gives). A v36 RAW takes a v35 .seq. v35 opens with
a SYSTEM-WIDE section (RPTFORMAT, MOV, SCMODEL at PSS/E 35.0's defaults) and adds REFDEG to the machine record; v33 has neither.

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
  neutral resistance (``r_n_<side>_ohm``, 0 when solid): the physical R_N,
  not 3 R_N, since PSS/E's zero-sequence equivalent circuits apply the 3 Z_G
  themselves (as a public utility transformer-modelling guide draws them and
  fills RG1 / RG2 with the neutral impedance). The connection code CC per
  vector group is ``CONNECTION_CODES`` (v33: 1-4, v35: 11-14), from
  ``Campus.windings`` (the island_sc_seq default Dyn11, LV solidly earthed,
  when a transformer has none); windings outside the schema are refused:

  =================================  ===  ===  ==============================
  group                              v33  v35  zero-sequence path
  =================================  ===  ===  ==============================
  YNyn                               1    11   series, earthed both sides
  YNd                                2    12   earth path on winding 1 (HV)
  Dyn                                3    13   earth path on winding 2 (LV)
  YNy, Yyn, Yy, Yd, Dy, Dd           4    14   none (as island_sc_seq)
  =================================  ===  ===  ==============================

Not written: loads (IEC 60909 leaves them out; PSS/E's defaults apply), the
campus's earthing transformers (a zero-sequence fixed shunt needs a RAW fixed
shunt, which the RAW does not carry yet; each is named in the header line and
the return value as ``NOT_WRITTEN``), mutual couplings and the
non-conventional-source tables (empty sections). ``write_seq`` returns the
assumptions it applied, one line each, for the pack's README.
"""

import math

from gridspine.handoff.dyr_writer import machine_records
from gridspine.handoff.raw_writer import SBASE_MVA, branch_ids, trafo_impedance
from gridspine.schema.contracts import ContractError
from gridspine.static import series_caps as SC
from gridspine.static.island_sc import _param
from gridspine.static.island_sc_seq import (
    DEFAULT_WINDINGS,
    GENSET_X0_RATIO,
    R_SCREEN_OHM_PER_KM,
    _p,
    genset_seq,
    grid_zero_seq,
    line_zero_seq,
    trafo_neutral_ohm,
)

VERSIONS = (33, 35)
#: How an element the .seq cannot carry yet is listed (header line and return value).
NOT_WRITTEN = "not written: zero-sequence source omitted"
CASES = ("max", "min")
#: An open circuit in a field that has no "absent" value: 9999 pu (generic engineering, assumed).
OPEN_PU = 9999.0

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


#: A winding's letter from its ``Campus.windings`` entry: ``(winding, earth) -> letter``.
_LETTER = {
    ("D", None): "D",
    ("Y", "isolated"): "Y",
    ("Y", "solid"): "YN",
    ("Y", "resistance"): "YN",
}

#: The PSS/E zero-sequence connection code of each vector group the campus
#: schema allows (HV D/Y/YN, LV d/y/yn; no zig-zag), winding 1 = HV. The earth
#: paths are island_sc_seq's: YNyn a series path, YNd an earth path on winding
#: 1, Dyn on winding 2, every other group none. v33 takes PSS/E 30-33's codes
#: 1-4; v35 the impedance-earthed codes 11-14 (the same paths, with CZ0 / CZG
#: and RNUTRL honoured). Code meanings: PSS/E's zero-sequence transformer
#: code table as a public utility transformer-modelling guide reproduces it.
CONNECTION_CODES = {
    33: {
        "YNyn": 1,
        "YNd": 2,
        "Dyn": 3,
        "YNy": 4,
        "Yyn": 4,
        "Yy": 4,
        "Yd": 4,
        "Dy": 4,
        "Dd": 4,
    },
    35: {
        "YNyn": 11,
        "YNd": 12,
        "Dyn": 13,
        "YNy": 14,
        "Yyn": 14,
        "Yy": 14,
        "Yd": 14,
        "Dy": 14,
        "Dd": 14,
    },
}


def vector_group(windings: dict) -> str:
    """
    The vector group without its clock number (``Dyn``, ``YNd`` ...) of a
    ``Campus.windings`` entry; ContractError for a winding outside the schema.
    """
    try:
        hv = _LETTER[(windings["hv"], windings["hv_earth"])]
        lv = _LETTER[(windings["lv"], windings["lv_earth"])].lower()
    except (KeyError, TypeError) as err:
        raise ContractError(
            f"seq: windings {windings!r} are not a vector group the campus schema allows"
        ) from err
    return hv + lv


def connection_code(windings: dict, version: int = 35) -> int:
    """
    The PSS/E zero-sequence connection code of a two-winding transformer
    with winding 1 = HV (``CONNECTION_CODES``); ContractError, from
    ``vector_group``, for windings outside the schema.
    """
    return CONNECTION_CODES[version][vector_group(windings)]


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
        z0, drafted = grid_zero_seq(campus, pcc, case, x1, rx)
        if drafted:
            assumed.append(
                f"seq: grid {uid}: no x0x_{case}; X0 = X1, R0/X0 = R/X (assumed)"
            )
        elif _p(campus, pcc, f"r0x0_{case}") is None:
            assumed.append(f"seq: grid {uid}: no r0x0_{case}; R0/X0 = R/X (assumed)")
        z1 = complex(rx * x1, x1)
        return z1, z1, z0, 0j
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
        x2, x0, _, rn = genset_seq(block, xd)
        assumed.append(
            f"seq: genset {uid}: X' = Xs = x''d (assumed: the island data carry x''d only)"
        )
        if not block:
            assumed.append(
                f"seq: genset {uid}: no island data; neutral unearthed (assumed)"
            )
        if rn is None:
            assumed.append(
                f"seq: genset {uid}: unearthed neutral written as ZRG = OPEN_PU = {OPEN_PU:g} pu (assumed)"
            )
        zg = complex(OPEN_PU if rn is None else rn / (bus_kv**2 / mbase), 0.0)
        return complex(rx * xd, xd), complex(rx * x2, x2), complex(rx * x0, x0), zg
    k2 = vals.get("neg_seq", 0.0)
    assumed.append(
        f"seq: converter {uid}: positive and zero sequence written as OPEN_PU = {OPEN_PU:g} pu"
        + ("" if k2 > 0 else ", negative sequence too (no neg_seq)")
        + " (assumed)"
    )
    z2 = complex(0.0, 1.0 / k2) if k2 > 0 else complex(0, OPEN_PU)
    return complex(0, OPEN_PU), z2, complex(0, OPEN_PU), 0j


def write_seq(campus, path, version=35, case="max", f_hz=50.0) -> list:
    """
    Write the .seq for ``campus.net`` (as ``write_raw`` numbers it, with the
    same ``f_hz`` for the zero-sequence charging); return the assumptions applied.
    """
    # GC-01b: a series capacitor is not written yet (follow-up)
    SC.refuse(campus.net, "PSS/E sequence writer")
    if version not in VERSIONS:
        raise ContractError(f"SEQ version {version!r} not in {VERSIONS}")
    if case not in CASES:
        raise ContractError(f"SEQ case {case!r} not in {CASES}")
    net = campus.net
    assumed = []
    sec = {name: [] for name in SECTIONS[version]}
    if version >= 35:
        sec["SYSTEM-WIDE"] = list(_SYSTEM_WIDE)

    kv_of_num = {
        k + 1: float(net.bus.at[i, "vn_kv"]) for k, i in enumerate(net.bus.index)
    }
    for uid, num, mid, mbase, _sync in machine_records(net):
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
        r0, x0, c0, drafted = line_zero_seq(
            campus,
            name,
            float(ln["r_ohm_per_km"]),
            float(ln["x_ohm_per_km"]),
            float(ln["c_nf_per_km"]),
        )
        if drafted:
            assumed.append(
                f"seq: line {name}: zero sequence drafted (R0 = R + 3 x {R_SCREEN_OHM_PER_KM} ohm/km, "
                "X0 = X, C0 = C; assumed)"
            )
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
            w = DEFAULT_WINDINGS
        assumed.append(f"seq: transformer {name}: vk0 = vk (assumed)")
        try:
            cc = connection_code(w, version)
        except ContractError as err:
            raise ContractError(f"seq: transformer {name}: {err}") from err
        r_pu, x_pu, _, _ = trafo_impedance(net, tr)
        rg = {}
        for side, bus in (("hv", tr["hv_bus"]), ("lv", tr["lv_bus"])):
            rn = trafo_neutral_ohm(campus, name, w, side)
            rg[side] = (rn or 0.0) / (float(net.bus.at[bus, "vn_kv"]) ** 2 / SBASE_MVA)
        if version == 33 and any(rg.values()):
            assumed.append(
                f"seq: transformer {name}: v33 RG1 / RG2 written under CC {cc} as PSS/E 30-33's "
                "grounding impedance fields (assumed)"
            )
        sec["ZERO SEQ. TRANSFORMER"].append(
            ",".join(
                [
                    str(i),
                    str(j),
                    "0",
                    f"'{ckt:<2s}'",
                    "1",
                    "1",
                    str(cc),
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

    omitted = [str(n) for n in campus.earthing_transformers.index]
    for n in omitted:
        assumed.append(f"seq: earthing transformer {n}: {NOT_WRITTEN}")
    note = f"; {NOT_WRITTEN}: {', '.join(omitted)}" if omitted else ""
    lines = (["@!IC, REV"] if version >= 35 else []) + [
        f"0, {version}   / gridspine seq export{note}"
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
