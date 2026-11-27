"""
psspy run scripts for the test battery: one script per case (GC-04 item 6).

Each ``run_<case_id>.py`` loads the pack's RAW, ``.seq`` and ``.dyr``, turns
the grid source machine (a GENCLS with H = 0, an infinite bus) into the case's
Thevenin source, applies the case's event at ``t_event_s`` and writes
``<case_id>.out`` plus ``<case_id>.csv`` next to itself (TRGC 04/05).

**Precondition.** The RAW already holds the SMIB: the grid is one GENCLS
machine (``grid_machine``) at the PoC side, whose impedance the script sets
(Z1, and Z0 when given). The script adds no machine, bus or branch.

**Channels.** PSS/E records ``raw_<name>`` channels as it measures them; after
the run the script reads the ``.out`` back with ``dyntools.CHNF.get_data``,
applies :data:`CONVERSIONS` in a pure ``_convert`` function and writes the CSV
at battery contract version 2 in the read-back's one dialect
(``readback.channels.read_csv``, GK A2 T8): the header is ``time [s]``, then
each of the case's channels as
``<name> [<unit>]`` with name and unit from ``test_battery.CHANNELS``, in the
case's order. So the read-back never sees a PSS/E unit. Every cell is a number,
except a ``connected`` sample that was not read, which is blank (version 1:
see "Connected"). id/iq come from the plant converter's current commands, pu on the plant
machine's MBASE: the pack sets MBASE = plant rated apparent power, which is
the contract base. P and Q are measured at the PoC: ``plant_branch`` is
``(from = poc_bus, to = plant side bus, ckt)``, so PSS/E reports the flow
from the PoC into the plant branch, and :data:`CONVERSIONS` negates both, so
export (plant to grid), i.e. injection, is positive.

**Thevenin source.** ``thevenin_impedances_ohm`` gives Z1 (and Z0) in ohms at
the PoC base kV; the generator converts them to pu on the grid machine's MBASE
(``Z_pu = Z_ohm * MBASE / kV**2``) and writes the numbers as literals. The
script refuses to run when the RAW machine's MBASE differs from the one the
numbers were made on. Z2 = Z1 (test_battery module docstring).

**Pack-local files.** A script names files only by basename and resolves each
against its own directory; :func:`check_script` (also used by the pack) parses
a script with ``ast`` and refuses any other file, an absolute path, an import
outside psspy/dyntools/os, ``open``/``eval``/``exec``/``__import__``, and
any ``os`` call outside ``os.path`` (GK condition S1). The one ``open`` allowed
is ``open(os.path.join(HERE, '<file>.csv'), 'w')`` on a pack file, with
``HERE`` bound once to the script's own directory.

**Solved start.** The RAW is written flat (VM 1, VA 0). After setting the
source impedance each script solves the power flow (``fnsl`` with
:data:`FNSL_OPTIONS`) and refuses to continue (``SystemExit``) when
``psspy.solved()`` is not 0, so dynamic initialisation (``cong`` onwards)
never starts from an unsolved case. :func:`check_script` refuses a script
without ``fnsl``, then that convergence check, then ``cong``, in that order.

**Current, measured at the machine terminal.** ``i_poc_pu`` is the plant
machine's terminal current ITERM, recorded directly (``machine_array_channel``
code :data:`ITERM_CODE`, pu on the machine's MBASE), so it is defined at V = 0:
a bolted fault at the PoC (the core LVRT cases) still gives the fault current.
The script reads that MBASE from the case (``macdat``) and ``_convert`` rebases
the current to pu on the plant rating, ``I * MBASE / S_rated``. The channel is
at the plant machine's terminal, not at the PoC end of ``plant_branch``: no
documented psspy branch-current channel was found (GK A ruling, issue 21). In
pu the two currents differ by the plant branch's shunt and charging current and
by any off-nominal ratio on it; a nominal-ratio transformer changes the
current in A, not in pu. The CSV has no text column.

**Connected** (GC-12, battery contract version 2). ``connected`` is 1 while
the PoC branch and every plant machine (``status_machines``) is in service, 0
once one is not. PSS/E records no status channel, so the script polls: every
``psspy.run`` goes through ``_run(t_stop)``, which stops at each multiple of
``POLL_DT_S`` inside the run and at ``t_stop`` itself (``_tpause``), so each
event time stays an exact stop, and after each stop reads
``brnint(..., 'STATUS')`` on the PoC branch and ``macint(..., 'STATUS')`` on
each machine (``_poll``, first at t = 0). ``_combine`` gives 1 only when every
reading is 1 with ``ierr`` 0, 0 when any reads 0, else a failed poll (blank,
never 1), with its reason. ``_latch`` holds every poll after a 0 at 0; a blank
never sets it. Each ``.out`` sample takes the first poll at or after it
(``_connected``): a 1 is exact, since a 0 latches; a trip shows at most
``POLL_DT_S`` early, never late. ``POLL_DT_S`` is the case's time step DELT
(``dsrval('DELT')``, read after ``strt``): the runs write a sample every step
(NPLT = 1), so the polls are never coarser than the samples (GK A P2). A
version-1 script keeps master's ``t_s`` / bare-name / ``status`` CSV byte-equal
and polls nothing; only version 2 writes the read-back dialect above. The reasons of failed polls go to the
sidecar ``<case_id>.status.txt`` (text, read back as a reason, never as data).

The psspy calls were written without a PSS/E licence: :data:`PENDING` is
repeated in every script, :data:`EVENT_CALLS` / :data:`CHANNEL_CALLS`
state per kind and channel which calls are used and what is unverified, and
:data:`UNVERIFIED` lists the remaining unverified calls and conventions.
"""

