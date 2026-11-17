"""PSS/E RAW v33 writer — the handoff contract's network half.

Impedances on 100 MVA system base (CZ=1); bus numbers deterministic from bus
table order; canonical names in the NAME field (<=12 chars, enforced by
schema). Offline units written STAT=0, not omitted: the .dyr and the
dynamic study need the unit to exist.

Frequency: ``write_raw`` takes ``f_hz`` (default 50.0 — this pipeline's
target grids are 50 Hz European). It is used consistently for BOTH the
header's BASFRQ field and the line-charging susceptance B = 2*pi*f*C*Zbase.
Case39 is nominally a 60 Hz US system; callers exporting it as-built should
pass ``f_hz=60.0``. The default stays 50.0 because the handoff target is
the European study grid.

v33 layout decisions that differ from a naive single-line dump:
- 2-winding transformers are FOUR-line records (control words CW=CZ=CM=1:
  WINDV in p.u. of bus base voltage, R/X in p.u. on system SBASE, MAG in
  p.u. admittance on system base). Line 3 carries all 17 fields through
  CNXA1.
- Off-nominal turns ratios are NOT dropped: WINDV on the tapped side is
  (vn_winding / vn_bus) * (1 + (tap_pos - tap_neutral) * tap_step_percent
  / 100).
- (I, ID) must be unique per bus for machines/loads and (I, J, CKT) unique
  per branch pair — per-bus / per-pair counters assign IDs.

sgen (RES / IBR machine) decisions:
- Each ``net.sgen`` row is a GENERATOR record (same 20-field layout), written
  after gen/ext_grid so the shared per-bus ID counter keeps (I, ID) unique on
  buses carrying both. A bus whose only machine is an sgen classifies IDE=2.
- LEDGER ASSUMPTION: MBASE falls back to installed ``p_mw`` treated as MVA
  when ``sn_mva`` is absent — the RES ledger records capacity in MW only.
  ``sn_mva`` wins when finite (it IS the machine MVA base); a non-positive
  fallback degrades to system SBASE rather than an invalid 0 MBASE. The
  fallback means "installed" only on a net no snapshot has touched: applying
  an hour overwrites ``p_mw`` with that hour's output, so a net that will be
  snapshotted must carry ``sn_mva`` (``load_case39_res`` does).
- Q limits: an sgen is a PQ injection with no voltage-control duty. The v33
  convention for fixed-Q machines is QT == QB (== QG) — PSS/E and
  PowerFactory both hold such a machine at its Q value instead of letting it
  regulate VS. Wide-open +/-9999 limits (inc-1 gens) would wrongly hand the
  IBR voltage control on import. Explicit ``min_q_mvar``/``max_q_mvar``
  columns engage as real capability limits ONLY when BOTH are finite —
  a one-sided bound collapses to fixed-Q, since backfilling the missing
  side from QG can yield QT < QB, a malformed v33 record. Explicit limits
  are deliberately NOT multiplied by ``scaling``: they are physical
  capability, ``scaling`` is dispatch. VS is written 1.0 and is inert for a machine pinned at
  its Q limits.
- v33 wind-machine trailing optionals WMOD/WPF are OMITTED (importer
  defaults: WMOD=0 "not a wind machine", WPF=1.0). Q behaviour is already
  fully specified by QT/QB above, and WMOD=2/3 would make PowerFactory
  re-derive Q limits from WPF, fighting the explicit fields. Tech
  classification (wind/solar) travels in the unit registry, not the RAW.

RAW v35 / v36 (``version=35`` or ``36``; v33 stays the default and its output
is unchanged). The layouts follow files PSS/E 35.3, 35.6, 36.0 and 36.1 wrote
themselves, whose ``@!`` header lines carry the manual's field names (the
PSS/E Program Operation Manual itself is licensed). What differs from v33:
- line 1 carries REV 35 / 36; a SYSTEM-WIDE section (GENERAL, GAUSS, NEWTON,
  ADJUST, TYSL, SOLVER and the 12 RATING names; v36 adds the 12 SWDRATNAM
  names) sits between the titles and the buses, with PSS/E 35.6's defaults;
- generator: NREG (0, the regulated node) right after IREG, BASLOD (0) right
  after PB;
- branch: 'NAME' (the line's canonical name) at field 7, then RATE1..RATE12
  (the v33 rating in 1-3, 0 in 4-12);
- transformer: line 1 NAME up to 40 characters (the same TR_<i>_<j> name);
  line 3 RATE1-1..RATE1-12 then COD1, CONT1, NOD1 (0), RMA1, ...;
- sections: SYSTEM SWITCHING DEVICE between branch and transformer and
  SUBSTATION last (v35); v36 adds VOLTAGE DROOP CONTROL, SWITCHING DEVICE
  RATING SET, LOAD TYPE, INTERFACE and INTERFACE ELEMENT. All are written
  empty.
Trailing optional fields new in v35/v36 (load DGENP/DGENQ/DGENF/LOAD TYPE,
the v36 NAME / DROOPNAME fields, transformer ZCOD, which PSS/E itself leaves
off two-winding records) are omitted, so the importer's defaults apply.
"""
import numpy as np

