"""
psspy run scripts for the test battery: one script per case (GC-04 item 6).

Each ``run_<case_id>.py`` loads the pack's RAW, ``.seq`` and ``.dyr``, turns
the grid source machine (a GENCLS with H = 0, an infinite bus) into the case's
Thevenin source, applies the case's event at ``t_event_s`` and writes
``<case_id>.out`` plus ``<case_id>.csv`` next to itself. Every output channel
carries the contract name from ``test_battery.CHANNELS`` as its IDENT, so a
result maps back to a case with no translation table (TRGC 04/05).

**Thevenin source.** ``thevenin_impedances_ohm`` gives Z1 (and Z0) in ohms at
the PoC base kV; the generator converts them to pu on the grid machine's MBASE
(``Z_pu = Z_ohm * MBASE / kV**2``) and writes the numbers as literals. The
script refuses to run when the RAW machine's MBASE differs from the one the
numbers were made on. Z2 = Z1 (test_battery module docstring).

**Pack-local files.** A script names files only by basename and resolves each
against its own directory; :func:`check_script` (also used by the pack) parses
a script with ``ast`` and refuses any other file, an absolute path, an import
outside psspy/dyntools/os/sys, ``open``/``eval``/``exec``/``__import__``, and
any ``os`` call outside ``os.path`` (GK condition S1).

The psspy calls were written without a PSS/E licence: :data:`PENDING` is
repeated in every script, and :data:`EVENT_CALLS` / :data:`CHANNEL_CALLS`
state per kind and channel which calls are used and what is unverified.
"""

import ast
import math
import os
import re

from gridspine.handoff.test_battery import (
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
)
from gridspine.schema.contracts import ContractError

PENDING = (
    "psspy calls generated without a PSS/E licence; pending verification on "
    "first PSS/E load"
)

#: kind -> (psspy calls used, note)
EVENT_CALLS = {
    "flat": ((), "no event; checks initialisation"),
    "fault_3ph": (
        ("dist_bus_fault", "dist_clear_fault"),
        "units=2 (ohms, at POC_KV) for Z_f > 0; a bolted fault uses units=1 "
        "with the documented default [0, -2e11] MVA",
    ),
    "fault_1ph": (
        ("dist_scmu_fault_2", "dist_clear_fault", "sysmva"),
        "intgar layout [UNBAL=1 (L-G), FLTCON=1, BUS, 0, 0] and Z_f in pu on "
        "SBASE are unverified",
    ),
    "v_step": (("increment_vref",), "VREF of the plant machine's controller"),
    "q_step": (
        ("increment_vref",),
        "assumes the plant controller in reactive-power mode, where VREF holds "
        "Qref; unverified",
    ),
    "p_step": (("increment_gref",), "GREF of the plant machine; base assumed MBASE"),
    "f_step": (
        ("mdlind", "dsrval", "change_state", "run"),
        "source angle advanced by 2*pi*df*t every 5 ms (H = 0 machine holds "
        "its angle); dsrval('STATE', K) return form unverified",
    ),
    "f_ramp": (
        ("mdlind", "dsrval", "change_state", "run"),
        "as f_step with the angle integral of the ramp, then constant df",
    ),
    "phase_jump": (
        ("mdlind", "dsrval", "change_state"),
        "one step of the GENCLS angle state; dsrval return form unverified",
    ),
}

#: channel -> (psspy call, note)
CHANNEL_CALLS = {
    "v_poc_pu": ("voltage_channel", "or voltage_and_angle_channel with theta"),
    "theta_poc_deg": ("voltage_and_angle_channel", "absolute angle, not vs source"),
    "f_hz": ("bus_frequency_channel", "PSS/E records pu deviation, not Hz"),
    "p_poc_mw": ("branch_p_and_q_channel", "flow from plant side (from bus)"),
    "q_poc_mvar": ("branch_p_and_q_channel", "flow from plant side (from bus)"),
    "id_pu": ("machine_array_channel", "code 22 (active current command)"),
    "iq_pu": ("machine_array_channel", "code 23 (reactive current command)"),
    "i_poc_pu": ("branch_mva_channel", "MVA; read-back divides by V and rating"),
}