import ast
import math
import os
import re

from gridspine.handoff.test_battery import (
    CHANNELS,
    CONNECTED,
    CONTRACT_VERSION,
    contract_channels,
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
)
from gridspine.schema.contracts import ContractError

PENDING = (
    "psspy calls generated without a PSS/E licence; pending verification on "
    "first PSS/E load"
)

#: fnsl options: taps locked, area interchange off, phase shifters locked, dc
#: taps locked, switched shunts locked, no flat start (the RAW values are
#: already flat), var limits applied at once, non-divergent solution on.
FNSL_OPTIONS = (0, 0, 0, 0, 0, 0, 0, 1)
#: GENCLS angle state = STATE K + this offset, K from ``mdlind(..., 'STATE')``.
ANGLE_STATE_OFFSET = 1
#: machine_array_channel quantity code of ITERM (machine terminal current, pu on
#: MBASE); listed in UNVERIFIED
ITERM_CODE = 12

#: call or convention -> what is assumed and unverified
UNVERIFIED = {
    "GENCLS angle state": (
        "mdlind(GRID_BUS, GRID_ID, 'GEN', 'STATE') gives K, the first GENCLS "
        "state; the rotor angle is assumed STATE K+1 with K the speed deviation "
        "(the speed-then-angle order of the larger machine models); no public "
        "source found"
    ),
    "fnsl": "option order and meaning of FNSL_OPTIONS",
    "multiple REPCA1 monitoring one branch": (
        "a detailed plant's machines each carry a REPCA1 on the same PoC branch, "
        "gains shared by MBASE (dyr_writer.share_repca1); derived from the "
        "machine-MBASE base of REPCA1 and checked against a reduced REPC_A model, "
        "not against PSS/E"
    ),
    "REPCA1 references": (
        "increment_gref and increment_vref on a machine with a REPCA1 move that "
        "REPCA1's Plant_pref and Vref/Qref, in pu of the machine's MBASE; a "
        "detailed plant steps every machine's (the step scaling relies on it)"
    ),
    "detailed plant currents": (
        "a detailed plant's id_pu, iq_pu and i_poc_pu are the MBASE-weighted sums of "
        "its machines' channels on the plant rating, which assumes one terminal bus "
        "and currents in phase (equal pu dispatch)"
    ),
    "DELT": "dsrval('DELT') returns (ierr, the dynamic simulation time step in s)",
    "STATUS polls": (
        "brnint(..., 'STATUS') and macint(..., 'STATUS') return (ierr, 1 in service "
        "/ 0 out of service), and a plant protection model's trip sets the "
        "machine's or the branch's status to 0 during the run"
    ),
    "solved": "psspy.solved() returns 0 when the last solution converged",
    "ITERM channel": (
        f"machine_array_channel quantity code {ITERM_CODE} is ITERM, the terminal current "
        "magnitude in pu on MBASE (recalled from the PSS/E API table, not checked; "
        "CHSB numbers ITERM 19); the same unchecked table gives the Ip/Iq command "
        "codes 22/23"
    ),
}

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

#: recorded PSS/E channel -> (psspy call, note)
CHANNEL_CALLS = {
    "raw_v_poc_pu": ("voltage_channel", "pu of bus base kV (= POC_KV)"),
    "raw_theta_poc_deg": ("voltage_and_angle_channel", "absolute bus angle, deg"),
    "raw_theta_src": ("machine_array_channel", "code 1 ANGLE of the grid GENCLS, deg"),
    "raw_f_hz": ("bus_frequency_channel", "pu deviation from base frequency"),
    "raw_p_poc_mw": (
        "branch_p_and_q_channel",
        "MW at the from (PoC) bus, into the plant",
    ),
    "raw_q_poc_mvar": (
        "branch_p_and_q_channel",
        "Mvar at the from (PoC) bus, into the plant",
    ),
    "raw_id_pu": (
        "machine_array_channel",
        "code 22, Ip command, pu on MBASE = plant rated",
    ),
    "raw_iq_pu": (
        "machine_array_channel",
        "code 23, Iq command, pu on MBASE = plant rated; positive = capacitive "
        "injection assumed (REEC_A convention), unverified",
    ),
    "raw_i_term_pu": (
        "machine_array_channel",
        f"code {ITERM_CODE}, ITERM terminal current magnitude of the plant machine, "
        "pu on its MBASE; measured at the machine terminal, not the PoC; code "
        "unverified",
    ),
}