from gridspine.schema.contracts import ContractError
from gridspine.static import series_caps as SC

SBASE_MVA = 100.0
VERSIONS = (33, 35, 36)

_EMPTY_TAIL = [
    "AREA", "TWO-TERMINAL DC", "VSC DC LINE", "IMPEDANCE CORRECTION",
    "MULTI-TERMINAL DC", "MULTI-SECTION LINE", "ZONE", "INTER-AREA TRANSFER",
    "OWNER", "FACTS DEVICE", "SWITCHED SHUNT", "GNE", "INDUCTION MACHINE",
]

#: Section order per RAW version; the file writes ``0 / END OF <a> DATA,
#: BEGIN <b> DATA`` between neighbours, ``0 / END OF <last> DATA`` and ``Q``.
SECTIONS = {
    33: ["BUS", "LOAD", "FIXED SHUNT", "GENERATOR", "BRANCH", "TRANSFORMER", *_EMPTY_TAIL],
    35: ["SYSTEM-WIDE", "BUS", "LOAD", "FIXED SHUNT", "GENERATOR", "BRANCH", "SYSTEM SWITCHING DEVICE",
         "TRANSFORMER", *_EMPTY_TAIL, "SUBSTATION"],
    36: ["SYSTEM-WIDE", "BUS", "LOAD", "FIXED SHUNT", "VOLTAGE DROOP CONTROL", "GENERATOR",
         "SWITCHING DEVICE RATING SET", "BRANCH", "SYSTEM SWITCHING DEVICE", "TRANSFORMER", *_EMPTY_TAIL,
         "LOAD TYPE", "INTERFACE", "INTERFACE ELEMENT", "SUBSTATION"],
}

#: The v35/v36 system-wide block, solution defaults as PSS/E 35.6 writes them.
_SYSTEM_WIDE = [
    "GENERAL, THRSHZ=0.0001, PQBRAK=0.7, BLOWUP=5.0, MaxIsolLvls=4, CAMaxReptSln=20, ChkDupCntLbl=0",
    "GAUSS, ITMX=100, ACCP=1.6, ACCQ=1.6, ACCM=1.0, TOL=0.0001",
    "NEWTON, ITMXN=20, ACCN=1.0, TOLN=0.1, VCTOLQ=0.1, VCTOLV=0.00001, DVLIM=0.99, NDVFCT=0.99",
    "ADJUST, ADJTHR=0.005, ACCTAP=1.0, TAPLIM=0.05, SWVBND=100.0, MXTPSS=99, MXSWIM=10",
    "TYSL, ITMXTY=20, ACCTY=1.0, TOLTY=0.00001",
    "SOLVER, FNSL, ACTAPS=0, AREAIN=0, PHSHFT=0, DCTAPS=0, SWSHNT=0, FLATST=0, VARLIM=99, NONDIV=0",
    *[f'RATING, {n:2d}, "{"RATE" + str(n):<6s}", "{"RATING SET " + str(n):<32s}"' for n in range(1, 13)],
]
_SWD_RATING_NAMES = [f'SWDRATNAM, {n:2d}, "{"RATE" + str(n):<6s}", 0, "MVA", "{"SWD RATING SET " + str(n):<32s}"'
                     for n in range(1, 13)]
