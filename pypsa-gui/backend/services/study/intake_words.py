"""
The decision panel's words for a study's intake steps and settings, for the chat edit card
(Q3-CARDS-1). A MIRROR of `frontend/src/utils/decisionVocabulary.ts`, the one source of
the panel's wording, kept equal by `tests/test_decision_vocabulary_parity.py` and the
frontend's `decisionVocabulary.test.ts`; edit the TS block and this table together.
"""
from __future__ import annotations

INTAKE_WORDS = {
    "steps": {"site": "Your site", "existing": "What exists today", "load": "Your consumption",
              "goal": "Your goal", "horizon": "Horizon and perspective"},
    "fields": {
        "site.zone": {"label": "Country or grid zone"},
        "site.year": {"label": "Year modelled"},
        "site.connection_mw": {"label": "Grid connection limit", "unit": "MW"},
        "site.latitude": {"label": "Latitude (for the PV profile)", "unit": "°"},
        "load.annual_mwh": {"label": "Yearly consumption", "unit": "MWh/yr"},
        "load.profile": {"label": "Sector profile"},
        "load.unit": {"label": "The file’s unit"},
    },
    "settings": {
        "perspective": {"label": "Perspective", "words": {"site_owner": "The site owner’s"}},
        "basis": {"label": "Basis", "default": "Real terms, before tax, without subsidy"},
        "currency_year": {"label": "Currency year", "format": "EUR of {}"},
    },
}