#: contract channel -> (recorded inputs a, b, c..., per-sample expression, formula)
CONVERSIONS = {
    "v_poc_pu": (("raw_v_poc_pu",), "a", "v_poc_pu = V"),
    "theta_poc_deg": (
        ("raw_theta_poc_deg", "raw_theta_src"),
        "a - b",
        "theta = bus angle - source EMF angle",
    ),
    "f_hz": (("raw_f_hz",), "f0_hz * (1.0 + a)", "f = f0 * (1 + dev_pu)"),
    "p_poc_mw": (("raw_p_poc_mw",), "-a", "P plant -> grid = -(PoC -> plant)"),
    "q_poc_mvar": (("raw_q_poc_mvar",), "-a", "Q plant -> grid = -(PoC -> plant)"),
    "id_pu": (("raw_id_pu",), "a", "Ip command on MBASE = plant rated"),
    "iq_pu": (("raw_iq_pu",), "a", "Iq command on MBASE = plant rated"),
    "i_poc_pu": (
        ("raw_i_term_pu",),
        "a * m_base / s_rated",
        "|I| = ITERM * MBASE / S_rated: plant machine terminal current, pu on the "
        "plant rating",
    ),
}

ALLOWED_IMPORTS = frozenset({"psspy", "dyntools", "os"})
_FORBIDDEN_CALLS = frozenset(
    {"open", "eval", "exec", "__import__", "compile", "getattr", "globals", "vars"}
)
_FILE_LIKE = re.compile(r"[^\s]+\.[A-Za-z][A-Za-z0-9]{0,4}")
_ABS = re.compile(r"(/|\\|~|[A-Za-z]:[\\/])")
_PLANT_KINDS = ("v_step", "q_step", "p_step")
_STEP_DT_S = 0.005
#: the time tolerance (s) of the ``connected`` poll helpers; the poll grid is DELT
EPS_S = 1e-9
#: the per-case sidecar that carries failed-poll reasons (text, never data)
STATUS_SIDECAR = "{case_id}.status.txt"
_TWO_PI = 2.0 * math.pi


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


_HERE_EXPR = "os.path.dirname(os.path.abspath(__file__))"


def _open_ok(node, allowed):
    """
    True for exactly ``open(os.path.join(HERE, '<allowed>'), 'w')`` on a
    ``.csv`` or ``.status.txt`` pack file.
    """
    if node.keywords or len(node.args) != 2:
        return False
    path, mode = node.args
    if not (isinstance(mode, ast.Constant) and mode.value == "w"):
        return False
    if not (isinstance(path, ast.Call) and _dotted(path.func) == "os.path.join"):
        return False
    if path.keywords or len(path.args) != 2:
        return False
    here, name = path.args
    return (
        isinstance(here, ast.Name)
        and here.id == "HERE"
        and isinstance(name, ast.Constant)
        and name.value in allowed
        and str(name.value).endswith((".csv", ".status.txt"))
    )


def _calls(node):
    return {_dotted(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}


def _solve_order_ok(tree):
    """
    True when the module's top level holds ``psspy.fnsl(...)``, then an
    ``if`` on ``psspy.solved()`` that raises, then ``psspy.cong(...)``.
    """
    want = [
        lambda n: isinstance(n, ast.Expr) and "psspy.fnsl" in _calls(n),
        lambda n: (
            isinstance(n, ast.If)
            and "psspy.solved" in _calls(n.test)
            and any(isinstance(b, ast.Raise) for b in n.body)
        ),
        lambda n: isinstance(n, ast.Expr) and "psspy.cong" in _calls(n),
    ]
    for node in tree.body:
        if want and want[0](node):
            want.pop(0)
    return not want


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
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                bad = [a.name for a in node.names if a.name != "path"]
                if bad:
                    problems.append(f"import of {bad} from {node.module}")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name == "open" and _open_ok(node, allowed):
                continue
            if name.split(".")[-1] in _FORBIDDEN_CALLS or "subprocess" in name:
                problems.append(f"call to {name}")
        elif isinstance(node, ast.Attribute) and not isinstance(
            node.value, ast.Attribute
        ):
            root = _dotted(node).split(".")[0]
            if root == "os" and node.attr != "path":
                problems.append(f"os call outside os.path: os.{node.attr}")
    here = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and n.id == "HERE"
        and not isinstance(n.ctx, ast.Load)
    ]
    binds = (
        [
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and [ast.dump(t) for t in n.targets] == [ast.dump(here[0])]
        ]
        if here
        else []
    )
    if len(here) != 1 or len(binds) != 1 or ast.unparse(binds[0].value) != _HERE_EXPR:
        problems.append(f"HERE must be bound once, to {_HERE_EXPR}")
    if not _solve_order_ok(tree):
        problems.append(
            "the case must be solved before dynamics: psspy.fnsl, then an if on "
            "psspy.solved() that raises, then psspy.cong"
        )
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