_N_RATES = 12  # v35/v36 rating sets on branches and transformer windings


def _bus_numbers(net):
    return {net.bus.at[i, "name"]: k + 1 for k, i in enumerate(net.bus.index)}


def _tap_ratio(tr):
    """Off-nominal ratio from pandapower tap columns; 1.0 when no tap set.

    pandapower (probed via net._ppc) applies tap_pos only when
    tap_changer_type == "Ratio"; otherwise the tap is inert and the ratio
    is 1.0. The writer mirrors that. (Other changer types — e.g. "Ideal"
    phase shifters via tap_step_degree — are not modelled here.)
    """
    if "tap_changer_type" in tr.index:
        tct = tr["tap_changer_type"]
        if not (isinstance(tct, str) and tct == "Ratio"):
            return 1.0
    pos = tr.get("tap_pos", np.nan)
    neutral = tr.get("tap_neutral", np.nan)
    step = tr.get("tap_step_percent", np.nan)
    if not (np.isfinite(pos) and np.isfinite(neutral) and np.isfinite(step)):
        return 1.0
    return 1.0 + (float(pos) - float(neutral)) * float(step) / 100.0


def sgen_mbase(s) -> float:
    """An sgen row's MBASE: ``sn_mva`` when finite, else installed ``p_mw`` as
    MVA, else the system base (see the module docstring). The .dyr writer
    calls this too, so the base its records declare cannot drift from the
    one the RAW record states."""
    sn = s.get("sn_mva", np.nan)
    if sn is not None and np.isfinite(sn):
        return float(sn)
    p_inst = float(s["p_mw"])
    return p_inst if p_inst > 0 else SBASE_MVA


class _IdCounter:
    """Sequential 2-char IDs per key — (I, ID) / (I, J, CKT) uniqueness."""

    def __init__(self):
        self._seen = {}

    def next(self, key):
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        return str(n)


def trafo_impedance(net, tr):
    """``(r_pu, x_pu, windv1, windv2)`` of a two-winding transformer row as the
    RAW writes it: R/X in pu on system SBASE at the winding's bus voltage base
    (CZ=1), the windings' off-nominal ratios in pu of the bus voltage (CW=1).

    pandapower vk/vkr are on (sn_mva, vn_lv) base. Probed against net._ppc:
    with tap_side=="lv" pandapower uses the TAP-ADJUSTED lv nominal voltage as
    the impedance base (r/x scale by tap**2); with tap_side=="hv" it does not.
    """
    sn = tr["sn_mva"]
    par = int(tr.get("parallel", 1))
    bus_vn_hv = net.bus.at[tr["hv_bus"], "vn_kv"]
    bus_vn_lv = net.bus.at[tr["lv_bus"], "vn_kv"]
    tap = _tap_ratio(tr)
    vn_hv_eff, vn_lv_eff = tr["vn_hv_kv"], tr["vn_lv_kv"]
    if str(tr.get("tap_side", "hv")) == "lv":
        vn_lv_eff *= tap
    else:
        vn_hv_eff *= tap
    conv = (SBASE_MVA / sn) * (vn_lv_eff / bus_vn_lv) ** 2 / par
    r_pu = (tr["vkr_percent"] / 100.0) * conv
    z_pu = (tr["vk_percent"] / 100.0) * conv
    x_pu = np.sqrt(max(z_pu ** 2 - r_pu ** 2, 1e-12))
    return r_pu, x_pu, vn_hv_eff / bus_vn_hv, vn_lv_eff / bus_vn_lv


def branch_ids(net):
    """``(lines, trafos)``: per row, in table order, the RAW's ``(I, J, CKT)``
    for each line (from, to) and ``(I, J, CKT)`` for each transformer (hv, lv).
    CKT comes from a per-pair counter over each table, as ``write_raw`` stamps it.
    """
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    out = []
    for table, a, b in ((net.line, "from_bus", "to_bus"), (net.trafo, "hv_bus", "lv_bus")):
        ckts = _IdCounter()
        ids = []
        for _, row in table.iterrows():
            i, j = nums[name_of.at[row[a]]], nums[name_of.at[row[b]]]
            ids.append((i, j, ckts.next((min(i, j), max(i, j)))))
        out.append(ids)
    return tuple(out)