ALLOWED_IMPORTS = frozenset({"psspy", "dyntools", "os", "sys"})
_FORBIDDEN_CALLS = frozenset(
    {"open", "eval", "exec", "__import__", "compile", "getattr", "globals", "vars"}
)
_FILE_LIKE = re.compile(r"[^\s]+\.[A-Za-z][A-Za-z0-9]{0,4}")
_ABS = re.compile(r"(/|\\|~|[A-Za-z]:[\\/])")
_PLANT_KINDS = ("v_step", "q_step", "p_step")
_STEP_DT_S = 0.005
_TWO_PI = 2.0 * math.pi


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def check_script(text, allowed_files, channels=()) -> set:
    """
    Parse ``text`` and return the file names it references; raise ContractError
    listing every breach of the pack contract (module docstring).

    A string literal names a file when it has no whitespace and ends in an
    extension, or holds a path separator; docstrings are skipped.
    """
    tree = ast.parse(text)
    allowed, problems, files, literals = set(allowed_files), [], set(), set()
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs:
                continue
            s = node.value
            literals.add(s)
            if _ABS.match(s):
                problems.append(f"absolute path literal {s!r}")
            elif "/" in s or "\\" in s or _FILE_LIKE.fullmatch(s):
                files.add(s)
                if s not in allowed:
                    problems.append(f"file {s!r} is not in the pack {sorted(allowed)}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                if name.split(".")[0] not in ALLOWED_IMPORTS:
                    problems.append(f"import of {name!r}")
            if isinstance(node, ast.ImportFrom) and node.module in ("os", "sys"):
                bad = [a.name for a in node.names if a.name != "path"]
                if bad:
                    problems.append(f"import of {bad} from {node.module}")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.split(".")[-1] in _FORBIDDEN_CALLS or "subprocess" in name:
                problems.append(f"call to {name}")
        elif isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Attribute):
            root = _dotted(node).split(".")[0]
            if root == "os" and node.attr != "path":
                problems.append(f"os call outside os.path: os.{node.attr}")
            if root == "sys" and node.attr != "path":
                problems.append(f"sys use outside sys.path: sys.{node.attr}")
    for ch in channels:
        if ch not in literals:
            problems.append(f"channel {ch!r} has no ident literal")
    if problems:
        raise ContractError("psspy script: " + "; ".join(problems))
    return files


def _pack_local(label, name):
    if not isinstance(name, str) or not name:
        raise ContractError(f"{label} {name!r}: not a file name")
    if os.path.isabs(name) or _ABS.match(name):
        raise ContractError(f"{label} {name!r}: absolute path; give a pack-local name")
    if os.path.basename(name) != name or name in (".", ".."):
        raise ContractError(f"{label} {name!r}: not pack-local (no directories)")


