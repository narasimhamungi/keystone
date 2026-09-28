"""jnj_fixture.py — J&J's FROZEN ValuationLab snapshot, kept as an offline test fixture.

Production runs no longer use this: every subject (J&J included) is valued through
keystone.valuation on live Trellis data, cached for replay. This frozen snapshot predates
the Trellis fixes (its FY2025 row has no interest_expense, and its cost of debt was
averaged over stale years only), which is exactly why it was retired from the live path.
It stays because it is real, committed J&J data the offline tests and the Phase-0
scripts need (scripts/). The JSON files are bundled under keystone/data/jnj_fixture/, copied
from ValuationLab's data/snapshots/ (generated 2026-09-15).
"""
from __future__ import annotations

import json
from pathlib import Path

from valuationlab.comps import build_peer_multiple, implied_value, summarize
from valuationlab.dcf import MarketData, run_dcf, sensitivity_grid
from valuationlab.marketdata import CAPMInputs, get_market_data
from valuationlab.precedent import MATURE_REVENUE_DEALS, apply_multiple
from valuationlab.triangulate import Method, MethodRange

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "jnj_fixture"
MARKET_SNAPSHOT = SNAPSHOT_DIR / "market.json"

# Copied from scripts/run_valuation.py's own module-level constants — not
# re-derived, so this can never silently drift from the curation that script
# already did and explained. If run_valuation.py's PHARMA_PEERS/CAPM/etc.
# change, this needs updating to match; that's a feature (a merge conflict
# here is a real signal), not a maintenance burden to design around.
SUBJECT = {"cik": 200406, "ticker": "JNJ", "name": "Johnson & Johnson"}
PHARMA_PEERS = [
    {"cik": 78003, "ticker": "PFE", "name": "Pfizer Inc."},
    {"cik": 310158, "ticker": "MRK", "name": "Merck & Co., Inc."},
    {"cik": 1551152, "ticker": "ABBV", "name": "AbbVie Inc."},
    {"cik": 14272, "ticker": "BMY", "name": "Bristol-Myers Squibb Company"},
]
CAPM = CAPMInputs(
    beta=0.53, risk_free_rate=0.0496, equity_risk_premium=0.0423,
    basis="See scripts/run_valuation.py's own CAPM constant for the full "
          "sourced basis (Blume-adjusted beta, 10Y UST, Damodaran implied ERP).")
TERMINAL_GROWTH = 0.025
WACC_RANGE = [0.060, 0.065, 0.070, 0.075, 0.080]
GROWTH_RANGE = [0.015, 0.020, 0.025, 0.030]


def load_snapshot(ticker: str) -> dict:
    path = SNAPSHOT_DIR / f"{ticker}_financials.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No golden snapshot for {ticker} at {path}. This adapter is "
            f"offline-only; it doesn't fetch live data.")
    with open(path) as f:
        data = json.load(f)
    # Same int-key conversion load_financial_snapshot does in run_valuation.py —
    # JSON always round-trips dict keys as strings; the rest of ValuationLab's
    # code expects int fiscal years.
    data["table"] = {int(k): v for k, v in data["table"].items()}
    data["forecast"] = {int(k): v for k, v in data["forecast"].items()}
    return data


def build_jnj_methods(field_overrides: dict[str, float] | None = None) -> dict:
    """Builds DCF/comps/precedent results and MethodRanges for J&J from the
    committed snapshots. field_overrides: {field_name: new_value} applied to
    J&J's own base-year row before anything is computed from it — the planted-
    corruption hook. Peer data (comps' own multiples) is never touched by
    field_overrides; only the SUBJECT's base-year row is, since that's what a
    bad Trellis mapping on the subject's own filing would actually corrupt.
    """
    field_overrides = field_overrides or {}
    subject = load_snapshot("JNJ")
    base = dict(subject["table"][subject["base_year"]])
    base.update(field_overrides)

    market = get_market_data("JNJ", MARKET_SNAPSHOT, prefer_live=False)
    dcf_market = MarketData(
        share_price=market.share_price, shares_outstanding=market.shares_outstanding,
        beta=CAPM.beta, risk_free_rate=CAPM.risk_free_rate,
        equity_risk_premium=CAPM.equity_risk_premium, as_of=market.as_of)
    interest_rate = base.get("_interest_rate", 0.038)
    tax_rate = base.get("_tax_rate", 0.17)

    dcf = run_dcf(base, subject["forecast"], dcf_market, interest_rate, tax_rate, TERMINAL_GROWTH)
    grid = sensitivity_grid(base, subject["forecast"], dcf_market, interest_rate, tax_rate,
                             WACC_RANGE, GROWTH_RANGE)
    finite = [v for v in grid.values() if v == v and v > 0]
    dcf_range = MethodRange(
        method=Method.DCF, low=min(finite), mid=dcf.implied_share_price, high=max(finite),
        basis=f"WACC {min(WACC_RANGE):.1%}-{max(WACC_RANGE):.1%} x g "
              f"{min(GROWTH_RANGE):.1%}-{max(GROWTH_RANGE):.1%}; computed WACC {dcf.wacc.wacc:.2%}",
        caveat=f"Terminal value is {dcf.pv_terminal_value / dcf.enterprise_value:.0%} of EV.",
        provenance=f"Trellis forecast off FY{subject['base_year']} (adapter, offline snapshot)")

    peer_multiples = []
    for peer in PHARMA_PEERS:
        pdata = load_snapshot(peer["ticker"])
        pmarket = get_market_data(peer["ticker"], MARKET_SNAPSHOT, prefer_live=False)
        peer_multiples.append(build_peer_multiple(
            peer["ticker"], peer["name"], pdata["base_year"],
            pdata["table"][pdata["base_year"]], pmarket))
    comps = summarize(peer_multiples, basis="ev_ebitda")
    comps_implied = implied_value(base, comps, market.shares_outstanding)
    comps_range = MethodRange(
        method=Method.TRADING_COMPS, low=comps_implied["implied_price_low"],
        mid=comps_implied["implied_price_median"], high=comps_implied["implied_price_high"],
        basis=f"EV/EBITDA {comps.low:.1f}x-{comps.high:.1f}x across {len(comps.included)} peers",
        caveat="Annual, not LTM denominators.",
        provenance=f"Trellis annual tables; market {market.as_of} (adapter, offline snapshot)")

    subject_ebitda = base["operating_income"] + base["depreciation_amortization"]
    net_debt = base.get("long_term_debt", 0.0) - base.get("cash_and_equivalents", 0.0)
    prec_prices = []
    for deal in MATURE_REVENUE_DEALS:
        ev = apply_multiple(deal, base["revenue"], subject_ebitda)["implied_ev_from_revenue_multiple"]
        prec_prices.append((ev - net_debt) / market.shares_outstanding)
    prec_range = MethodRange(
        method=Method.PRECEDENT, low=min(prec_prices), mid=sum(prec_prices) / len(prec_prices),
        high=max(prec_prices),
        basis=f"EV/Revenue from {len(MATURE_REVENUE_DEALS)} sourced mature-revenue pharma deals",
        caveat=f"Only {len(MATURE_REVENUE_DEALS)} deals sourced.",
        provenance="Acquirer/target SEC filings (adapter, offline snapshot)")

    return dict(
        dcf=dcf, dcf_range=dcf_range,
        comps=comps, comps_range=comps_range,
        precedent_prices=prec_prices, precedent_range=prec_range,
        market=market, base=base,
    )
