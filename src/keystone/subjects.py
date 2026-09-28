"""subjects.py — every company Keystone can value, as data, not code.

Adding a subject means adding a SubjectConfig: its peers, CAPM inputs, precedent deals and
dated market data. Everything else (ingestion, forecast, DCF/comps/precedent, credit read,
tie-out, verdict, report) is shared code in keystone.valuation / keystone.run. That this
works for two genuinely different companies -- J&J (mega-cap, healthy, multi-segment) and
CYH (small-cap, distressed, single-segment hospital operator) -- is the generalization
proof Keystone was scoped around.

MARKET DATA POLICY (decided): each subject carries cited, dated market-data snapshots.
Every run records them in its manifest, so a result is always traceable to the exact
prices it used and replays reproduce it exactly. A live quote feed would make runs
irreproducible and raises licensing questions the free options don't resolve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from valuationlab.marketdata import CAPMInputs, MarketSnapshot
from valuationlab.precedent import MATURE_REVENUE_DEALS, DealTier, PrecedentDeal


@dataclass(frozen=True)
class SubjectConfig:
    ticker: str
    name: str
    peers: tuple[str, ...]
    capm: CAPMInputs
    terminal_growth: float
    growth_range: tuple[float, ...]
    precedent_deals: tuple[PrecedentDeal, ...]
    market: dict  # ticker -> MarketSnapshot, subject AND every peer
    # WACC grid centred on the computed WACC, +/-1pp -- the same rule for every subject.
    wacc_grid_offsets: tuple[float, ...] = (-0.010, -0.005, 0.0, 0.005, 0.010)
    comps_basis: str = "ev_ebitda"
    comps_caveat: str = ""
    precedent_caveat: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


_VL_MARKET = ("ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with "
              "Keystone as the J&J fixture")


def _vl(ticker: str, price: float, shares: float) -> MarketSnapshot:
    return MarketSnapshot(ticker=ticker, share_price=price, shares_outstanding=shares,
                          as_of="2026-09-15", source=_VL_MARKET)


JNJ = SubjectConfig(
    ticker="JNJ", name="Johnson & Johnson",
    peers=("PFE", "MRK", "ABBV", "BMY"),  # ValuationLab's own PHARMA_PEERS, chosen there
    capm=CAPMInputs(beta=0.53, risk_free_rate=0.0496, equity_risk_premium=0.0423,
                    basis="ValuationLab scripts/run_valuation.py CAPM (Blume-adjusted beta, "
                          "10Y UST, Damodaran implied ERP)"),
    terminal_growth=0.025,
    growth_range=(0.015, 0.020, 0.025, 0.030),
    precedent_deals=tuple(MATURE_REVENUE_DEALS),
    market={"JNJ": _vl("JNJ", 267.20, 2_409_898_597.0), "PFE": _vl("PFE", 27.55, 5_699_673_589.0),
            "MRK": _vl("MRK", 143.79, 2_467_171_638.0), "ABBV": _vl("ABBV", 263.04, 1_767_117_285.0),
            "BMY": _vl("BMY", 63.73, 2_042_714_656.0)},
    comps_caveat="Pharma peers only; J&J's MedTech segment is not reflected in this peer set.",
    notes=("SOTP, the market-implied residual and catalysts stay out of the automated core: "
           "each needs hand-sourced data per subject.",),
)

CYH = SubjectConfig(
    ticker="CYH", name="Community Health Systems",
    peers=("HCA", "UHS", "THC"),
    capm=CAPMInputs(beta=1.22, risk_free_rate=0.0496, equity_risk_premium=0.0423,
                    basis="Beta hand-sourced (converges across live quotes, Aug 2026); rf and "
                          "ERP carried over from J&J's basis -- macro, not company-specific"),
    terminal_growth=0.020,  # below J&J's 2.5%: revenue shrinking (Q2 2026 admissions -11.4%)
    growth_range=(0.010, 0.015, 0.020, 0.025),
    precedent_deals=(PrecedentDeal(
        acquirer="WVU Health System", target="Independence Health System",
        announced="2026-06-01", tier=DealTier.MATURE_REVENUE,
        enterprise_value_usd_mm=800.0, target_revenue_usd_mm=1_170.0,
        target_ebitda_proxy_usd_mm=float("nan"),
        ebitda_proxy_label="not disclosed -- revenue-basis multiple only",
        source="Announced June 2026; $800M for 5 hospitals with $1.17B disclosed LTM revenue"),),
    market={
        "CYH": MarketSnapshot(ticker="CYH", share_price=2.79, shares_outstanding=141_010_000,
                              as_of="2026-07-28", source="hand-sourced (Morningstar; corroborated "
                              "within cents by StockTitan/eToro/Motley Fool, Jul-Aug 2026)"),
        "HCA": MarketSnapshot(ticker="HCA", share_price=402.59, shares_outstanding=221_839_800,
                              as_of="2026-07-31", source="hand-sourced (10jqka, equibles)"),
        "UHS": MarketSnapshot(ticker="UHS", share_price=155.77, shares_outstanding=61_700_000,
                              as_of="2026-07-24", source="hand-sourced (ChartRow)"),
        "THC": MarketSnapshot(ticker="THC", share_price=259.45,
                              shares_outstanding=20_900_000_000 / 259.45, as_of="2026-08-12",
                              source="hand-sourced (Kalkine); shares DERIVED from disclosed "
                              "market cap / price, not directly stated"),
    },
    comps_caveat=("Peers are 25-200x CYH's market cap. Kept at 3 (below the 5-peer minimum, so "
                  "comps is ruled out) by decision: the only other listed pure acute-care "
                  "operator, Ardent (IPO 2024), would make 4 -- still short -- and padding with "
                  "behavioral or rehab operators to reach 5 would game the threshold."),
    precedent_caveat=("Kept at one deal by decision: hospital-system M&A rarely discloses target "
                      "financials (HCA's 10-K gives bolt-on prices with no target revenue). "
                      "CYH's own divestiture prices are the best source for a future pass."),
    notes=("The 8-12% WACC range once used for CYH was retracted: it rested on an unverified "
           "distress-yield claim, and the one bond quote found (10.875% secured notes due 2032 "
           "at a 6.90% yield, illiquid, undated) contradicts it.",),
)

SUBJECTS: dict[str, SubjectConfig] = {"JNJ": JNJ, "CYH": CYH}
