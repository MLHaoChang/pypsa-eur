"""
The decision report as printable HTML (plan S7; review v1 S6 stored XSS, N5).

Jinja2 with ``Environment(autoescape=True)`` over
``backend/templates/decision_report.html.j2``: a study name, a ledger source
or a tariff sentence is user text and is ESCAPED wherever it appears (the
template has no ``|safe``). The route serves the page with
``Content-Security-Policy: sandbox`` and a ``Content-Disposition`` from
``services/http_filenames.py::content_disposition`` as a second fence.

Charts are PNGs from ``services/study/report_charts.py`` (matplotlib
``Agg``), embedded as ``data:`` URIs so the file stands alone. Prose goes
through :func:`services.study.report.validate_prose` first; a paragraph it
refuses fails the render with the section and paragraph named.
"""
from __future__ import annotations

import base64
import pathlib

from models.study import DecisionReport, Figure
from services.study import report as R

__all__ = ["TEMPLATE", "environment", "render_html"]

TEMPLATE = "decision_report.html.j2"
TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[2] / "templates"


def environment():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    # S7 [S6]: autoescape ON — the one line the XSS test pins.
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True,
                       undefined=StrictUndefined)


def num(value, digits: int = 0) -> str:
    """A table cell: thousands separators, empty for a null."""
    if value is None:
        return ""
    v = float(value)
    if digits:
        return f"{v:,.{digits}g}" if abs(v) < 1e4 else f"{v:,.0f}"
    return f"{v:,.0f}"


def pct(value) -> str:
    return "" if value is None else f"{float(value) * 100:.1f} %"


def provenance(fig: Figure) -> str:
    parts = [fig.engine]
    if fig.fidelity is not None:
        parts.append(fig.fidelity.value)
    if fig.basis is not None:
        b = fig.basis
        parts.append(f"{b.terms.value}, {'pre-tax' if b.tax == 'pre' else 'post-tax'}, "
                     f"{'without' if b.subsidy == 'excl' else 'with'} subsidy")
    if fig.currency_year is not None:
        parts.append(f"currency year {fig.currency_year}")
    if fig.unavailable:
        parts.append(f"not established: {fig.unavailable}")
    return "; ".join(parts)


def render_html(report: DecisionReport, charts: dict[str, bytes] | None = None, *,
                stale: bool | None = None, stale_reasons: list[str] | None = None) -> str:
    """The page. ``stale``/``stale_reasons`` default to the report's own."""
    prose = R.validate_prose(report)
    if charts is None:
        from services.study import report_charts

        charts = report_charts.render_all(report)
    encoded = {k: base64.b64encode(v).decode("ascii") for k, v in charts.items()}
    help_map = dict(report.honesty_help)

    def help_text(code: str) -> str:
        return help_map.get(code) or R.help_for(code)[0]

    tpl = environment().get_template(TEMPLATE)
    return tpl.render(
        report=report, sections=R.SECTIONS, prose=prose, charts=encoded,
        stale=report.stale if stale is None else stale,
        stale_reasons=report.stale_reasons if stale_reasons is None else stale_reasons,
        fmt=R.format_fact, prov=provenance, num=num, pct=pct, help=help_text)
