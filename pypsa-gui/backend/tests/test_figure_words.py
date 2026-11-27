"""guided-results 04: one name, unit and basis per result figure.

`services/figure_words.json` is the one source. These tests pin every backend
surface that prints one of the figures to it (GK B (72) conditions 1, 3, 4, 6),
and the loader to its closed schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services import figure_words as FW
from services.reports import chapters, evidence as E
from services.reports import formatting as F
from tests.test_chat_tools_investment_case import _report as _ic_report

BACKEND = Path(__file__).resolve().parent.parent

# ── the source and its loader ───────────────────────────────────────────────


def test_the_rulings_are_the_words():
    assert (
        FW.name("project_npv") == "Project NPV at the WACC, after tax (unlevered)"
    )  # GK B (63)
    assert (
        FW.label("expected_shortfall") == "Expected shortfall (h/yr)"
    )  # GR04-SHORTFALL-1
    assert FW.tag("expected_shortfall") == "LOLE"
    assert "LOLE" not in FW.name("expected_shortfall")
    assert FW.unit("screening_shortfall") == "h"  # a horizon figure, not per year
    assert FW.tag("screening_shortfall") == "LOLE" and "LOLE" not in FW.name(
        "screening_shortfall"
    )
    assert FW.name("lcoe_real") == "LCOE (real)"
    assert FW.name("lcoe_nominal") == "LCOE (finance-consistent, nominal)"


def test_every_name_is_its_own():
    names = [f["name"] for f in FW.FIGURES.values()]
    assert len(names) == len(set(names))


def test_units_and_bases_take_the_report_currency_and_never_a_silent_euro():
    assert FW.unit("lcoe_real", "USD") == "USD/MWh"
    assert FW.unit("project_npv", "USD") == "USD"
    assert FW.unit("lcoe_real") == "report currency/MWh"
    assert (
        FW.basis("lcoe_real", "USD", "2024 USD")
        == "investment case · real basis, 2024 USD"
    )
    assert "None" not in FW.basis(
        "lcoe_real", "USD"
    )  # no money words: nothing, never "None"
    # only the study's TEA figure is in euro by construction (`cost_at_target_eur`)
    euro = {
        fid for fid, f in FW.FIGURES.items() if "€" in json.dumps(f, ensure_ascii=False)
    }
    assert euro == {"study_cost_per_mwh"}


def _write(tmp_path, doc):
    path = tmp_path / "figure_words.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "bad, why",
    [
        (
            {
                "version": 1,
                "figures": {"x": {"name": "X", "unit": "h", "colour": "red"}},
            },
            "unknown keys",
        ),
        ({"version": 1, "figures": {"x": {"unit": "h"}}}, "lacks"),
        ({"version": 1, "figures": {"x": {"name": "X"}}}, "lacks"),
        (
            {"version": 1, "figures": {"x": {"name": " ", "unit": "h"}}},
            "non-empty string",
        ),
        (
            {"version": 1, "figures": {"x": {"name": 3, "unit": "h"}}},
            "non-empty string",
        ),
        (
            {"version": 1, "figures": {"x": {"name": "X", "unit": "{year}"}}},
            "placeholders",
        ),
        ({"version": 2, "figures": {"x": {"name": "X", "unit": "h"}}}, "version"),
        ({"version": 1, "figures": {}}, "non-empty"),
        ({"version": 1, "figures": {"x": "X"}}, "must be an object"),
        ({"version": 1, "figures": ["x"]}, "non-empty object"),
        (["version", "figures"], "top level"),
        (
            {"version": 1, "figures": {"x": {"name": "X", "unit": "h"}}, "extra": 1},
            "top level",
        ),
    ],
)
def test_the_loader_refuses_a_file_outside_its_schema(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        FW.load(_write(tmp_path, bad))


def test_the_loader_refuses_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        FW.load(tmp_path / "figure_words.json")


def test_the_loader_reads_a_good_file(tmp_path):
    good = {
        "version": 1,
        "figures": {"x": {"name": "X", "unit": "{currency}", "tag": "T", "basis": "b"}},
    }
    assert FW.load(_write(tmp_path, good)) == good["figures"]


# ── the reports ─────────────────────────────────────────────────────────────


def _headline(mc_lole_h):
    eh = {"archetype": "strong_grid", "mc_lole_h": mc_lole_h, "mc_eue_mwh": 999.0}
    return E.collect_evidence(study_report=None, eh_report=eh, worksheet=None)


def test_the_report_prints_the_expected_shortfall_under_its_one_name():
    ev = _headline(3.2149)
    rows = dict(map(tuple, ev.tables["headline"].rows))
    # the figure under the name is the MC LOLE in h/yr, not EUE and not a horizon figure
    assert rows[FW.label("expected_shortfall")] == F.fmt_number(
        3.2149, unit="h/yr", digits=2
    )
    assert not any(str(k).startswith("MC LOLE") for k in rows)


def test_the_studys_cost_per_mwh_is_not_called_lcoe():
    eh = {"archetype": "strong_grid", "tea": {"lcoe_eur_per_mwh": 42.0}}
    rows = dict(
        map(
            tuple,
            E.collect_evidence(study_report=None, eh_report=eh, worksheet=None)
            .tables["headline"]
            .rows,
        )
    )
    assert rows[FW.name("study_cost_per_mwh")] == F.fmt_number(
        42.0, unit="€/MWh", digits=1
    )
    assert not any("LCOE" in str(k) for k in rows if k != "LCOH")


def _investment(currency="EUR", nominal=91.25, real=77.5):
    rep = _ic_report()
    p = rep["sections"]["project"]["payload"]
    p["currency"] = currency
    rep["lcoe_finance_consistent_eur_per_mwh"] = nominal
    rep["lcoe_real_eur_per_mwh"] = real
    payload = chapters.investment_payload(rep, stale=False)
    return payload, dict(map(tuple, chapters.investment_table(payload, "x").rows))


def test_the_investment_table_names_npv_and_lcoe_as_the_one_source_does():
    payload, rows = _investment()
    assert rows[FW.name("project_npv")] == F.fmt_number(
        payload["npv_at_wacc_eur"], unit="EUR"
    )
    # the LCOE the table prints is the finance-consistent NOMINAL one, so it carries that name
    assert rows[FW.name("lcoe_nominal")] == F.fmt_number(
        91.25, unit="EUR/MWh", digits=1
    )
    assert FW.name("lcoe_real") not in rows and "LCOE" not in rows


def test_a_usd_case_prints_no_euro_and_a_missing_currency_says_report_currency():
    _payload, rows = _investment(currency="USD")
    assert "€" not in json.dumps(rows, ensure_ascii=False) and "EUR" not in json.dumps(
        rows
    )
    assert rows[FW.name("lcoe_nominal")].endswith("USD/MWh")
    _payload, rows = _investment(currency=None)
    assert "€" not in json.dumps(rows, ensure_ascii=False)
    assert rows[FW.name("lcoe_nominal")].endswith("report currency/MWh")


# ── no second copy of a name (GK B (72) cond. 6) ────────────────────────────

# A retired name may not reappear in backend code; the one source prints the figure.
RETIRED = [
    "MC LOLE (h/yr)",
    'NPV at the WACC, after tax"',
    "Unlevered NPV at WACC",
    '("LCOE",',
    '("MC LOLE",',
]
# Kept on purpose, each with its reason:
KEPT = {
    # The checked-metric labels are contract text (AA-V1-CHANGE-1): a rename is a new
    # profile version, which guided-results 04 leaves to its follow-up.
    "harness/investment_calculations.py",
    "harness/copt_calculations.py",
    "harness/pilot_evaluation.py",
    "harness/assurance_contracts.v1.json",
}


def test_no_backend_code_keeps_its_own_copy_of_a_retired_name():
    hits = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv")) or rel in KEPT:
            continue
        text = path.read_text(encoding="utf-8")
        hits += [f"{rel}: {word}" for word in RETIRED if word in text]
    assert hits == []


def test_the_reports_read_the_names_from_the_one_source():
    for mod in ("services/reports/evidence.py", "services/reports/chapters.py"):
        text = (BACKEND / mod).read_text(encoding="utf-8")
        assert "figure_words as FW" in text
        for fid in (
            "project_npv",
            "expected_shortfall",
            "lcoe_real",
            "lcoe_nominal",
            "study_cost_per_mwh",
        ):
            assert not re.search(re.escape(json.dumps(FW.name(fid))), text), (mod, fid)


# ── the chat's checked metrics (GK B (72): contract labels move only with a profile v2) ──

# Every checked metric that prints one of the figures, as an exact set: (profile, metric) → figure.
# The /2 profiles carry the ruled names (AA issue 28); the frozen /1 labels are pinned in
# tests/test_aa28_contract_labels.py.
HARNESS_FIGURES = {
    ("investment-case/2", "project_npv"): "project_npv",
    ("investment-lcoe/2", "lcoe_real"): "lcoe_real",
    ("investment-lcoe/2", "lcoe_nominal"): "lcoe_nominal",
    ("copt-screening/2", "screening_lole"): "screening_shortfall",
}


def _contract_metrics():
    from harness import copt_calculations as copt, investment_calculations as inv

    return {
        (inv.PROFILE_V2, "project_npv"): inv.METRICS_V2["project_npv"],
        (inv.LCOE_PROFILE_V2, "lcoe_real"): inv.LCOE_METRICS_V2["lcoe_real"],
        (inv.LCOE_PROFILE_V2, "lcoe_nominal"): inv.LCOE_METRICS_V2["lcoe_nominal"],
        (copt.PROFILE_V2, "screening_lole"): copt.METRICS_V2["screening_lole"],
    }


def test_every_checked_metric_of_a_figure_is_mapped_to_it():
    from harness import copt_calculations as copt, investment_calculations as inv

    words = re.compile(r"\b(NPV|LOLE|LCOE)\b")
    printed = {
        (inv.PROFILE_V2, m)
        for m, d in inv.METRICS_V2.items()
        if words.search(d["label"])
    }
    printed |= {
        (inv.LCOE_PROFILE_V2, m)
        for m, d in inv.LCOE_METRICS_V2.items()
        if words.search(d["label"])
    }
    printed |= {
        (copt.PROFILE_V2, m)
        for m, d in copt.METRICS_V2.items()
        if words.search(d["label"])
    }
    # Equity NPV is another figure (at the cost of equity); it carries none of the four names.
    assert printed - {(inv.PROFILE_V2, "equity_npv")} == set(HARNESS_FIGURES)
    assert set(_contract_metrics()) == set(HARNESS_FIGURES)


def test_each_contract_label_is_the_ruled_name():
    for key, definition in _contract_metrics().items():
        assert definition["label"] == FW.name(HARNESS_FIGURES[key]), key


# ── the money year (finance/report.py helpers, GK B (72) ruling A) ──────────


def test_the_money_year_is_the_base_year_unless_none_is_stated():
    from services.finance.report import money_year

    assert money_year(2020, 2027) == 2027
    assert money_year(2024, 2024) == 2024
    assert money_year(2024, None) == 2024
    assert money_year(None, 2027) is None


def test_the_money_words_follow_the_engine_basis():
    from types import SimpleNamespace

    from services.finance.report import basis_statement, money_words

    assert money_words("USD", 2020, 2027) == "costs in real 2027 USD (2020 price data)"
    assert money_words("USD", 2024, 2024) == "2024 USD"
    assert money_words("USD", 2024) == "2024 USD"
    assert money_words("USD", None, 2027) == "USD (currency year not stated)"
    fin = SimpleNamespace(currency="USD", currency_year=2020, price_basis="nominal")
    assert (
        basis_statement(fin, 2027)
        == "nominal basis, costs in real 2027 USD (2020 price data)"
    )