def _rates(rate):
    """RATE1..RATE12 for v35/v36: the one known rating in sets 1-3 (v33's A, B, C), 0 elsewhere."""
    return "".join(f"{rate if k < 3 else 0.0:8.2f}," for k in range(_N_RATES))


def write_raw(net, path, title="gridspine export", f_hz=50.0, version=33):
    if version not in VERSIONS:
        raise ContractError(f"RAW version {version!r} not in {VERSIONS}")
    SC.refuse(net, "PSS/E RAW writer")  # GC-01b: not written yet (follow-up)
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    slack_buses = set(net.ext_grid["bus"])
    sgen = getattr(net, "sgen", None)
    sgen_buses = set(sgen["bus"]) if sgen is not None else set()
    gen_buses = set(net.gen["bus"]) | slack_buses | sgen_buses
    lines = [
        f" 0, {SBASE_MVA:.2f}, {version}, 0, 0, {f_hz:.2f}",
        str(title)[:60],
        f"gridspine v{version} export",
    ]
    order = SECTIONS[version]
    n_done = 0

    def begin(name):
        """Close every open section before ``name`` (empty ones included); ``None`` closes all."""
        nonlocal n_done
        stop = len(order) if name is None else order.index(name)
        for k in range(n_done, stop):
            nxt = f", BEGIN {order[k + 1]} DATA" if k + 1 < len(order) else ""
            lines.append(f"0 / END OF {order[k]} DATA{nxt}")
        n_done = stop

    if version >= 35:
        lines.extend(_SYSTEM_WIDE)
        if version >= 36:
            lines.extend(_SWD_RATING_NAMES)
    begin("BUS")

    for i in net.bus.index:
        if not net.bus.at[i, "in_service"]:
            ide = 4
        else:
            ide = 3 if i in slack_buses else (2 if i in gen_buses else 1)
        lines.append(
            f"{nums[name_of.at[i]]},'{name_of.at[i][:12]:<12s}',{net.bus.at[i, 'vn_kv']:9.4f},"
            f"{ide}, 1, 1, 1,1.00000, 0.00000,1.10000,0.90000,1.10000,0.90000"
        )
    begin("LOAD")

    load_ids = _IdCounter()
    for _, ld in net.load.iterrows():
        num = nums[name_of.at[ld["bus"]]]
        stat = 1 if ld["in_service"] else 0
        scal = float(ld.get("scaling", 1.0))
        lines.append(
            f"{num},'{load_ids.next(num):<2s}',{stat}, 1, 1,"
            f"{ld['p_mw'] * scal:10.3f},{ld['q_mvar'] * scal:10.3f},"
            f" 0.000, 0.000, 0.000, 0.000, 1,1,0"
        )
    begin("GENERATOR")

    mach_ids = _IdCounter()
    # v35: NREG right after IREG, BASLOD right after PB (both 0)
    nreg, baslod = (" 0,", " 0,") if version >= 35 else ("", "")

    def gen_record(bus_idx, p_mw, vm_pu, mbase, stat,
                   qg=0.0, qt=9999.0, qb=-9999.0):
        num = nums[name_of.at[bus_idx]]
        return (
            f"{num},'{mach_ids.next(num):<2s}',{p_mw:10.3f},{qg:8.3f},"
            f"{qt:8.3f},{qb:9.3f},"
            f"{vm_pu:7.5f}, 0,{nreg}{mbase:9.3f}, 0.00000,0.15000, 0.00000, 0.00000,1.00000,"
            f"{stat},100.0,{mbase:9.3f},   0.000,{baslod} 1,1.0000"
        )

    for _, g in net.gen.iterrows():
        sn = g.get("sn_mva", np.nan)
        mbase = float(sn) if np.isfinite(sn) else SBASE_MVA
        scal = float(g.get("scaling", 1.0))
        lines.append(gen_record(g["bus"], g["p_mw"] * scal, g.get("vm_pu", 1.0),
                                mbase, 1 if g["in_service"] else 0))
    for _, e in net.ext_grid.iterrows():
        lines.append(gen_record(e["bus"], 0.0, e.get("vm_pu", 1.0), SBASE_MVA,
                                1 if e["in_service"] else 0))
    if sgen is not None:
        for _, s in sgen.iterrows():
            scal = float(s.get("scaling", 1.0))
            p_inst = float(s["p_mw"])
            mbase = sgen_mbase(s)
            qg = float(s.get("q_mvar", 0.0)) * scal
            qt_raw = s.get("max_q_mvar", np.nan)
            qb_raw = s.get("min_q_mvar", np.nan)
            if np.isfinite(qt_raw) and np.isfinite(qb_raw):
                qt, qb = float(qt_raw), float(qb_raw)
            else:
                # A lone bound is not a capability box; backfilling the
                # missing side from QG can invert the pair (QT < QB —
                # malformed v33 record). Collapse to fixed-Q instead.
                qt = qb = qg
            lines.append(gen_record(s["bus"], p_inst * scal, 1.0, mbase,
                                    1 if s["in_service"] else 0,
                                    qg=qg, qt=qt, qb=qb))
    begin("BRANCH")

    branch_ckts = _IdCounter()
    for _, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        z_base = vn ** 2 / SBASE_MVA
        r_pu = ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        x_pu = ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        b_pu = (2 * np.pi * f_hz * ln["c_nf_per_km"] * 1e-9 * ln["length_km"]
                * ln["parallel"]) * z_base
        rate = np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"]
        i, j = nums[name_of.at[ln["from_bus"]]], nums[name_of.at[ln["to_bus"]]]
        ckt = branch_ckts.next((min(i, j), max(i, j)))
        if version >= 35:
            # 'NAME' at field 7, then RATE1..RATE12
            name = ln.get("name")
            name = "" if name is None or name != name else str(name).replace("'", "")[:40]
            ratings = f"'{name:<40s}'," + _rates(rate)
        else:
            ratings = f"{rate:8.2f},{rate:8.2f},{rate:8.2f},"
        lines.append(
            f"{i},{j},'{ckt:<2s}',"
            f"{r_pu:10.5f},{x_pu:10.5f},{b_pu:10.5f},{ratings}"
            f" 0.000, 0.000, 0.000, 0.000,{1 if ln['in_service'] else 0},1, 0.00, 1,1.0000"
        )
    begin("TRANSFORMER")

    trafo_ckts = _IdCounter()
    for _, tr in net.trafo.iterrows():
        sn = tr["sn_mva"]
        par = int(tr.get("parallel", 1))
        hv_bus, lv_bus = tr["hv_bus"], tr["lv_bus"]
        r_pu, x_pu, windv1, windv2 = trafo_impedance(net, tr)
        shift = float(tr.get("shift_degree", 0.0) or 0.0)
        hv, lv = nums[name_of.at[hv_bus]], nums[name_of.at[lv_bus]]
        ckt = trafo_ckts.next((min(hv, lv), max(hv, lv)))
        stat = 1 if tr["in_service"] else 0
        rate = sn * par
        xname = f"TR_{hv}_{lv}"[:12]
        # MAG1/MAG2 = 0: deliberate case39-scope simplification — case39 has
        # pfe_kw == i0_percent == 0 everywhere, so zero is exact, not lossy.
        lines.append(
            f"{hv},{lv},0,'{ckt:<2s}',1,1,1, 0.00000, 0.00000,2,'{xname:<12s}',{stat},1,1.0000"
        )
        lines.append(f"{r_pu:10.5f},{x_pu:10.5f},{sn:8.2f}")
        if version >= 35:
            # RATE1-1..RATE1-12, COD1, CONT1, NOD1 (0)
            ratings = _rates(rate) + "0, 0, 0,"
        else:
            ratings = f"{rate:8.2f},{rate:8.2f},{rate:8.2f},0, 0,"
        lines.append(
            f"{windv1:7.5f},{tr['vn_hv_kv']:9.4f},{shift:7.3f},"
            f"{ratings}"
            f"1.10000,0.90000,1.10000,0.90000,33,0, 0.00000, 0.00000, 0.000"
        )
        lines.append(f"{windv2:7.5f},{tr['vn_lv_kv']:9.4f}")
    begin(None)
    lines.append("Q")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return nums
