"""
URDB rate → `Tariff` (Edge Investment Case P2 WP2.4b-i).

`urdb_to_tariff(urdb_response, *, name, cyclic_year=False, accept_partial=False,
tariff_id=None, jurisdiction=None, valid_from=None)` returns
`(tariff, refusals, notes)`:

  * `refusals` — `[{"field", "reason"}]`, one per URDB field the importer
    cannot map. A field is refused by name and never silently dropped. With
    refusals and no `accept_partial`, `UrdbRefused` is raised (the route's
    422). With `accept_partial`, the tariff records the refused field names in
    `Tariff.unsupported_fields`, and the engine flags `tariff_incomplete` with
    `total = None`. Nothing mappable at all is always refused.
  * `notes` — disclosures for the Library item's meta
    (`demandwindow_absent_assumed_15min`, `cyclic_year_set_by_importer`, …).

Mapping (URDB field → model):

  * `energyratestructure` + `energyweekdayschedule` / `energyweekendschedule`
    (12 × 24 period indices, JSON arrays or JSON strings) → item `energy`,
    settlement `"h"` (the URDB grid is hourly). Each URDB period `k` is one
    period NAME `str(k)`, made of fragments per month set × weekday set
    (Mon–Fri / Sat–Sun, merged when equal) × contiguous hours; a period that
    covers everything has no window. The rate is `rate + adj` (REopt adds
    `adj`). Tier `max` is cumulative (URDB): thresholds `[0, max_1, …]`. A
    tiered item on one catch-all period keeps its rates on the tiers;
    otherwise every period carries `tier_rates` (WP2.1a-ii). The model holds
    one threshold list per item, so periods whose tier `max` or tier count
    differ are refused (`energyratestructure.max`).
  * `demandratestructure` + demand schedules → a TOU demand item (`demand`,
    or `demand_tou` beside a facility item), mapped the same way.
  * `flatdemandstructure` + `flatdemandmonths` → the facility demand item
    `demand`, period name `facility`. Months with equal rates form one period;
    all months equal is one catch-all period.
  * `demandwindow` 15 / 30 / 60 → settlement `15min` / `30min` / `h`. Absent
    → `15min` with the note `demandwindow_absent_assumed_15min` (REopt
    ignores the field). Any other value is refused.
  * Fixed charges: `fixedmonthlycharge` first. Otherwise
    `fixedchargefirstmeter` with `fixedchargeunits`:
    `$/month` → `per_month`; `$/day` → `per_day`; `$/year` → `per_month` at
    1/12, as REopt does. Absent units are taken as `$/month`, with a note.
  * `lookbackpercent` (> 0) with `lookbackrange` (range mode, `cyclic_year`
    as asked, disclosed) or `lookbackmonths` (months mode) → the facility
    item's ratchet. `lookbackpercent == 0` means no ratchet. Both set
    (`lookbackrange ≠ 0` and any month true; 12 zeros mean months mode is
    off) → refused.

Refused by name: `mincharge`, `annualmincharge`, `coincidentrate*`, demand
units other than kW, energy units other than kWh, `sell` tiers, and any other
non-empty field that is neither mapped nor metadata (`_METADATA`). A 0, an
empty value or an all-zero list is not a charge and is not refused.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from models.commercial import Ratchet, Tariff, TariffItem, TariffPeriod, Tier

WEEKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]
_SETTLEMENT = {15: "15min", 30: "30min", 60: "h"}
_TIER_KEYS = {"rate", "adj", "max", "unit", "sell"}

# Descriptive URDB fields: never a charge.
_METADATA = {
    "label", "uri", "name", "utility", "utility_name", "eiaid", "sector", "description",
    "source", "sourceparent", "startdate", "enddate", "approved", "is_default", "country",
    "supersedes", "revisions", "servicetype", "voltagecategory", "phasewiring",
    "peakkwcapacitymin", "peakkwcapacitymax", "peakkwcapacityhistory", "peakkwhusagemin",
    "peakkwhusagemax", "peakkwhusagehistory", "voltageminimum", "voltagemaximum",
    "energycomments", "demandcomments", "basicinformationcomments", "latest_update",
    "minchargeunits",   # only a companion of `mincharge`, which is refused itself
    "supercedes", "isdefault", "utility_id",   # URDB spellings seen in the wild
}
_MAPPED = {
    "energyratestructure", "energyweekdayschedule", "energyweekendschedule",
    "demandratestructure", "demandweekdayschedule", "demandweekendschedule",
    "flatdemandstructure", "flatdemandmonths", "demandwindow", "demandunits",
    "flatdemandunit", "demandrateunit", "fixedmonthlycharge", "fixedchargefirstmeter",
    "fixedchargeunits", "lookbackpercent", "lookbackrange", "lookbackmonths",
}
_REASONS = {
    "mincharge": "minimum charges are not supported",
    "annualmincharge": "annual minimum charges are not supported",
    "demandratchetpercentage": "the legacy monthly ratchet percentages are not supported "
                               "(use lookbackpercent)",
}


class UrdbRefused(ValueError):
    """The URDB rate has fields the importer cannot map (the route's 422)."""

    def __init__(self, refusals: list[dict], message: str | None = None):
        self.refusals = refusals
        fields = ", ".join(r["field"] for r in refusals) or "none"
        super().__init__(message or f"URDB fields not supported: {fields}")


def _empty(v: Any) -> bool:
    if v is None or isinstance(v, bool) and not v:
        return True
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v == 0
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple)):
        return all(_empty(x) for x in v)
    if isinstance(v, dict):
        return not v
    return False


