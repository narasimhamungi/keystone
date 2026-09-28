"""Shared offline fixtures. Nothing here touches the network.

FakeFetcher is a real LiveFetcher with only its three network hooks replaced, so save(),
the cache format and ReplayFetcher are exercised for real. Its data is honest about being
synthetic: CreditRiskLab's own synthetic panel (watermarked by that module), and Trellis raw
observations backcast from the REAL J&J/peer base years in the bundled fixture at a constant
4% a year -- a mechanics test of the pipeline, not a test of any number's accuracy.
"""
from datetime import date

import pytest
from creditrisklab.synthetic import build_fundamentals, stamped_issuers
from creditrisklab.universe import load_issuers
from trellis.ingest import Observation
from trellis.schema import SCHEMA

from keystone.fetch import LiveFetcher
from keystone.jnj_fixture import load_snapshot
from keystone.valuation import norm_cik

CIKS = {"JNJ": 200406, "PFE": 78003, "MRK": 310158, "ABBV": 1551152, "BMY": 14272}
AS_OF = date(2024, 6, 30)  # inside the synthetic credit panel's range -> J&J FY2023


def synthetic_raw(ticker: str, years=range(2021, 2026), growth: float = 0.04) -> dict:
    inst = {i.canonical_name: i.instant for i in SCHEMA}
    tag = {i.canonical_name: i.xbrl_tags[0] for i in SCHEMA}
    s = load_snapshot(ticker)
    base = dict(s["table"][s["base_year"]])
    if "interest_expense" not in base and base.get("long_term_debt"):
        # The frozen fixture predates the Trellis interest fixes and has NO interest expense
        # (the gap that made Trellis derive a 0.0 cost of debt -- the guard correctly fires on
        # it). SYNTHETIC stand-in for this mechanics test only: 3% of long-term debt.
        base["interest_expense"] = 0.03 * base["long_term_debt"]
    raw: dict = {}
    for y in years:
        scale = (1 + growth) ** (y - s["base_year"])
        for k, v in base.items():
            if k.startswith("_") or k not in inst:
                continue
            raw.setdefault(k, []).append(Observation(
                canonical_name=k, matched_tag=tag[k], fiscal_year=y, fiscal_period="FY",
                period_end=f"{y}-12-31", form="10-K", filed=f"{y + 1}-02-15",
                accession_number=f"{ticker}-{y}", value=float(v) * scale, unit="USD",
                period_start=None if inst[k] else f"{y}-01-01"))
    return raw


class FakeFetcher(LiveFetcher):
    def __init__(self, issuers, frames) -> None:
        super().__init__()
        self._frames = frames
        self._by_cik = {norm_cik(c): t for t, c in CIKS.items()}

    def _resolve(self, ticker):
        return str(CIKS[ticker]).zfill(10)

    def _crl_fetch(self, cik, ticker):
        return self._frames[cik]

    def _trellis_fetch(self, cik):
        return synthetic_raw(self._by_cik[cik])


@pytest.fixture(scope="session")
def synthetic_credit():
    issuers = stamped_issuers(load_issuers())
    return issuers, {norm_cik(k): v for k, v in build_fundamentals(issuers, seed=42).items()}


@pytest.fixture
def fake_fetcher(synthetic_credit):
    issuers, frames = synthetic_credit
    return FakeFetcher(issuers, frames)
