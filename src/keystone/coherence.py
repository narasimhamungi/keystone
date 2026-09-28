"""coherence.py — does the equity market's pricing agree with the credit signal?

REDESIGNED after the first CYH run. The original rule asked whether the market price sat
below the LOWEST valuation on offer. Under limited liability, any heavily indebted company
has some method floored at $0, and then that test can never fire -- CYH read UNDERCUTS by
construction, not evidence. The rule worked only for J&J-shaped companies.

The redesign returns to the question Keystone was scoped around: does the equity
market's implied story agree with what credit risk signals say?

  credit_elevated  PD > pd_multiple_threshold x the population default rate
                   (Altman Z'' distress zone when the PD is suppressed out of domain)
  market_stressed  equity cushion = market cap / (market cap + net debt) < cushion_threshold

The equity cushion is defined for every capital structure and cannot degenerate the way
a price floor does. Threshold 25% (a stated judgement, like triangulate's 75% / 2.0x /
5-peer thresholds): below it, an enterprise-value decline of that size -- ordinary in a
recession -- would erase the equity entirely. It was set on that principle, not tuned
to either subject; every result reports whether the verdict survives nearby thresholds
(cushion 15-35%, PD multiple 1.5x-3x) and says so when it doesn't.

The valuation methods stay in the picture descriptively: the market price as a share of
the median method value -- context, not an input to the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Iterable

from keystone.credit import CreditRead

DEFAULT_PD_MULTIPLE_THRESHOLD = 2.0
DEFAULT_CUSHION_THRESHOLD = 0.25
ROBUSTNESS_CUSHION = (0.15, 0.35)
ROBUSTNESS_PD_MULTIPLE = (1.5, 3.0)


class Verdict(Enum):
    SUPPORTS = "supports"    # credit and market agree (both stressed, or neither)
    UNDERCUTS = "undercuts"  # they disagree -- the case worth reading closely
    SILENT = "silent"        # no credit signal at all
    UNPRICED = "unpriced"    # no usable market pricing to compare against


def equity_cushion(market_cap: float, net_debt: float) -> float | None:
    """Equity's share of enterprise value. Net cash counts as zero debt (cushion 1.0)."""
    if market_cap is None or market_cap <= 0:
        return None
    return market_cap / (market_cap + max(net_debt, 0.0))


@dataclass(frozen=True)
class CoherenceResult:
    verdict: Verdict
    credit_elevated: bool | None
    market_stressed: bool | None
    credit_basis: str
    pd_multiple: float | None
    equity_cushion: float | None
    cushion_threshold: float
    pd_multiple_threshold: float
    robust: bool | None
    market_to_median_value: float | None

    def explain(self) -> str:
        if self.verdict is Verdict.SILENT:
            return "SILENT: no credit signal (out of domain and Altman Z'' unscored)."
        if self.verdict is Verdict.UNPRICED:
            return f"UNPRICED: credit read available ({self.credit_basis}) but no usable market pricing."
        both = "both signal stress" if self.credit_elevated else "neither signals stress"
        head = (f"{self.verdict.value.upper()}: credit ({self.credit_basis}) and market pricing "
                f"(equity is {self.equity_cushion:.1%} of enterprise value; stressed below "
                f"{self.cushion_threshold:.0%}) ")
        head += f"agree -- {both}." if self.verdict is Verdict.SUPPORTS else (
            f"disagree -- credit {'elevated' if self.credit_elevated else 'benign'}, market "
            f"{'stressed' if self.market_stressed else 'unstressed'}.")
        rob = (" Robust: unchanged for cushion thresholds 15-35% and PD multiples 1.5-3x."
               if self.robust else " FRAGILE: flips within nearby thresholds -- not a finding "
               "on its own; read the inputs.")
        ctx = (f" Market price is {self.market_to_median_value:.0%} of the median method value."
               if self.market_to_median_value is not None else "")
        return head + rob + ctx


def _credit(read: CreditRead, k: float) -> tuple[bool | None, str, float | None]:
    if read.scored is not None:
        s = read.scored
        m = s.pd / s.population_default_rate
        return m > k, f"PD {s.pd:.2%} = {m:.1f}x the {s.population_default_rate:.1%} base rate", m
    if read.altman_z_double_prime is not None:
        return (read.altman_zone == "distress",
                f"Altman Z'' {read.altman_z_double_prime:.2f} ({read.altman_zone}), PD suppressed", None)
    return None, "no credit signal", None


def credit_equity_coherence(
    credit: CreditRead, *, market_price: float | None, shares_outstanding: float | None,
    net_debt: float | None, method_mids: Iterable[float] = (),
    pd_multiple_threshold: float = DEFAULT_PD_MULTIPLE_THRESHOLD,
    cushion_threshold: float = DEFAULT_CUSHION_THRESHOLD,
) -> CoherenceResult:
    elevated, basis, mult = _credit(credit, pd_multiple_threshold)
    mids = [m for m in method_mids if m is not None]
    ctx = (market_price / median(mids)) if (market_price and mids and median(mids) > 0) else None
    if elevated is None:
        return CoherenceResult(Verdict.SILENT, None, None, basis, mult, None, cushion_threshold,
                               pd_multiple_threshold, None, ctx)
    cushion = (equity_cushion(market_price * shares_outstanding, net_debt)
               if None not in (market_price, shares_outstanding, net_debt) else None)
    if cushion is None:
        return CoherenceResult(Verdict.UNPRICED, elevated, None, basis, mult, None, cushion_threshold,
                               pd_multiple_threshold, None, ctx)
    stressed = cushion < cushion_threshold
    verdict = Verdict.SUPPORTS if elevated == stressed else Verdict.UNDERCUTS

    def variant(k: float, c: float) -> Verdict:
        e = (mult > k) if mult is not None else elevated
        return Verdict.SUPPORTS if e == (cushion < c) else Verdict.UNDERCUTS

    robust = all(variant(k, c) is verdict for k in ROBUSTNESS_PD_MULTIPLE for c in ROBUSTNESS_CUSHION)
    return CoherenceResult(verdict, elevated, stressed, basis, mult, cushion, cushion_threshold,
                           pd_multiple_threshold, robust, ctx)