class _Import:
    def __init__(self, urdb: dict):
        self.u = urdb
        self.refusals: list[dict] = []
        self.notes: list[str] = []
        self.pending: list[str] = []   # notes that hold only if the ratchet is attached

    def refuse(self, field: str, reason: str) -> None:
        if all(r["field"] != field for r in self.refusals):
            self.refusals.append({"field": field, "reason": reason})

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    # ── parsing ───────────────────────────────────────────────────────────

    def _json(self, field: str):
        v = self.u.get(field)
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                self.refuse(field, "not a JSON value")
                return None
        return v

    def schedule(self, field: str) -> list[list[int]] | None:
        v = self._json(field)
        ok = (isinstance(v, list) and len(v) == 12
              and all(isinstance(r, list) and len(r) == 24 for r in v)
              and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
                      for r in v for x in r))
        if not ok:
            self.refuse(field, "a schedule is 12 months × 24 hours of period indices")
            return None
        return v

    def structure(self, field: str, unit: str) -> list[list[tuple[float, float | None]]] | None:
        """Per URDB period, its tiers as (rate + adj, cumulative max or None).
        None when any part of it is refused: a partial import then misses the
        charge rather than misstating it (review M2)."""
        before = len(self.refusals)
        out = self._structure(field, unit)
        return None if len(self.refusals) > before else out

    def _structure(self, field: str, unit: str):
        v = self._json(field)
        if not isinstance(v, list) or not all(isinstance(p, list) and p for p in v):
            self.refuse(field, "a rate structure is a list of periods, each a list of tiers")
            return None
        out = []
        for per in v:
            tiers = []
            for t in per:
                if not isinstance(t, dict):
                    self.refuse(field, "a tier is an object")
                    return None
                for key in t:
                    if key not in _TIER_KEYS:
                        self.refuse(f"{field}.{key}", "tier field not supported")
                if not _empty(t.get("sell")):
                    self.refuse(f"{field}.sell", "sell rates (export credit) are not supported")
                u = t.get("unit")
                if u is not None and str(u).strip().lower() != unit.lower():
                    self.refuse(f"{field}.unit", f"only {unit} is supported, got {u!r}")
                try:
                    r = float(t.get("rate") or 0.0) + float(t.get("adj") or 0.0)
                    mx = None if t.get("max") is None else float(t["max"])
                except (TypeError, ValueError):
                    self.refuse(field, "a tier rate, adj or max is not a number")
                    return None
                tiers.append((r, mx))
            out.append(tiers)
        return out

    # ── building ──────────────────────────────────────────────────────────

    @staticmethod
    def _runs(mask: list[bool]) -> list[tuple[int, int]]:
        runs, start = [], None
        for h, on in enumerate(mask + [False]):
            if on and start is None:
                start = h
            elif not on and start is not None:
                runs.append((start, h))
                start = None
        return runs

    def fragments(self, wd, we, k: int) -> list[dict]:
        """The windows of URDB period `k`: month groups with one daily
        pattern, weekday / weekend sets (merged when equal), hour runs."""
        groups: dict[tuple, list[int]] = {}
        for m in range(12):
            key = (tuple(x == k for x in wd[m]), tuple(x == k for x in we[m]))
            groups.setdefault(key, []).append(m + 1)
        out = []
        for (wmask, emask), months in groups.items():
            if not any(wmask) and not any(emask):
                continue
            days = [([], list(wmask))] if wmask == emask else \
                [(WEEKDAYS, list(wmask)), (WEEKEND, list(emask))]
            for weekdays, mask in days:
                for s, e in self._runs(mask):
                    frag = {"months": [] if len(months) == 12 else months,
                            "weekdays": weekdays}
                    if (s, e) != (0, 24):
                        frag.update(start_hour=s, end_hour=e)
                    out.append(frag)
        return out

    def thresholds(self, field: str, tiered: list[list[tuple]]) -> list[float] | None:
        """One cumulative threshold list shared by `tiered` periods, or None
        (refused) when their tier count or `max` differ."""
        counts = {len(t) for t in tiered}
        maxes = {tuple(mx for _, mx in t[:-1]) for t in tiered}
        if len(counts) != 1 or len(maxes) != 1:
            self.refuse(f"{field}.max", "periods whose tier max or tier count differ cannot "
                                        "share one threshold list")
            return None
        (mx,) = maxes
        if any(x is None for x in mx) or any(b <= a for a, b in zip((0.0,) + mx, mx)):
            self.refuse(f"{field}.max", "every tier but the last needs a rising cumulative max")
            return None
        if any(t[-1][1] is not None for t in tiered):
            self.note(f"{field}.last_tier_max_ignored")
        return [0.0, *mx]

    def item(self, item_id: str, kind: str, unit: str, settlement: str, field: str,
             windows: list[tuple[str, list[dict], list[tuple]]]) -> TariffItem | None:
        """`windows`: (period name, fragments, tiers) per URDB period."""
        tiered = [t for _, _, t in windows]
        multi = any(len(t) > 1 for t in tiered)
        th = self.thresholds(field, tiered) if multi else None
        if multi and th is None:
            return None
        periods = []
        for name, frags, tiers in windows:
            for f in frags:
                p = {"name": name, "rate": 0.0 if multi else tiers[0][0], **f}
                if multi:
                    p["tier_rates"] = [r for r, _ in tiers]
                periods.append(p)
        body: dict = {"id": item_id, "kind": kind, "unit": unit, "settlement": settlement,
                      "periods": periods}
        catch_all = len(periods) == 1 and not periods[0]["months"] \
            and not periods[0]["weekdays"] and "start_hour" not in periods[0]
        if multi:
            if catch_all:
                (p,) = periods
                body["tiers"] = [Tier(threshold=x, rate=r)
                                 for x, (r, _) in zip(th, windows[0][2])]
                del p["tier_rates"]
            else:
                body["tiers"] = [Tier(threshold=x, rate=0.0) for x in th]
        body["periods"] = [TariffPeriod(**p) for p in periods]
        try:
            return TariffItem(**body)
        except ValidationError as exc:
            self.refuse(field, f"not representable: {exc.errors()[0]['msg']}")
            return None

    def tou(self, prefix: str, item_id: str, kind: str, unit: str, settlement: str,
            unit_word: str) -> TariffItem | None:
        field = f"{prefix}ratestructure"
        struct = self.structure(field, unit_word)
        wd = self.schedule(f"{prefix}weekdayschedule")
        we = self.schedule(f"{prefix}weekendschedule")
        if struct is None or wd is None or we is None:
            return None
        used = sorted({x for r in wd + we for x in r})
        if any(k >= len(struct) for k in used):
            self.refuse(field, f"the schedules name periods beyond the {len(struct)} of {field}")
            return None
        windows = [(str(k), self.fragments(wd, we, k), struct[k]) for k in used]
        return self.item(item_id, kind, unit, settlement, field, windows)

    def facility(self, settlement: str) -> TariffItem | None:
        field = "flatdemandstructure"
        struct = self.structure(field, "kW")
        months = self._json("flatdemandmonths")
        if struct is None:
            return None
        if not (isinstance(months, list) and len(months) == 12
                and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < len(struct)
                        for x in months)):
            self.refuse("flatdemandmonths", "12 period indices into flatdemandstructure")
            return None
        groups: dict[tuple, list[int]] = {}
        for m, j in enumerate(months):
            groups.setdefault(tuple(struct[j]), []).append(m + 1)
        windows = []
        for tiers, ms in groups.items():
            frag = {"months": [] if len(ms) == 12 else ms, "weekdays": []}
            windows.append(("facility", [frag], list(tiers)))
        return self.item("demand", "demand", "per_kw_month", settlement, field, windows)

    def ratchet(self, cyclic_year: bool) -> Ratchet | None:
        pct = self.u.get("lookbackpercent")
        if _empty(pct):
            return None
        rng = self.u.get("lookbackrange") or 0
        months = self._json("lookbackmonths") or []
        if not isinstance(months, list) or (months and len(months) != 12):
            self.refuse("lookbackmonths", "12 month flags")
            return None
        chosen = [i + 1 for i, x in enumerate(months) if not _empty(x)]
        if rng and chosen:
            self.refuse("lookbackrange/lookbackmonths", "both lookback modes are set")
            return None
        try:
            share = float(pct)
            if rng:
                if cyclic_year and int(rng) >= 12:
                    # Wrapping within the rate year, a lookback of 12 or more
                    # months reads every month: months mode over all 12. The
                    # note is recorded once the ratchet is attached (round 2 L1).
                    self.pending.append("cyclic_lookbackrange_ge_12_as_all_months")
                    return Ratchet(months=list(range(1, 13)), share=share)
                r = Ratchet(lookback_months=int(rng), share=share, cyclic_year=cyclic_year)
                if cyclic_year:
                    self.pending.append("cyclic_year_set_by_importer")
                return r
            if chosen:
                return Ratchet(months=chosen, share=share)
        except (TypeError, ValueError, ValidationError) as exc:
            msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            self.refuse("lookbackrange" if rng else "lookbackpercent",
                        f"not representable: {msg}")
            return None
        self.refuse("lookbackpercent", "a lookback percent with neither lookbackrange nor "
                                       "lookbackmonths")
        return None

    def fixed(self) -> TariffItem | None:
        monthly = self.u.get("fixedmonthlycharge")
        first = self.u.get("fixedchargefirstmeter")
        if not _empty(monthly):
            unit, amount = "per_month", float(monthly)
            if not _empty(first):
                self.note("fixedchargefirstmeter_ignored_fixedmonthlycharge_used")
        elif not _empty(first):
            units = self.u.get("fixedchargeunits")
            if _empty(units):
                units = "$/month"
                self.note("fixedchargeunits_absent_assumed_per_month")
            u = str(units).strip().lower()
            if u == "$/month":
                unit, amount = "per_month", float(first)
            elif u == "$/day":
                unit, amount = "per_day", float(first)
                # REopt bills $/day × 30.4375 per month; the engine bills the
                # days covered (a documented deviation).
                self.note("fixed_per_day_billed_on_covered_days")
            elif u == "$/year":
                unit, amount = "per_month", float(first) / 12.0
            else:
                self.refuse("fixedchargeunits", f"{units!r} is not $/month, $/day or $/year")
                return None
        else:
            return None
        return TariffItem(id="fixed", kind="fixed", unit=unit,
                          periods=[TariffPeriod(name="all", rate=amount)])