def _event_lines(case, poc_kv):
    p, kind = case.params, case.kind
    if kind == "flat":
        return ["# flat run: no event"]
    if kind == "fault_3ph":
        zf = fault_impedance_ohm(case, poc_kv)
        vals = "1, POC_KV, [0.0, -2.0e11]" if zf == 0 else (
            f"2, POC_KV, [{zf.real!r}, {zf.imag!r}]"
        )
        return [
            f"# three-phase fault, retained voltage {p['v_retained_pu']!r} pu",
            f"psspy.dist_bus_fault(POC_BUS, {vals})",
            f"psspy.run(0, T_EVENT + {p['duration_s']!r}, 1000, 1, 0)",
            "psspy.dist_clear_fault(1)",
        ]
    if kind == "fault_1ph":
        zf = fault_impedance_ohm(case, poc_kv)
        return [
            f"# single-phase-to-earth fault, retained voltage {p['v_retained_pu']!r} pu",
            f"ZF_OHM = ({zf.real!r}, {zf.imag!r})",
            "ZF_PU = [z * psspy.sysmva() / POC_KV**2 for z in ZF_OHM]",
            "psspy.dist_scmu_fault_2([1, 1, POC_BUS, 0, 0], ZF_PU + [0.0, 0.0])",
            f"psspy.run(0, T_EVENT + {p['duration_s']!r}, 1000, 1, 0)",
            "psspy.dist_clear_fault(1)",
        ]
    if kind in ("v_step", "q_step"):
        return [f"psspy.increment_vref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    if kind == "p_step":
        return [f"psspy.increment_gref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    head = [
        "ierr, K = psspy.mdlind(GRID_BUS, GRID_ID, 'GEN', 'STATE')",
        "ierr, ANGLE0 = psspy.dsrval('STATE', K)",
    ]
    if kind == "phase_jump":
        rad = math.radians(p["angle_deg"])
        return head + [f"psspy.change_state(K, ANGLE0 + {rad!r})"]
    if kind == "f_step":
        rate, ramp, hold = p["step_hz"], 0.0, 0.0
    else:
        rate, ramp, hold = 0.0, p["ramp_hz_per_s"], p["duration_s"]
    return head + [
        f"DF0, RAMP, HOLD, DT = {rate!r}, {ramp!r}, {hold!r}, {_STEP_DT_S!r}",
        "n = 1",
        "while T_EVENT + n * DT < T_END:",
        "    tau = n * DT",
        "    th = min(tau, HOLD)",
        "    cycles = DF0 * tau + RAMP * (0.5 * th * th + HOLD * (tau - th))",
        f"    psspy.change_state(K, ANGLE0 + {_TWO_PI!r} * cycles)",
        "    psspy.run(0, T_EVENT + tau, 1000, 1, 0)",
        "    n += 1",
    ]


def _channel_lines(case):
    ch, out = set(case.channels), []
    if "theta_poc_deg" in ch:
        out.append(
            "psspy.voltage_and_angle_channel([-1, -1, -1, POC_BUS], "
            "['v_poc_pu', 'theta_poc_deg'])"
        )
    elif "v_poc_pu" in ch:
        out.append("psspy.voltage_channel([-1, -1, -1, POC_BUS], 'v_poc_pu')")
    if "f_hz" in ch:
        out.append("psspy.bus_frequency_channel([-1, POC_BUS], 'f_hz')")
    if ch & {"p_poc_mw", "q_poc_mvar"}:
        out.append(
            "psspy.branch_p_and_q_channel([-1, -1, -1, BR_FROM, BR_TO], BR_CKT, "
            "['p_poc_mw', 'q_poc_mvar'])"
        )
    for name, code in (("id_pu", 22), ("iq_pu", 23)):
        if name in ch:
            out.append(
                f"psspy.machine_array_channel([-1, {code}, PLANT_BUS], PLANT_ID, {name!r})"
            )
    if "i_poc_pu" in ch:
        out.append(
            "psspy.branch_mva_channel([-1, -1, -1, BR_FROM, BR_TO], BR_CKT, 'i_poc_pu')"
        )
    return out


def _script(case, files, poc_bus, poc_kv, grid_machine, mbase, plant_branch, plant_machine):
    g, cid = case.grid, case.case_id
    z1, z0 = thevenin_impedances_ohm(g, poc_kv)
    to_pu = mbase / poc_kv**2
    z1p, z0p = z1 * to_pu, (None if z0 is None else z0 * to_pu)
    params = ", ".join(f"{k}={v!r}" for k, v in case.params.items()) or "none"
    lines = [
        '"""',
        f"PSS/E run for battery case {cid}.",
        "",
        f"Case {cid}; kind {case.kind}; TRGC {case.trgc}; grid corner {g.label} "
        f"(Sk {g.sk_mva!r} MVA, X/R {g.x_over_r!r}); parameters: {params}; "
        f"source {case.source}.",
        "",
        PENDING + ".",
        '"""',
        "import os",
        "",
        "import dyntools",
        "import psspy",
        "",
        "HERE = os.path.dirname(os.path.abspath(__file__))",
    ]
    for var, name in files.items():
        lines.append(f"{var} = os.path.join(HERE, {name!r})")
    lines += [
        f"POC_BUS, POC_KV = {poc_bus!r}, {float(poc_kv)!r}",
        f"GRID_BUS, GRID_ID, GRID_MBASE = {grid_machine[0]!r}, {grid_machine[1]!r}, "
        f"{float(mbase)!r}",
        f"Z1_PU = ({z1p.real!r}, {z1p.imag!r})  # Thevenin Z1 on GRID_MBASE",
        f"T_EVENT, T_END = {case.t_event_s!r}, {case.t_end_s!r}",
    ]
    if plant_branch is not None:
        lines.append("BR_FROM, BR_TO, BR_CKT = {!r}, {!r}, {!r}".format(*plant_branch))
    if plant_machine is not None:
        lines.append("PLANT_BUS, PLANT_ID = {!r}, {!r}".format(*plant_machine))
    lines += [
        "",
        "psspy.psseinit()",
        "psspy.read(0, RAW)",
        "psspy.resq(SEQ)",
        "ierr, mbase = psspy.macdat(GRID_BUS, GRID_ID, 'MBASE')",
        "if ierr or abs(mbase - GRID_MBASE) > 1e-6:",
        "    raise SystemExit('grid machine MBASE differs from the generated base')",
        "_i, _f = psspy.getdefaultint(), psspy.getdefaultreal()",
        "psspy.machine_chng_2(GRID_BUS, GRID_ID, [_i] * 6, "
        "[_f] * 7 + list(Z1_PU) + [_f] * 8)",
    ]
    if z0p is not None:
        lines += [
            f"Z0_PU = ({z0p.real!r}, {z0p.imag!r})  # Thevenin Z0 on GRID_MBASE",
            "psspy.seq_machine_data_3(GRID_BUS, GRID_ID, 0, "
            "list(Z1_PU) + list(Z1_PU) + list(Z0_PU) + [_f, _f])",
        ]
    lines += [
        "psspy.cong(0)",
        "for k in (1, 2, 3):",
        "    psspy.conl(0, 1, k, [0, 0], [100.0, 0.0, 0.0, 100.0])",
        "psspy.ordr(0)",
        "psspy.fact()",
        "psspy.tysl(0)",
        "psspy.dyre_new([1, 1, 1, 1], DYR, '', '', '')",
        *_channel_lines(case),
        "psspy.strt(0, OUT)",
        "psspy.run(0, T_EVENT, 1000, 1, 0)",
        *_event_lines(case, poc_kv),
        "psspy.run(0, T_END, 1000, 1, 0)",
        "dyntools.CHNF(OUT).csvout(channels='', csvfile=CSV)",
        "",
    ]
    return "\n".join(lines)


def write_scripts(
    cases,
    pack_dir,
    *,
    raw,
    seq,
    dyr,
    poc_bus,
    poc_kv,
    grid_machine,
    grid_mbase_mva,
    plant_branch=None,
    plant_machine=None,
) -> dict:
    """
    Write ``run_<case_id>.py`` into ``pack_dir`` for each case; return
    ``{case_id: script file name}``.

    ``raw``/``seq``/``dyr`` are pack-local file names; ``poc_bus`` the RAW bus
    number of the PoC, ``poc_kv`` its base kV; ``grid_machine`` the grid source
    ``(bus, id)`` with MBASE ``grid_mbase_mva``; ``plant_branch`` the PoC branch
    ``(from_bus = plant side, to_bus, ckt)`` for P/Q/current channels;
    ``plant_machine`` the ``(bus, id)`` whose references the V/Q/P steps move
    and whose current commands id/iq record.
    """
    cases = tuple(cases)
    if not cases:
        raise ContractError("no battery cases to write scripts for")
    cases = validate_battery(cases)
    for label, name in (("raw", raw), ("seq", seq), ("dyr", dyr)):
        _pack_local(label, name)
    for label, v in (("poc_kv", poc_kv), ("grid_mbase_mva", grid_mbase_mva)):
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        if not ok or not math.isfinite(v) or v <= 0:
            raise ContractError(f"{label}={v!r} must be a finite number > 0")
    for c in cases:
        if plant_branch is None and set(c.channels) & {"p_poc_mw", "q_poc_mvar", "i_poc_pu"}:
            raise ContractError(f"case {c.case_id}: P/Q/current channels need plant_branch")
        needs_machine = c.kind in _PLANT_KINDS or set(c.channels) & {"id_pu", "iq_pu"}
        if plant_machine is None and needs_machine:
            raise ContractError(
                f"case {c.case_id}: setpoint steps and id/iq channels need plant_machine"
            )
    os.makedirs(pack_dir, exist_ok=True)
    written = {}
    for c in cases:
        files = {
            "RAW": raw,
            "SEQ": seq,
            "DYR": dyr,
            "OUT": f"{c.case_id}.out",
            "CSV": f"{c.case_id}.csv",
        }
        text = _script(c, files, poc_bus, poc_kv, grid_machine, grid_mbase_mva,
                       plant_branch, plant_machine)
        check_script(text, set(files.values()), c.channels)
        name = f"run_{c.case_id}.py"
        with open(os.path.join(pack_dir, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written[c.case_id] = name
    return written
