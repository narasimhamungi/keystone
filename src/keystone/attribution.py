"""bridgework_dcf_adapter.py — wires ValuationLab's real DCF onto Bridgework's
Shapley attribution engine. Not a parameter rename: the two models have
genuinely different shapes.

THE MISMATCH, STATED PRECISELY
--------------------------------
ValuationLab's DCF (dcf.run_dcf) builds a full multi-year FCF series from
Trellis's own year-by-year forecast -- each year has its own NOPAT, D&A,
capex, and working-capital delta, so the growth rate is NOT constant year to
year (confirmed on real J&J data: -0.91% year1->year2, then a steady 4.58%
year2 through year5). Bridgework's DCF (dcf.compute_share_price) applies a
SINGLE constant growth rate compounding for 5 years -- that's its whole
point, six clean drivers a Shapley decomposition can attribute across.
Reconciling them means approximating the real multi-year path with one
constant rate, not translating units.

RECONSTRUCTION METHOD
-----------------------
Matches the real path's year-1 and year-5 values exactly (geometric-mean
growth rate between those two endpoints), rather than naively feeding the
real year-1 FCF as Bridgework's fcf_base -- that's a different quantity in
Bridgework's model (fcf_base is a "year 0" value the model grows INTO year 1,
confirmed by reading compute_share_price: `fcf *= 1+g` happens before year
1's contribution). Verified against real J&J data: this reconstruction prices
within +0.31% ($0.78/share on a $251.07 base) of ValuationLab's real
implied_share_price -- small, but real, and reported on every call rather
than assumed away.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bridgework.dcf import DCF_ROLES, DCFDrivers, compute_share_price
from bridgework.variance_bridge import shapley_attribution


@dataclass(frozen=True)
class ReconstructedDCF:
    drivers: DCFDrivers
    real_price: float
    reconstructed_price: float

    @property
    def reconstruction_error(self) -> float:
        return self.reconstructed_price - self.real_price

    @property
    def reconstruction_error_pct(self) -> float:
        return self.reconstruction_error / self.real_price if self.real_price else float("nan")


def to_dcf_drivers(dcf_result, net_debt: float, shares_outstanding: float) -> ReconstructedDCF:
    """dcf_result: a valuationlab.dcf.DCFResult (has .ufcf_by_year, .wacc,
    .terminal_growth, .implied_share_price). net_debt/shares_outstanding
    aren't on DCFResult itself in ValuationLab's own shape -- pass what
    scripts/run_valuation.py computes for them (long_term_debt - cash, and
    market.shares_outstanding)."""
    ufcf = [u.ufcf for u in dcf_result.ufcf_by_year]
    if len(ufcf) < 2:
        raise ValueError(f"Need at least 2 forecast years to fit a growth rate, got {len(ufcf)}.")

    n_steps = len(ufcf) - 1
    cagr = (ufcf[-1] / ufcf[0]) ** (1 / n_steps) - 1
    fcf_base = ufcf[0] / (1 + cagr)  # a "year 0" value Bridgework's model grows into year 1

    drivers = DCFDrivers(
        fcf_base=fcf_base, growth_explicit=cagr, wacc=dcf_result.wacc.wacc,
        terminal_growth=dcf_result.terminal_growth, net_debt=net_debt,
        shares_outstanding=shares_outstanding,
    )
    reconstructed_price = compute_share_price(drivers)
    return ReconstructedDCF(drivers, dcf_result.implied_share_price, reconstructed_price)


def attribute_dcf_change(
    v1: ReconstructedDCF, v2: ReconstructedDCF, *, method: str = "auto",
) -> pd.DataFrame:
    """Shapley attribution of the price change from v1 to v2, driver by
    driver. Only the roles that actually differ are attributed -- passing an
    unchanged driver into Shapley's coalition enumeration wastes computation
    and (for the exact method) risks a spurious near-zero contribution
    reading as though it were measured rather than trivially zero."""
    changed = tuple(r for r in DCF_ROLES if getattr(v1.drivers, r) != getattr(v2.drivers, r))
    if not changed:
        raise ValueError("v1 and v2 have identical drivers -- nothing to attribute.")
    return shapley_attribution(v1.drivers, v2.drivers, changed, method=method, model_fn=compute_share_price)