def _run_call(polled, t):
    """One run to ``t``: through ``_run`` (polled, v2) or psspy.run as on v1."""
    return f"_run({t})" if polled else f"psspy.run(0, {t}, 1000, 1, 0)"


def _event_lines(case, poc_kv, polled=False, multi=False):
    p, kind = case.params, case.kind
    if kind == "flat":
        return ["# flat run: no event"]
    if kind == "fault_3ph":
        zf = fault_impedance_ohm(case, poc_kv)
        vals = (
            "1, POC_KV, [0.0, -2.0e11]"
            if zf == 0
            else (f"2, POC_KV, [{zf.real!r}, {zf.imag!r}]")
        )
        return [
            f"# three-phase fault, retained voltage {p['v_retained_pu']!r} pu",
            f"psspy.dist_bus_fault(POC_BUS, {vals})",
            _run_call(polled, f"T_EVENT + {p['duration_s']!r}"),
            "psspy.dist_clear_fault(1)",
        ]
    if kind == "fault_1ph":
        zf = fault_impedance_ohm(case, poc_kv)
        return [
            f"# single-phase-to-earth fault, retained voltage {p['v_retained_pu']!r} pu",
            f"ZF_OHM = ({zf.real!r}, {zf.imag!r})",
            "ZF_PU = [z * psspy.sysmva() / POC_KV**2 for z in ZF_OHM]",
            "psspy.dist_scmu_fault_2([1, 1, POC_BUS, 0, 0], ZF_PU + [0.0, 0.0])",
            _run_call(polled, f"T_EVENT + {p['duration_s']!r}"),
            "psspy.dist_clear_fault(1)",
        ]
    if kind in _PLANT_KINDS and multi:
        return _member_step_lines(kind, p["step_pu"])
    if kind in ("v_step", "q_step"):
        return [f"psspy.increment_vref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    if kind == "p_step":
        return [f"psspy.increment_gref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    head = [
        "ierr, K = psspy.mdlind(GRID_BUS, GRID_ID, 'GEN', 'STATE')",
        f"K_ANGLE = K + {ANGLE_STATE_OFFSET!r}  # GENCLS angle state: unverified",
        "ierr, ANGLE0 = psspy.dsrval('STATE', K_ANGLE)",
    ]
    if kind == "phase_jump":
        rad = math.radians(p["angle_deg"])
        return head + [f"psspy.change_state(K_ANGLE, ANGLE0 + {rad!r})"]
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
        f"    psspy.change_state(K_ANGLE, ANGLE0 + {_TWO_PI!r} * cycles)",
        "    " + _run_call(polled, "T_EVENT + tau"),
        "    n += 1",
    ]


def _recorded(case):
    # ``connected`` is polled (``_poll``), not a recorded channel
    return {r for ch in case.channels if ch != CONNECTED for r in CONVERSIONS[ch][0]}


def _member_step_lines(kind, step):
    """
    A detailed plant's step (GC-04b): every machine's REPCA1 reference moves.
    A V step is in pu volts, the same for every machine. A Q or P step is in
    pu of the plant rating; each machine's reference is in pu of its own
    MBASE, so it moves by ``step * PLANT_MBASE / M_BASE[k]``: the same
    reference MW on every machine, and each machine's output moves by the
    step in its own pu, which is the aggregate's step under the gain sharing
    (``dyr_writer.share_repca1``).
    """
    call = "increment_gref" if kind == "p_step" else "increment_vref"
    if kind == "v_step":
        return [
            "for bus, mid in PLANT_MACHINES:  # pu volts, the same for every machine",
            f"    psspy.{call}(bus, mid, {step!r})",
        ]
    return [
        "for (bus, mid), m in zip(PLANT_MACHINES, M_BASE):  # same reference MW on each",
        f"    psspy.{call}(bus, mid, {step!r} * PLANT_MBASE / m)",
    ]


#: Raw per-machine channels a detailed plant records once per machine.
_MEMBER_RAW = ("raw_id_pu", "raw_iq_pu", "raw_i_term_pu")


def _channel_lines(case, multi=False):
    raw, out = _recorded(case), []
    if "raw_theta_poc_deg" in raw:
        out.append(
            "psspy.voltage_and_angle_channel([-1, -1, -1, POC_BUS], "
            "['raw_v_poc_pu', 'raw_theta_poc_deg'])"
        )
    elif "raw_v_poc_pu" in raw:
        out.append("psspy.voltage_channel([-1, -1, -1, POC_BUS], 'raw_v_poc_pu')")
    if "raw_theta_src" in raw:
        out.append(
            "psspy.machine_array_channel([-1, 1, GRID_BUS], GRID_ID, 'raw_theta_src')"
        )
    if "raw_f_hz" in raw:
        out.append("psspy.bus_frequency_channel([-1, POC_BUS], 'raw_f_hz')")
    if raw & {"raw_p_poc_mw", "raw_q_poc_mvar"}:
        out.append(
            "psspy.branch_p_and_q_channel([-1, -1, -1, BR_FROM, BR_TO], BR_CKT, "
            "['raw_p_poc_mw', 'raw_q_poc_mvar'])"
        )
    for name, code in (
        ("raw_id_pu", 22),
        ("raw_iq_pu", 23),
        ("raw_i_term_pu", ITERM_CODE),
    ):
        if name in raw and multi:
            out += [
                "for k, (bus, mid) in enumerate(PLANT_MACHINES):",
                f"    psspy.machine_array_channel([-1, {code}, bus], mid, {name!r} + '_%d' % k)",
            ]
        elif name in raw:
            out.append(
                f"psspy.machine_array_channel([-1, {code}, PLANT_BUS], PLANT_ID, {name!r})"
            )
    return out


