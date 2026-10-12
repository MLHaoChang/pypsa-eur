"""On-load tap changers set per critical hour and case (plan C15).

Where a campus voltage is out of its band, the study sets the taps of the
campus transformers' on-load tap changers (OLTC) to bring it back, and
reports the taps it used, or why they could not.

**Which taps move.** A transformer group (``campus_flow.trafo_groups``: the
units between the same two buses) is searched when every one of its units
has a tap block (plan C13) with ``changer: on_load``. Parallel units move
together (master-follower): the group has one position, an offset ``o`` from
neutral, and every unit sits at its own ``neutral + o``. The group's range
is the offsets every unit can reach. The group starts at its first unit's
(the master's) offset in the file. Nothing else moves:

* an ``off_circuit`` tap stays at the file's ``tap.pos`` (owner, 2026-10-12);
* a group with a unit without tap data, or with an off-circuit unit, has no
  on-load tap data as a group, and stays as the file has it;
* the transformers at the PCC (``hv_bus`` the PCC) stay at the file's taps
  when the study gives them to the grid operator (``pcc_tap_control =
  "grid_operator"``; the default ``"campus"`` searches them).

**The objective** (owner): every campus voltage in its band; among equal
excursions, the fewest steps from neutral. No money is put on a tap move.
The PCC voltage is never sent to the taps: in this model it is the grid's
set-point (``ext_grid``), which no campus measure moves.

**The search** (``Taps.search``), on a solved hour and case with its Q
dispatch held:

1. Nothing moves unless a campus voltage is out of its band.
2. The groups are visited top-down from the PCC (breadth first by the depth
   of their HV bus), upstream before downstream, as the usual time grading
   does.
3. A group whose buses are all in band does not move. Otherwise, from its
   current offset, it walks one step at a time in the direction that
   lowers ``(zone excursion, fed excursion, |offset|)``, compared in that
   order, and stops when a step lowers it no further or at its range end.
   The *fed* buses are every bus fed through the group (the LV side of it,
   the PCC never); its *zone* is those not fed through a searched group
   further down. The zone comes first so that an upstream changer holds
   its own busbars and leaves the buses below a downstream changer to it,
   helping them only as far as its own zone allows. ``|offset|`` last is the
   neutral tie-break: among equal excursions the walk ends nearest neutral.
4. A position that does not converge counts as an infinite excursion.

**Cases** (owner): taps are set per hour **and per case**. An N-1 case is
the steady state after the outage once the tap changers have acted
(minutes), from the intact case's taps, with the site's Q set-points of the
intact case unchanged. This departs from C8's "nothing re-dispatched in
N-1" for the taps only.

**The C8 dispatch with taps** (``Taps.settle``): the reactive dispatch comes
first, as today (``reactive_need(residual=False)``: inverters, STATCOMs,
steps, to the PCC Q band). If a campus voltage is then out of band, the
taps are searched with that Q held, and the dispatch is re-run with the
new taps (the transformers' reactive losses move). This repeats until the
taps do not change, for at most ``TAP_ROUNDS`` tap changes. If they are
still moving after that (hunting), the round with the smallest worst
excursion is kept (the first on a tie) and the hour is flagged. An hour
whose voltages pass keeps its file taps and gives exactly the numbers it
gave before taps were modelled.

**Failures** (``Taps.diagnose``) name, for the worst bus still out of band,
every group upstream of it, nearest first: "range exhausted: TR1+TR2 at +9
(max), MV2 0.893 pu", "TR3 has no on-load tap data", "TR1's taps are the
grid operator's (pcc_tap_control)", or a group not at its range end that
a further step does not help.

Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.schema.transformer import ratio as tap_ratio
from gridspine.static.campus_flow import apply_hour, apply_setpoints, trafo_groups
from gridspine.static.campus_reactive import reactive_need
from gridspine.templates.grid_codes import band_for

#: The most tap changes per hour before the dispatch is called hunting.
TAP_ROUNDS = 3
PCC_TAP_CONTROL = ("campus", "grid_operator")
#: A voltage further outside its band than this (pu) is out of band, as in
#: the compliance report.
V_TOL = 1e-9
#: A step must lower the excursion by more than this (pu) to be taken.
STEP_TOL = 1e-12
TAP_COLUMNS = ["period", "hour", "case", "transformer", "group", "tap_pos", "ratio_pct", "moved_by"]


def voltage_bands(campus, profile) -> dict:
    """``{bus: (v_min, v_max)}`` for every campus bus but the PCC: the
    profile's ``campus_voltage`` band, else the code band for the bus's
    voltage (as the compliance report judges them)."""
    net = campus.net
    pcc = int(net.ext_grid["bus"].iloc[0])
    own = profile.get("campus_voltage")
    out = {}
    for i in net.bus.index:
        if i == pcc:
            continue
        band = own or band_for(profile, float(net.bus.at[i, "vn_kv"]))
        out[str(net.bus.at[i, "name"])] = (float(band["v_min"]), float(band["v_max"]))
    return out


def excursion(vm: dict, bands: dict, buses=None) -> float:
    """The worst distance outside its band (pu, 0 when every bus is in
    band) of ``buses`` (default every banded bus), from ``{bus: vm_pu}``."""
    worst = 0.0
    for b in (bands if buses is None else buses):
        lo, hi = bands[b]
        v = vm[b]
        worst = max(worst, v - hi, lo - v)
    return worst


@dataclasses.dataclass(frozen=True)
class Group:
    """A transformer group as the tap search sees it (module docstring)."""
    name: str
    members: tuple
    pcc: bool
    oltc: bool                   # every unit with an on-load tap changer
    controlled: bool             # searched: oltc, and not the grid operator's
    lo: int                      # the offsets from neutral every unit reaches
    hi: int
    neutral: tuple               # per member
    start: int                   # the master's offset in the file
    depth: int                   # of the HV bus, from the PCC
    fed: frozenset               # buses fed through the group (no PCC)
    zone: frozenset = frozenset()


def _graph(net):
    adj = {int(i): set() for i in net.bus.index}
    for tbl, a, b in (("line", "from_bus", "to_bus"), ("trafo", "hv_bus", "lv_bus")):
        for i in net[tbl].index:
            x, y = int(net[tbl].at[i, a]), int(net[tbl].at[i, b])
            adj[x].add((y, tbl, i))
            adj[y].add((x, tbl, i))
    return adj


def _reach(adj, start, skip):
    seen, todo = {start}, [start]
    while todo:
        x = todo.pop()
        for y, tbl, i in adj[x]:
            if (tbl, i) in skip or y in seen:
                continue
            seen.add(y)
            todo.append(y)
    return seen


def _depths(adj, root):
    depth, frontier = {root: 0}, [root]
    while frontier:
        nxt = []
        for x in frontier:
            for y, _, _ in adj[x]:
                if y not in depth:
                    depth[y] = depth[x] + 1
                    nxt.append(y)
        frontier = nxt
    return depth


class Taps:
    """The tap rule for one built campus (module docstring). ``profile`` is
    the grid-code profile the voltages are judged against."""

    def __init__(self, campus, profile, pcc_tap_control: str = "campus"):
        if pcc_tap_control not in PCC_TAP_CONTROL:
            raise ContractError(f"pcc_tap_control is {pcc_tap_control!r}; allowed {list(PCC_TAP_CONTROL)}")
        self.control = pcc_tap_control
        net = campus.net
        self.bands = voltage_bands(campus, profile)
        self.data = dict(getattr(campus, "taps", {}) or {})
        self.idx = dict(zip(net.trafo["name"].astype(str), net.trafo.index))
        name_of = dict(zip(net.bus.index, net.bus["name"].astype(str)))
        pcc = int(net.ext_grid["bus"].iloc[0])
        adj = _graph(net)
        depth = _depths(adj, pcc)
        groups = []
        for gname, members in trafo_groups(campus).items():
            rows = [self.idx[m] for m in members]
            hv = int(net.trafo.at[rows[0], "hv_bus"])
            lv = int(net.trafo.at[rows[0], "lv_bus"])
            fed = {name_of[b] for b in _reach(adj, lv, {("trafo", i) for i in rows}) if b != pcc}
            taps = [self.data.get(m) for m in members]
            oltc = all(tp is not None and tp["changer"] == "on_load" for tp in taps)
            is_pcc = hv == pcc
            lo = max((tp["min"] - tp["neutral"] for tp in taps), default=0) if oltc else 0
            hi = min((tp["max"] - tp["neutral"] for tp in taps), default=0) if oltc else 0
            groups.append(Group(
                gname, tuple(members), is_pcc, oltc,
                oltc and not (is_pcc and pcc_tap_control == "grid_operator"), lo, hi,
                tuple(tp["neutral"] if tp else 0 for tp in taps),
                taps[0]["pos"] - taps[0]["neutral"] if oltc else 0,
                depth.get(hv, math.inf), frozenset(fed)))
        groups.sort(key=lambda g: (g.depth, g.name))
        searched = [g for g in groups if g.controlled]
        self.groups = []
        for g in groups:
            below = set().union(*(h.fed for h in searched if h is not g and h.fed < g.fed)) if searched else set()
            self.groups.append(dataclasses.replace(g, zone=frozenset(g.fed - below)))
        self.searched = [g for g in self.groups if g.controlled]
        self.file_pos = {m: tp["pos"] for m, tp in self.data.items()}

    @property
    def active(self) -> bool:
        """Some group can be searched: otherwise the study runs as before."""
        return bool(self.searched)

    # ------------------------------------------------------------------
    # positions on a net
    # ------------------------------------------------------------------

    def offset(self, net, g) -> int:
        return int(net.trafo.at[self.idx[g.members[0]], "tap_pos"]) - g.neutral[0]

    def put(self, net, g, o) -> None:
        for m, n0 in zip(g.members, g.neutral):
            net.trafo.at[self.idx[m], "tap_pos"] = n0 + o

    def positions(self, net) -> dict:
        """``{transformer: tap_pos}`` of every unit the search may move."""
        return {m: int(net.trafo.at[self.idx[m], "tap_pos"]) for g in self.searched for m in g.members}

    def _vm(self, net):
        try:
            pp.runpp(net)
        except pp.LoadflowNotConverged:
            return None
        return dict(zip(net.bus["name"].astype(str), net.res_bus["vm_pu"].astype(float)))

    def out_of_band(self, net) -> float:
        """Solve ``net`` and return the campus's worst excursion (inf when
        it does not converge)."""
        vm = self._vm(net)
        return math.inf if vm is None else excursion(vm, self.bands)

    # ------------------------------------------------------------------
    # the search
    # ------------------------------------------------------------------

    def search(self, net) -> bool:
        """Set the searched groups' taps on ``net`` (its hour, Q dispatch and
        case already in place), top-down (module docstring). Returns True
        when a tap moved. ``net`` ends at the chosen taps, solved there."""
        if not self.searched:
            return False
        vm = self._vm(net)
        if vm is not None and excursion(vm, self.bands) <= V_TOL:
            return False
        moved = False
        for g in self.searched:
            def key(o, vm=None):
                if vm is None:
                    self.put(net, g, o)
                    vm = self._vm(net)
                if vm is None:
                    return (math.inf, math.inf, abs(o))
                return (excursion(vm, self.bands, g.zone), excursion(vm, self.bands, g.fed), abs(o))

            o = self.offset(net, g)
            now = key(o, vm) if vm is not None else (math.inf, math.inf, abs(o))
            if now[1] <= V_TOL:
                continue
            best = None
            for d in (+1, -1):
                if g.lo <= o + d <= g.hi:
                    k = key(o + d)
                    if _better(k, now) and (best is None or _better(k, best[1])):
                        best = (d, k)
            if best is not None:
                d, now = best
                o += d
                while g.lo <= o + d <= g.hi:
                    k = key(o + d)
                    if not _better(k, now):
                        break
                    o, now = o + d, k
                moved = True
            self.put(net, g, o)
            vm = self._vm(net)
        return moved

    def hook(self, search_intact: bool, log: dict):
        """A ``solve_cases(retap=...)`` hook: it searches each case (the
        intact one only with ``search_intact``) and records the positions
        of every unit with tap data in service in ``log[case]``."""
        def retap(case, net):
            if case != "intact" or search_intact:
                self.search(net)
            on = net.trafo["in_service"].astype(bool)
            log[case] = {m: int(net.trafo.at[i, "tap_pos"]) for m, i in self.idx.items()
                         if m in self.data and bool(on.at[i])}
        return retap

    # ------------------------------------------------------------------
    # the C8 dispatch, rounds of Q and taps
    # ------------------------------------------------------------------

    def at(self, campus, taps: dict):
        """A copy of ``campus`` with its transformers at ``taps``."""
        work = copy.copy(campus)
        work.net = copy.deepcopy(campus.net)
        for m, pos in taps.items():
            work.net.trafo.at[self.idx[m], "tap_pos"] = pos
        return work

    def settle(self, campus, rows, req):
        """The C8 dispatch of one hour with the taps (module docstring):
        ``(ReactiveResult, hunted)``, its ``taps`` the intact case's moved
        units."""
        def dispatch(taps):
            return reactive_need(self.at(campus, taps) if taps else campus, rows, req, residual=False)

        def solve(r, taps):
            work = copy.deepcopy(campus.net)
            hour = copy.copy(campus)
            hour.net = work
            apply_hour(hour, rows)
            apply_setpoints(work, {**r.setpoints, "trafo_tap": taps})
            return work, self.out_of_band(work)

        def search(net):
            self.search(net)
            return self.positions(net)

        r, taps, hunted = tap_rounds(dispatch, solve, search, dict(self.positions(campus.net)))
        r.taps = {m: p for m, p in taps.items() if p != self.file_pos.get(m)}
        return r, hunted

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    def rows(self, key, log: dict) -> list:
        """The ``campus_invest_taps.csv`` rows of one hour from a hook's log."""
        period, hour = key
        group_of = {m: g.name for g in self.groups for m in g.members}
        out = []
        for case, pos in log.items():
            for m, p in pos.items():
                tp = self.data[m]
                out.append({"period": int(period), "hour": int(hour), "case": case, "transformer": m,
                            "group": group_of[m], "tap_pos": int(p),
                            "ratio_pct": 100.0 * tap_ratio(1.0, 1.0, tp, p - tp["neutral"]),
                            "moved_by": int(p) - int(tp["pos"])})
        return out

    def worst(self, bus: pd.DataFrame):
        """The worst campus reading of a study's bus table (``period``,
        ``hour``, ``case``, ``bus``, ``vm_pu``): the largest excursion, or
        else the reading closest to its band edge, as the compliance
        report picks it. ``None`` without a campus bus."""
        best = None
        for r in bus.itertuples(index=False):
            if r.bus not in self.bands:
                continue
            lo, hi = self.bands[r.bus]
            k = max(float(r.vm_pu) - hi, lo - float(r.vm_pu))
            if best is None or k > best[0]:
                best = (k, (int(r.period), int(r.hour)), str(r.case), str(r.bus), float(r.vm_pu))
        return best

    def diagnose(self, bus: pd.DataFrame, taps: pd.DataFrame) -> str:
        """Why the worst campus voltage is still out of band after the taps
        (module docstring), from a study's bus and tap tables."""
        w = self.worst(bus)
        if w is None or w[0] <= V_TOL:
            return "every campus voltage is in band"
        _, key, case, b, vm = w
        here = taps[(taps["period"] == key[0]) & (taps["hour"] == key[1]) & (taps["case"] == case)] \
            if len(taps) else taps
        pos = dict(zip(here["transformer"], here["tap_pos"])) if len(here) else {}
        up = sorted((g for g in self.groups if b in g.fed), key=lambda g: (-g.depth, g.name))
        where = f"{b} {vm:.3f} pu at {key} ({case})"
        if not up:
            return f"no transformer feeds {b}; {where}"
        said = []
        for g in up:
            if not g.oltc:
                said.append(f"{g.name} has no on-load tap data")
            elif not g.controlled:
                said.append(f"{g.name}'s taps are the grid operator's (pcc_tap_control)")
            else:
                master = g.members[0]
                p = pos.get(master, self.file_pos[master])
                o = p - g.neutral[0]
                end = " (max)" if o >= g.hi else " (min)" if o <= g.lo else ""
                said.append(f"range exhausted: {g.name} at {p:+d}{end}" if end else
                            f"{g.name} at {p:+d}: a further step does not reduce the excursion")
        return "; ".join(said) + f"; {where}"


