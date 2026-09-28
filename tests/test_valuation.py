"""keystone.valuation and keystone.subjects: the rules earned on the CYH live runs, now
generic, plus each subject's configuration."""
import dataclasses
import math
from unittest import mock

import pytest

from keystone import valuation as V
from keystone.subjects import SUBJECTS
from tests.conftest import FakeFetcher, synthetic_raw


def test_every_subject_has_market_data_for_itself_and_every_peer():
    for t, s in SUBJECTS.items():
        assert set(s.market) == {s.ticker, *s.peers}, t
        assert 0.0 in s.wacc_grid_offsets and min(s.wacc_grid_offsets) < 0 < max(s.wacc_grid_offsets)


def test_cyh_decisions_are_recorded_in_config():
    cyh = SUBJECTS["CYH"]
    assert len(cyh.peers) == 3 and "game the threshold" in cyh.comps_caveat
    assert len(cyh.precedent_deals) == 1 and math.isnan(cyh.precedent_deals[0].target_ebitda_proxy_usd_mm)
    assert any("retracted" in n for n in cyh.notes)


def test_required_fields_exclude_inventory():
    assert "inventory" not in V.REQUIRED and "operating_income" in V.REQUIRED


@pytest.mark.parametrize("row,expected", [
    ({"pretax_income": 100.0, "interest_expense": 30.0, "net_interest": -99.0}, 130.0),
    ({"pretax_income": 100.0, "net_interest": -25.0}, 125.0),
    ({"pretax_income": 100.0, "net_interest": 5.0}, None),
    ({"interest_expense": 30.0}, None),
])
def test_ebit_from_pretax(row, expected):
    assert V.ebit_from_pretax(row) == expected


def test_floor_note():
    assert V.floor_note("precedent", {"deal 1": 12.0}) == ""
    note = V.floor_note("precedent", {"deal 1": -11.23})
    assert "-11.23" in note and "floored at $0" in note


def test_peer_without_operating_income_uses_pretax_and_is_disclosed(synthetic_credit):
    fetcher = FakeFetcher(*synthetic_credit)
    raw = synthetic_raw("PFE")
    # A hospital income statement: no operating-income line AND none of the lines Trellis's
    # exact identity would rebuild it from (gross profit / cost of revenue / SG&A).
    for fld in ("operating_income", "gross_profit", "cost_of_revenue", "sga_expense"):
        raw.pop(fld, None)
    for fld, val in (("pretax_income", 1.0e10), ("interest_expense", 1.0e9)):
        raw[fld] = [dataclasses.replace(o, canonical_name=fld, value=val) for o in raw["revenue"]]
    with mock.patch.object(FakeFetcher, "_trellis_fetch", return_value=raw):
        row, fy, note = V.peer_base("PFE", fetcher)
    assert row["operating_income"] == pytest.approx(1.1e10) and fy == 2025
    assert note == "PFE FY2025: EBIT = pretax income + interest (no operating-income line)"


def test_zero_interest_rate_on_levered_subject_stops_the_run(synthetic_credit):
    fetcher = FakeFetcher(*synthetic_credit)
    raw = synthetic_raw("JNJ")
    raw.pop("interest_expense", None)
    with mock.patch.object(FakeFetcher, "_trellis_fetch", return_value=raw), \
         mock.patch("trellis.ingest.fetch_companyfacts", return_value={"facts": {"us-gaap": {}}}):
        with pytest.raises(RuntimeError, match="interest_rate = 0.0"):
            V.base_and_forecast("JNJ", fetcher)


def test_negative_precedent_is_floored_and_disclosed(synthetic_credit, monkeypatch):
    from valuationlab.precedent import DealTier, PrecedentDeal
    tiny = PrecedentDeal(acquirer="a", target="t", announced="2026-01-01", tier=DealTier.MATURE_REVENUE,
                         enterprise_value_usd_mm=1.0, target_revenue_usd_mm=10_000.0,
                         target_ebitda_proxy_usd_mm=float("nan"), ebitda_proxy_label="n/a", source="test")
    subject = dataclasses.replace(SUBJECTS["JNJ"], precedent_deals=(tiny,))
    built = V.build_methods(subject, FakeFetcher(*synthetic_credit))
    assert built["precedent_prices"] == [0.0]
    assert "EQUITY <= 0 before flooring" in built["precedent_range"].caveat


def test_derived_fields_and_own_rates_are_recorded(synthetic_credit):
    base, _, fy, drivers = V.base_and_forecast("JNJ", FakeFetcher(*synthetic_credit))
    assert base["_interest_rate"] == drivers.interest_rate > 0
    assert base["_tax_rate"] == drivers.tax_rate
    assert isinstance(base["_derived"], list)