def _convert_lines(case, multi=False):
    """
    A pure ``_convert(rec, f0_hz, s_rated, m_base)``: recorded lists -> contract
    units, keyed ``time`` and the contract channel names; ``m_base`` is the plant
    machine's MBASE as the case holds it (``None`` without a plant machine; a
    list, one per machine, for a detailed plant, whose id/iq/i_poc are the
    MBASE-weighted sums of its machines' channels on the plant rating); and
    a pure ``_csv_text(data, columns, header)`` that formats them (a str cell as
    is, a number by ``repr(float(x))``). A case that carries ``connected`` also
    gets the pure status helpers (:func:`_status_lines`), and ``_convert`` reads
    the polls from ``rec['poll_t']`` / ``rec['poll_v']``.
    """
    polled = CONNECTED in case.channels
    t_key = "time" if polled else "t_s"  # v1 keeps master's t_s and status (GK A P1)
    out = [
        "def _convert(rec, f0_hz, s_rated, m_base):",
        f"    out = {{{t_key!r}: list(rec['t_s'])}}",
    ]
    for ch in case.channels:
        if ch == CONNECTED:
            continue
        inputs, expr, formula = CONVERSIONS[ch]
        if len(inputs) > len("abc"):
            raise ContractError(
                f"conversion of {ch!r}: {len(inputs)} inputs, at most 3"
            )
        if multi and inputs[0] in _MEMBER_RAW:
            out += [
                f"    # sum over the plant machines of {inputs[0]} * MBASE_k / S_rated "
                "(one terminal bus, currents in phase)",
                f"    out[{ch!r}] = [sum(a * m for a, m in zip(row, m_base)) / s_rated "
                f"for row in zip(*[rec[{inputs[0]!r} + '_%d' % k] "
                "for k in range(len(m_base))])]",
            ]
            continue
        names = "abc"[: len(inputs)]
        cols = ", ".join(f"rec[{r!r}]" for r in inputs)
        out += [
            f"    # {formula}",
            f"    out[{ch!r}] = [{expr} for ({', '.join(names)},) in zip({cols})]",
        ]
    if polled:
        out += [
            "    # the latched status polls, each sample taking the first poll at or after it",
            f"    out[{CONNECTED!r}] = _connected(rec['t_s'], rec['poll_t'], rec['poll_v'])",
        ]
    else:
        out.append("    out['status'] = ['' for _ in out['t_s']]")
    sig, cols = (
        ("data, columns, header", "c in columns")
        if polled
        else ("data, header", "h in header")
    )
    return out + [
        "    return out",
        "",
        "",
        f"def _csv_text({sig}):",
        "    def cell(x):",
        "        return x if isinstance(x, str) else repr(float(x))",
        "",
        f"    rows = [header, *zip(*[data[{cols[0]}] for {cols}])]",
        "    return ''.join(','.join(cell(x) for x in row) + '\\n' for row in rows)",
        "",
    ]


def _status_lines():
    """
    The pure helpers behind ``connected`` (module docstring, "Connected"):
    ``_tpause`` the run stops, ``_combine`` one poll, ``_latch`` the trip latch,
    ``_connected`` the per-sample cells.
    """
    return [
        "def _tpause(t_now, t_stop, dt):",
        "    # every k*dt strictly inside (t_now, t_stop), then t_stop itself",
        "    out, k = [], int(t_now // dt)",
        "    while (k + 1) * dt < t_stop - EPS_S:",
        "        k += 1",
        "        if k * dt > t_now + EPS_S:",
        "            out.append(k * dt)",
        "    return out + [t_stop]",
        "",
        "",
        "def _combine(readings):",
        "    # readings: (label, ierr, status); 1 only if all read 1, a 0 read -> 0,",
        "    # else a failed poll -> None (blank), with the reason",
        "    if any(ierr == 0 and st == 0 for _, ierr, st in readings):",
        "        return 0, ''",
        "    bad = [",
        "        f'{label}: ierr={ierr}, status={st!r}'",
        "        for label, ierr, st in readings",
        "        if ierr != 0 or st != 1",
        "    ]",
        "    return (None, '; '.join(bad)) if bad else (1, '')",
        "",
        "",
        "def _latch(polls):",
        "    # a 0 holds every later poll at 0; a blank (None) never sets the latch",
        "    out, tripped = [], False",
        "    for v in polls:",
        "        tripped = tripped or v == 0",
        "        out.append(0 if tripped else v)",
        "    return out",
        "",
        "",
        "def _connected(times, poll_t, poll_v):",
        "    # sample t takes the first latched poll at or after t; none after -> blank",
        "    latched, out, j = _latch(poll_v), [], 0",
        "    for t in times:",
        "        while j < len(poll_t) and poll_t[j] < t - EPS_S:",
        "            j += 1",
        "        v = latched[j] if j < len(poll_t) else None",
        "        out.append('' if v is None else v)",
        "    return out",
        "",
    ]