def _date(epoch) -> date | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def urdb_to_tariff(urdb_response: dict, *, name: str, cyclic_year: bool = False,
                   accept_partial: bool = False, tariff_id: str | None = None,
                   jurisdiction: str | None = None, valid_from: date | None = None
                   ) -> tuple[Tariff, list[dict], list[str]]:
    if not isinstance(urdb_response, dict):
        raise UrdbRefused([{"field": "urdb_response", "reason": "not a JSON object"}])
    imp = _Import(urdb_response)
    u = urdb_response
    for field, v in u.items():
        if field in _MAPPED or field in _METADATA or _empty(v):
            continue
        if field.startswith("coincident"):
            imp.refuse(field, "coincident demand charges are not supported")
        else:
            imp.refuse(field, _REASONS.get(field, "not mapped by the importer"))
    demand_ok = True
    for field in ("demandunits", "flatdemandunit", "demandrateunit"):
        if not _empty(u.get(field)) and str(u[field]).strip().lower() != "kw":
            imp.refuse(field, f"only kW demand is supported, got {u[field]!r}")
            demand_ok = False   # billed as kW it would be misstated (review M2)

    has_tou_demand = demand_ok and not _empty(u.get("demandratestructure"))
    has_facility = demand_ok and not _empty(u.get("flatdemandstructure"))
    settlement = "15min"
    if has_tou_demand or has_facility:
        window = u.get("demandwindow")
        if _empty(window):
            imp.note("demandwindow_absent_assumed_15min")
        elif isinstance(window, (int, float)) and int(window) == window \
                and int(window) in _SETTLEMENT:
            settlement = _SETTLEMENT[int(window)]
        else:
            imp.refuse("demandwindow", f"{window!r} is not 15, 30 or 60 minutes")

    items: list[TariffItem] = []
    if not _empty(u.get("energyratestructure")):
        energy = imp.tou("energy", "energy", "energy", "per_kwh", "h", "kWh")
        if energy is not None:
            items.append(energy)
    if has_tou_demand:
        tou = imp.tou("demand", "demand_tou" if has_facility else "demand", "demand",
                      "per_kw_month", settlement, "kW")
        if tou is not None:
            items.append(tou)
    facility = imp.facility(settlement) if has_facility else None
    ratchet = imp.ratchet(cyclic_year)
    if ratchet is not None and facility is not None:
        # The engine's rule (`tariff_engine._charged`): a window is free when
        # every effective rate of it is 0 — its `tier_rates`, else the item's
        # tier rates, else its rate (round 2 M1a/M1b).
        def effective(p) -> list[float]:
            if p.tier_rates is not None:
                return list(p.tier_rates)
            return [t.rate for t in facility.tiers] if facility.tiers else [p.rate]

        free = sorted({m for p in facility.periods if all(r == 0 for r in effective(p))
                       for m in (p.months or range(1, 13))})
        reads = (set(ratchet.months) if ratchet.months is not None else set(range(1, 13)))
        if free and reads & set(free):
            # REopt's ratchet reads every month's actual peak; the engine's
            # reads charged months only, so the import would under-bill
            # silently (review M1).
            imp.refuse("lookbackpercent", f"the lookback reads facility months {free} whose "
                                          "rate is 0; the ratchet here reads charged months "
                                          "only")
            ratchet = None
    if ratchet is not None:
        if facility is None:
            imp.refuse("lookbackpercent", "a ratchet needs the facility demand item "
                                          "(flatdemandstructure)")
        else:
            facility = facility.model_copy(update={"ratchet": ratchet})
            facility = TariffItem.model_validate(facility.model_dump())
            for note in imp.pending:
                imp.note(note)
    if facility is not None:
        items.append(facility)
    fixed = imp.fixed()
    if fixed is not None:
        items.append(fixed)

    if not items:
        raise UrdbRefused(imp.refusals or [{"field": "urdb_response",
                                            "reason": "no charge the importer can map"}],
                          "nothing in the URDB rate can be imported")
    if imp.refusals and not accept_partial:
        raise UrdbRefused(imp.refusals)
    start = valid_from or _date(u.get("startdate"))
    if start is None:
        raise ValueError("valid_from is required: the URDB rate has no startdate")
    end = _date(u.get("enddate")) if not _empty(u.get("enddate")) else None
    if end is not None and end < start:
        # An expired URDB rate imported for a later period (review L3).
        imp.note("enddate_before_valid_from_ignored")
        end = None
    tid = tariff_id or (re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "urdb")
    tariff = Tariff(id=tid, name=name, jurisdiction=jurisdiction or "US", valid_from=start,
                    valid_to=end, items=items,
                    unsupported_fields=sorted({r["field"] for r in imp.refusals}))
    return tariff, imp.refusals, imp.notes
