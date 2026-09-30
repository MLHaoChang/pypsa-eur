"""
The finance year axis (IC P4 plan C2).

Integer calendar years. `y0` = the year of financial close; construction years
run `y0 … cod_year − 1` (none when COD falls in `y0`); operating years
`cod_year … cod_year + analysis_years − 1`. Index 0 of every engine array is
`y0` (SAM's year 0 when there is one construction year).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.finance.case import FinanceCase, FinanceRefused


@dataclass(frozen=True)
class Timeline:
    y0: int
    cod_year: int
    base_year: int
    analysis_years: int

    @property
    def construction_years(self) -> list[int]:
        return list(range(self.y0, self.cod_year))

    @property
    def operating_years(self) -> list[int]:
        return list(range(self.cod_year, self.cod_year + self.analysis_years))

    @property
    def years(self) -> np.ndarray:
        return np.arange(self.y0, self.cod_year + self.analysis_years)

    @property
    def n(self) -> int:
        return len(self.years)

    def index(self, year: int) -> int:
        return year - self.y0

    def operating_mask(self) -> np.ndarray:
        return self.years >= self.cod_year

    def operating_k(self) -> np.ndarray:
        """Operating year number k (1 at COD; 0 before COD)."""
        return np.where(self.operating_mask(), self.years - self.cod_year + 1, 0)


def build_timeline(case: FinanceCase) -> Timeline:
    fin = case.inputs
    if fin.analysis_years is None:
        raise FinanceRefused("analysis_years_missing",
                             "state the analysis period (years of operation)")
    if fin.financial_close > case.cod:
        raise FinanceRefused("financial_close_after_cod",
                             f"financial close {fin.financial_close} is after COD {case.cod}")
    cods = set(fin.cod_by_asset.values())
    if len(cods) > 1:
        raise FinanceRefused("cod_mismatch",
                             f"the owner's assets have different CODs {sorted(cods)} (P5)")
    y0, cod_year = fin.financial_close.year, case.cod.year
    phases = len(fin.capex_phasing)
    construction = cod_year - y0
    if construction and phases != construction:
        raise FinanceRefused("capex_phasing_mismatch",
                             f"{phases} phasing entries for {construction} construction year(s)")
    if not construction and phases != 1:
        raise FinanceRefused("capex_phasing_mismatch",
                             "no construction year: capex_phasing must be [1.0]")
    tl = Timeline(y0=y0, cod_year=cod_year, base_year=case.base_year,
                  analysis_years=int(fin.analysis_years))
    for a in case.assets:
        if a.lifetime_years is not None and a.lifetime_years < tl.analysis_years:
            replaced = any(r[1] == a.name for r in fin.replacement_capex)
            if not replaced:
                raise FinanceRefused("asset_lifetime_short",
                                     f"{a.name}: lifetime {a.lifetime_years:g} < analysis period "
                                     f"{tl.analysis_years} with no replacement")
    return tl