def _delt_lines():
    """
    Poll every time step (GK A P2): the output writes a sample each step
    (``psspy.run`` NPLT = 1), so POLL_DT_S = DELT is never coarser than the samples.
    """
    return [
        "ierr, DELT = psspy.dsrval('DELT')",
        "if ierr or not DELT > 0:",
        "    raise SystemExit('time step DELT not read: connected cannot be polled')",
        "POLL_DT_S = DELT  # one poll per time step, so per output sample",
    ]


def _run_lines(status_machines):
    """
    ``_run(t_stop)``: how a ``connected`` (v2) script advances the
    simulation: it stops at every ``_tpause`` point and polls the PoC branch
    and each plant machine after each stop.
    """
    return [
        f"STATUS_MACHINES = {list(status_machines)!r}",
        "POLLS = {'t_now': 0.0, 't': [], 'v': [], 'why': []}",
        "",
        "",
        *_status_lines(),
        "",
        "def _poll(t):",
        "    ierr, st = psspy.brnint(BR_FROM, BR_TO, BR_CKT, 'STATUS')",
        "    readings = [('PoC branch', ierr, st)]",
        "    for bus, mid in STATUS_MACHINES:",
        "        ierr, st = psspy.macint(bus, mid, 'STATUS')",
        "        readings.append((f'machine {bus} {mid}', ierr, st))",
        "    v, why = _combine(readings)",
        "    POLLS['t'].append(t)",
        "    POLLS['v'].append(v)",
        "    if why:",
        "        POLLS['why'].append(f't={t!r} s: not read ({why})')",
        "",
        "",
        "def _run(t_stop):",
        "    for t in _tpause(POLLS['t_now'], t_stop, POLL_DT_S):",
        "        psspy.run(0, t, 1000, 1, 0)",
        "        _poll(t)",
        "    POLLS['t_now'] = t_stop",
        "",
    ]


def _header(case):
    """
    The CSV columns (``_convert`` keys) and header. Version 2: ``time [s]`` and
    ``name [unit]`` (GK A2 T8). Version 1 keeps master's ``t_s``, bare names and
    ``status`` byte-equal (GK A P1; no pack writes v1 scripts any more).
    """
    if CONNECTED not in case.channels:
        return None, ["t_s", *case.channels, "status"]
    columns = ["time", *case.channels]
    header = ["time [s]", *(f"{ch} [{CHANNELS[ch].unit}]" for ch in case.channels)]
    return columns, header


def _member_base_lines():
    """
    Every plant machine's MBASE as the case holds it; their sum must be the
    plant rating the steps and currents are scaled to.
    """
    return [
        "M_BASE = []",
        "for bus, mid in PLANT_MACHINES:",
        "    ierr, m = psspy.macdat(bus, mid, 'MBASE')",
        "    if ierr or not m > 0:",
        "        raise SystemExit('plant machine MBASE not read from the case')",
        "    M_BASE.append(m)",
        "if abs(sum(M_BASE) - PLANT_MBASE) > 1e-6:",
        "    raise SystemExit('plant machines MBASE sum differs from the plant rating')",
    ]