def compliance_note(tapper, bus: pd.DataFrame, taps: pd.DataFrame, hunted=()) -> str:
    """What the compliance detail adds about the taps (plan C15): the
    semantics, the searched groups' taps at the worst campus reading and
    the hours still hunting. Empty when no tap moved and none hunted, so a
    study whose voltages pass reads as it did before taps were modelled."""
    if tapper is None or not tapper.active or not (len(taps) and (taps["moved_by"] != 0).any() or hunted):
        return ""
    out = ["taps set per hour and case after the on-load tap changers act, the Q dispatch held in N-1"]
    w = tapper.worst(bus)
    if w is not None:
        _, key, case, _, _ = w
        here = taps[(taps["period"] == key[0]) & (taps["hour"] == key[1]) & (taps["case"] == case)]
        pos = dict(zip(here["transformer"], here["tap_pos"]))
        said = [f"{g.name} {pos[g.members[0]]:+d}" for g in tapper.searched if g.members[0] in pos]
        if said:
            out.append(f"taps at {key} ({case}): " + ", ".join(said))
    if hunted:
        out.append(f"taps still moving after {TAP_ROUNDS} rounds at {sorted(hunted)}: the round with the smallest "
                   "excursion kept")
    return "; ".join(out)


def _better(a, b) -> bool:
    """``a`` lowers the walk's key below ``b`` (compared in order; an
    excursion must drop by more than ``STEP_TOL``)."""
    for x, y in zip(a[:2], b[:2]):
        if x < y - STEP_TOL:
            return True
        if x > y + STEP_TOL:
            return False
    return a[2] < b[2]


def tap_rounds(dispatch, solve, search, taps: dict, rounds: int = TAP_ROUNDS):
    """The rounds of Q and taps (module docstring), with the steps given:
    ``dispatch(taps) -> r`` (the Q dispatch at those taps), ``solve(r, taps)
    -> (net, excursion)``, ``search(net) -> taps`` (the search with that Q
    held). Returns ``(r, taps, hunted)``: the last state when the taps
    settle, else the state with the smallest excursion (the first on a tie)
    and ``hunted`` True."""
    r = dispatch(taps)
    states = []
    for k in range(rounds + 1):
        if not r.converged:
            return r, taps, False
        net, exc = solve(r, taps)
        states.append((exc, r, taps))
        if exc <= V_TOL:
            return r, taps, False
        new = search(net)
        if new == taps:
            return r, taps, False
        if k == rounds:
            break
        taps = new
        r = dispatch(taps)
    exc, r, taps = min(states, key=lambda s: s[0])
    return r, taps, True


def tap_table(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=TAP_COLUMNS)
