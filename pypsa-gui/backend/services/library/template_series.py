"""
Price series a project template ships (IPP renewables + BESS ticket 02, spec §10 G3).

A template directory may carry `price_series/meta.json` and one CSV per series
(`timestamp,value`, the timestamps in UTC). At create, `mint` stores each series in the
creating org's Library with `series_store.put_series` (idempotent on content: the same
template minted twice, or into two orgs, gives the same hash), and `apply_refs` writes the
returned refs into the new project's `solver_config.json` `commercial` block at the targets
the meta names. The template's own JSON never carries a ref: a ref is org-scoped.

`meta.json`:

    {"series": [{"name": "ipp_da_base", "file": "da_base.csv",
                 "label": "...", "source": "illustrative", "vintage_year": 2030,
                 "description": "...", "notes": ["currency:EUR", ...],
                 "targets": ["commercial.export_price_ref", "contract:ppa1.reference_price"]}]}

Everything is read and checked BEFORE anything is minted (`read`), so a bad template refuses
the create before the project exists (`TemplateSeriesError`, typed). Until the series meta
gains currency, price basis, currency year, region and time basis (#448 S1), they travel as
`key:value` notes (`currency:EUR`, `price_basis:real`, ...), and every series carries the
`illustrative` note when its source says so.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pandas as pd

__all__ = ["DIR", "META", "NOTE_KEYS", "TemplateSeries", "TemplateSeriesError", "apply_refs",
           "basis_conflicts", "mint", "note_values", "read"]

DIR = "price_series"
META = "meta.json"
#: The commercial fields a template series may fill (a contract's reference by id).
TARGETS = ("commercial.export_price_ref", "commercial.import_price_ref",
           "commercial.grid_cfe_share_ref")
_CONTRACT_PREFIX = "contract:"
_CONTRACT_FIELD = ".reference_price"
_META_KEYS = ("label", "source", "vintage_year", "provider", "description", "unit", "notes")
#: The `key:value` notes that stand in for series fields until #448 S1 adds them to
#: `SeriesMeta`; S1 migrates exactly these (gatekeeper). Disclosure only: nothing reads
#: them as data except `basis_conflicts`, which checks them against the template's finance.
NOTE_KEYS = ("currency", "price_basis", "currency_year", "region", "source_time_basis")
#: Money notes a non-price series (a share) must not carry.
_MONEY_KEYS = ("currency", "price_basis", "currency_year")
_SHARE_TARGETS = ("commercial.grid_cfe_share_ref",)


class TemplateSeriesError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TemplateSeries:
    name: str
    series: pd.Series
    meta: dict
    targets: tuple[str, ...]


def note_values(meta: dict) -> dict[str, str]:
    """The `key:value` notes of NOTE_KEYS in `meta` (the rest of the notes are free text)."""
    out: dict[str, str] = {}
    for note in meta.get("notes") or []:
        key, sep, value = str(note).partition(":")
        if sep and key in NOTE_KEYS:
            out[key] = value
    return out


def basis_conflicts(series: list["TemplateSeries"], finance: dict | None) -> list[str]:
    """Series whose money basis does not fit the template's finance block, one reason each
    (gatekeeper's rule table, IPP ticket 03). With `price_basis` noted:

    * the series `currency` must equal `FinanceInputs.currency`;
    * real series, nominal case: OK when the series `currency_year` is the finance one (base-year
      money, escalated from that year: the owner's convention);
    * nominal series, nominal case: OK only for a single-year series whose year is the finance
      `currency_year` (a multi-year nominal series would be escalated a second time);
    * nominal series, real case: refused (it would need deflating, never done silently);
    * real series, real case: the `currency_year`s must match.
    A series with no money notes (a share) is not checked."""
    if not finance:
        return []
    f_basis = str(finance.get("price_basis") or "nominal")
    f_year = finance.get("currency_year")
    f_cur = str(finance.get("currency") or "EUR")
    out = []
    for ts in series:
        notes = note_values(ts.meta)
        basis = notes.get("price_basis")
        if basis is None:
            continue
        if notes.get("currency", f_cur) != f_cur:
            out.append(f"{ts.name}: currency {notes['currency']} vs finance {f_cur}")
        year = notes.get("currency_year")
        same_year = year is not None and f_year is not None and str(year) == str(f_year)
        if basis == "real":
            if not same_year:
                out.append(f"{ts.name}: real in {year} money vs finance currency_year {f_year}")
        elif basis == "nominal" and f_basis == "real":
            out.append(f"{ts.name}: a nominal series in a real case would need deflating")
        elif basis == "nominal":
            years = sorted(set(pd.DatetimeIndex(ts.series.index).year))
            if not (same_year and years == [int(f_year)]):
                out.append(f"{ts.name}: a nominal series must be the single year "
                           f"{f_year} (spans {years[0]}–{years[-1]}); it would be escalated twice")
        else:
            out.append(f"{ts.name}: price_basis {basis!r} is neither real nor nominal")
    return out


def _check_target(t: str) -> None:
    if t in TARGETS:
        return
    if t.startswith(_CONTRACT_PREFIX) and t.endswith(_CONTRACT_FIELD) and \
            len(t) > len(_CONTRACT_PREFIX) + len(_CONTRACT_FIELD):
        return
    raise TemplateSeriesError("template_series_target_unknown",
                              f"template series target {t!r} is not one a template may fill")


def read(template_dir: Path) -> list[TemplateSeries]:
    """The template's series, checked; [] when it ships none."""
    root = Path(template_dir) / DIR
    meta_path = root / META
    if not meta_path.is_file():
        return []
    try:
        entries = json.loads(meta_path.read_text())["series"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TemplateSeriesError("template_series_meta_invalid",
                                  f"{DIR}/{META} is unreadable: {exc}") from exc
    out: list[TemplateSeries] = []
    for e in entries:
        name, file = e.get("name"), e.get("file")
        if not name or not file or Path(file).name != file:
            raise TemplateSeriesError("template_series_meta_invalid",
                                      f"series entry {e!r} needs a name and a plain file name")
        targets = tuple(e.get("targets") or ())
        for t in targets:
            _check_target(t)
        try:
            df = pd.read_csv(root / file)
            idx = pd.DatetimeIndex(pd.to_datetime(df.iloc[:, 0], utc=True)).tz_convert(None)
            s = pd.Series(pd.to_numeric(df.iloc[:, 1]).to_numpy(dtype=float), index=idx,
                          name=name)
        except (OSError, ValueError, IndexError) as exc:
            raise TemplateSeriesError("template_series_file_invalid",
                                      f"{DIR}/{file} is unreadable: {exc}") from exc
        meta = {k: e[k] for k in _META_KEYS if k in e}
        share = any(t in _SHARE_TARGETS for t in targets)
        meta.setdefault("unit", "share" if share else "EUR/MWh")
        notes = list(meta.get("notes") or [])
        for note in notes:
            key, sep, _v = str(note).partition(":")
            if sep and " " not in key and key not in NOTE_KEYS:
                raise TemplateSeriesError("template_series_meta_invalid",
                                          f"series {name!r}: note key {key!r} is not one of "
                                          f"{NOTE_KEYS}")
        if share:
            money = [k for k in note_values({"notes": notes}) if k in _MONEY_KEYS]
            if meta["unit"] != "share" or money:
                raise TemplateSeriesError("template_series_meta_invalid",
                                          f"series {name!r} fills a share: unit 'share' and no "
                                          f"money notes ({money})")
        if "illustrative" in str(meta.get("source", "")).lower() and "illustrative" not in notes:
            notes.append("illustrative")
        notes.append("source_time_basis:UTC")
        meta["notes"] = notes
        out.append(TemplateSeries(name=name, series=s, meta=meta, targets=targets))
    return out


def mint(db, org_id: UUID, series: list[TemplateSeries], *, created_by: UUID | None = None,
         root: Path | None = None) -> dict[str, object]:
    """Store each series in the org's Library; {series name: PriceSeriesRef}. A series
    `put_series` refuses (not finite, unordered) raises `TemplateSeriesError`."""
    from services.library import series_store

    refs: dict[str, object] = {}
    for ts in series:
        try:
            refs[ts.name] = series_store.put_series(db, org_id, ts.name, ts.series, ts.meta,
                                                    created_by=created_by, root=root)
        except ValueError as exc:
            raise TemplateSeriesError("template_series_invalid",
                                      f"template series {ts.name!r}: {exc}") from exc
    return refs


def apply_refs(solver_config: dict, series: list[TemplateSeries], refs: dict) -> dict:
    """`solver_config` with each series' ref at its targets (a new dict). A target whose
    commercial block or contract is absent is refused: the template is inconsistent."""
    cfg = json.loads(json.dumps(solver_config))
    commercial = cfg.get("commercial")
    for ts in series:
        if not ts.targets:
            continue
        if not isinstance(commercial, dict):
            raise TemplateSeriesError("template_series_target_missing",
                                      "the template's series target a commercial block it "
                                      "does not ship")
        ref = refs[ts.name].model_dump(mode="json")
        for t in ts.targets:
            if t in TARGETS:
                commercial[t.split(".", 1)[1]] = ref
                continue
            cid = t[len(_CONTRACT_PREFIX):-len(_CONTRACT_FIELD)]
            hit = [c for c in commercial.get("contracts") or [] if c.get("id") == cid]
            if len(hit) != 1:
                raise TemplateSeriesError("template_series_target_missing",
                                          f"template series target {t!r}: no contract {cid!r}")
            hit[0]["reference_price"] = ref
    return cfg