def _script(
    case,
    files,
    csv,
    poc_bus,
    poc_kv,
    grid_machine,
    mbase,
    plant_branch,
    plant_machine,
    f0_hz,
    plant_mbase,
    status_machines,
    sidecar,
    plant_machines=None,
):
    g, cid = case.grid, case.case_id
    multi = plant_machines is not None and len(plant_machines) > 1
    polled = CONNECTED in case.channels
    columns, header = _header(case)
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
        f"F0_HZ, PLANT_MBASE = {float(f0_hz)!r}, "
        f"{None if plant_mbase is None else float(plant_mbase)!r}",
        *([f"COLUMNS = {columns!r}"] if polled else []),
        f"HEADER = {header!r}",
    ]
    if polled:
        lines.append(f"EPS_S = {EPS_S!r}  # time tolerance of the status poll helpers")
    if plant_branch is not None:
        lines.append("BR_FROM, BR_TO, BR_CKT = {!r}, {!r}, {!r}".format(*plant_branch))
    if plant_machine is not None:
        lines.append("PLANT_BUS, PLANT_ID = {!r}, {!r}".format(*plant_machine))
    if multi:
        lines.append(f"PLANT_MACHINES = {[tuple(m) for m in plant_machines]!r}")
    lines += [
        "",
        "",
        *_convert_lines(case, multi),
        "",
        *(_run_lines(status_machines) + [""] if polled else []),
        "psspy.psseinit()",
        "psspy.read(0, RAW)",
        "psspy.resq(SEQ)",
        "ierr, mbase = psspy.macdat(GRID_BUS, GRID_ID, 'MBASE')",
        "if ierr or abs(mbase - GRID_MBASE) > 1e-6:",
        "    raise SystemExit('grid machine MBASE differs from the generated base')",
        *(
            _member_base_lines()
            if multi
            else [
                "ierr, M_BASE = psspy.macdat(PLANT_BUS, PLANT_ID, 'MBASE')",
                "if ierr or not M_BASE > 0:",
                "    raise SystemExit('plant machine MBASE not read from the case')",
            ]
            if plant_machine is not None
            else ["M_BASE = None"]
        ),
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
        f"psspy.fnsl({list(FNSL_OPTIONS)!r})",
        "if psspy.solved() != 0:",
        "    raise SystemExit('power flow did not converge; dynamics not started')",
        "psspy.cong(0)",
        "for k in (1, 2, 3):",
        "    psspy.conl(0, 1, k, [0, 0], [100.0, 0.0, 0.0, 100.0])",
        "psspy.ordr(0)",
        "psspy.fact()",
        "psspy.tysl(0)",
        "psspy.dyre_new([1, 1, 1, 1], DYR, '', '', '')",
        *_channel_lines(case, multi),
        "psspy.strt(0, OUT)",
        *(_delt_lines() if polled else []),
        *(["_poll(0.0)"] if polled else []),
        _run_call(polled, "T_EVENT"),
        *_event_lines(case, poc_kv, polled, multi),
        _run_call(polled, "T_END"),
        "_title, idents, values = dyntools.CHNF(OUT).get_data()",
        "rec = {idents[k]: values[k] for k in idents if k != 'time'}",
        "rec['t_s'] = values['time']",
        *(["rec['poll_t'], rec['poll_v'] = POLLS['t'], POLLS['v']"] if polled else []),
        "data = _convert(rec, F0_HZ, PLANT_MBASE, M_BASE)",
        f"with open(os.path.join(HERE, {csv!r}), 'w') as fh:",
        "    fh.write(_csv_text(data, COLUMNS, HEADER))"
        if polled
        else "    fh.write(_csv_text(data, HEADER))",
    ]
    if polled:
        lines += [
            f"with open(os.path.join(HERE, {sidecar!r}), 'w') as fh:",
            "    fh.write(''.join(w + '\\n' for w in POLLS['why']))",
        ]
    lines.append("")
    return "\n".join(lines)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_positive_int(v):
    return _is_int(v) and v > 0


def _check_machine(label, m):
    ok = isinstance(m, tuple) and len(m) == 2
    if not ok or not _is_positive_int(m[0]) or not isinstance(m[1], str):
        raise ContractError(f"{label}={m!r} must be a (positive int bus, str id) tuple")
    if not 1 <= len(m[1]) <= 2:
        raise ContractError(f"{label} id {m[1]!r} must be 1 or 2 characters")


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
    f0_hz,
    plant_branch=None,
    plant_machine=None,
    plant_mbase_mva=None,
    contract_version=CONTRACT_VERSION,
    status_machines=None,
    plant_machines=None,
) -> dict:
    """
    Write ``run_<case_id>.py`` into ``pack_dir`` for each case; return
    ``{case_id: script file name}``.

    ``raw``/``seq``/``dyr`` are pack-local file names; ``poc_bus`` the RAW bus
    number of the PoC, ``poc_kv`` its base kV; ``grid_machine`` the grid source
    ``(bus, id)`` with MBASE ``grid_mbase_mva``; ``plant_branch`` the PoC branch
    ``(from_bus = poc_bus, to_bus = plant side, ckt)`` for the P/Q
    channels, measured at the PoC (a pack whose PoC branch is BUS1 = plant
    side, BUS2 = grid side passes ``(BUS2, BUS1, CKT)``);
    ``plant_machine`` the ``(bus, id)`` whose references the V/Q/P steps move
    and whose current commands id/iq and terminal current i_poc record; ``f0_hz`` the base frequency that
    turns PSS/E's pu frequency deviation into Hz; ``plant_mbase_mva`` the plant
    rating S_rated (the generated MBASE), needed when a case records ``i_poc_pu``.

    ``contract_version`` is the battery's (``test_battery.CONTRACT_VERSIONS``):
    every case is checked against it, and under version 2 each script polls
    ``connected`` (module docstring, "Connected"), which needs ``plant_branch``
    and ``plant_machine``. ``status_machines`` are the ``(bus, id)`` plant
    machines polled for it, default ``(plant_machine,)``; ``connected`` reads 1
    only while the PoC branch and every one of them is in service.

    ``plant_machines`` are a detailed plant's ``(bus, id)`` machines (GC-04b),
    each with a gain-shared REPCA1; ``plant_machine`` is the first of them and
    ``plant_mbase_mva`` the sum of their MBASEs. A V step then moves every
    machine's reference by the step, a Q or P step by the same reference MW
    on each, so each output moves by the step in its own pu
    (``_member_step_lines``), and id/iq/i_poc are the MBASE-weighted sums of
    the machines' channels. One machine writes the aggregate's script.
    """
    cases = tuple(cases)
    if not cases:
        raise ContractError("no battery cases to write scripts for")
    contract_channels(contract_version)
    cases = validate_battery(cases, contract_version)
    for label, name in (("raw", raw), ("seq", seq), ("dyr", dyr)):
        _pack_local(label, name)
    if not _is_positive_int(poc_bus):
        raise ContractError(f"poc_bus={poc_bus!r} must be a positive int bus number")
    _check_machine("grid_machine", grid_machine)
    if plant_machine is not None:
        _check_machine("plant_machine", plant_machine)
    if plant_branch is not None:
        ok = isinstance(plant_branch, tuple) and len(plant_branch) == 3
        if (
            not ok
            or not all(_is_int(b) for b in plant_branch[:2])
            or not isinstance(plant_branch[2], str)
        ):
            raise ContractError(
                f"plant_branch={plant_branch!r} must be a (from bus, to bus, ckt) "
                "tuple of (int, int, str)"
            )
        if plant_branch[0] != poc_bus:
            raise ContractError(
                f"plant_branch from bus {plant_branch[0]!r} is not poc_bus "
                f"{poc_bus!r}: the PoC branch is measured at the PoC"
            )
    positive = [
        ("poc_kv", poc_kv),
        ("grid_mbase_mva", grid_mbase_mva),
        ("f0_hz", f0_hz),
    ]
    if plant_mbase_mva is not None or any("i_poc_pu" in c.channels for c in cases):
        positive.append(("plant_mbase_mva", plant_mbase_mva))
    for label, v in positive:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        if not ok or not math.isfinite(v) or v <= 0:
            raise ContractError(f"{label}={v!r} must be a finite number > 0")
    polled = CONNECTED in contract_channels(contract_version)
    if polled and (plant_branch is None or plant_machine is None):
        raise ContractError(
            f"contract version {contract_version!r} records {CONNECTED!r}: the status "
            "polls need plant_branch and plant_machine"
        )
    if status_machines is None:
        status_machines = (plant_machine,) if polled else ()
    status_machines = tuple(status_machines)
    if polled and not status_machines:
        raise ContractError(f"{CONNECTED!r} needs at least one status machine")
    for m in status_machines:
        _check_machine("status_machines entry", m)
    if plant_machines is not None:
        plant_machines = tuple(tuple(m) for m in plant_machines)
        if not plant_machines:
            raise ContractError("plant_machines is empty")
        for m in plant_machines:
            _check_machine("plant_machines entry", m)
        if len(set(plant_machines)) != len(plant_machines):
            raise ContractError(f"plant_machines {plant_machines!r} repeat a machine")
        if plant_machine is None or tuple(plant_machine) != plant_machines[0]:
            raise ContractError(
                f"plant_machine {plant_machine!r} is not the first of plant_machines "
                f"{plant_machines!r}"
            )
        if len(plant_machines) > 1 and plant_mbase_mva is None:
            raise ContractError(
                "a detailed plant's steps and currents need plant_mbase_mva, the sum "
                "of its machines' MBASE"
            )
    if not polled and status_machines:
        raise ContractError(
            f"status_machines given, but contract version {contract_version!r} has no "
            f"{CONNECTED!r} channel"
        )
    for c in cases:
        if plant_branch is None and set(c.channels) & {"p_poc_mw", "q_poc_mvar"}:
            raise ContractError(f"case {c.case_id}: P/Q channels need plant_branch")
        needs_machine = c.kind in _PLANT_KINDS or set(c.channels) & {
            "id_pu",
            "iq_pu",
            "i_poc_pu",
        }
        if plant_machine is None and needs_machine:
            raise ContractError(
                f"case {c.case_id}: setpoint steps and id/iq/current channels need "
                "plant_machine"
            )
    os.makedirs(pack_dir, exist_ok=True)
    written = {}
    for c in cases:
        files = {"RAW": raw, "SEQ": seq, "DYR": dyr, "OUT": f"{c.case_id}.out"}
        csv = f"{c.case_id}.csv"
        sidecar = STATUS_SIDECAR.format(case_id=c.case_id)
        text = _script(
            c,
            files,
            csv,
            poc_bus,
            poc_kv,
            grid_machine,
            grid_mbase_mva,
            plant_branch,
            plant_machine,
            f0_hz,
            plant_mbase_mva,
            status_machines,
            sidecar,
            plant_machines,
        )
        outputs = {csv, sidecar} if polled else {csv}
        check_script(text, {*files.values(), *outputs}, c.channels)
        name = f"run_{c.case_id}.py"
        with open(
            os.path.join(pack_dir, name), "w", encoding="utf-8", newline="\n"
        ) as fh:
            fh.write(text)
        written[c.case_id] = name
    return written
