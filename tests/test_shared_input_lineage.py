"""test_shared_input_lineage.py — the field maps are asserted against the real
source read, not just internally self-consistent, since a self-consistent but
wrong map would pass tests while still being wrong."""
import pytest

from keystone.lineage import (
    COMPS_SUBJECT_FIELDS_BY_BASIS,
    DCF_SUBJECT_FIELDS,
    PRECEDENT_SUBJECT_FIELDS,
    all_pairwise_overlaps,
    method_fields,
    overlap,
)


def test_dcf_fields_match_verified_source_reading():
    # dcf.py's base_year_actuals usage: only long_term_debt/cash_and_equivalents
    # (net_debt calc) — verified by grep, dcf.py never reads "revenue" or
    # "operating_income" from the base year directly (forecast years instead).
    assert DCF_SUBJECT_FIELDS == frozenset({"long_term_debt", "cash_and_equivalents"})


def test_comps_ev_ebitda_fields_match_verified_source_reading():
    # comps.py's implied_value(): ebitda_proxy() -> operating_income +
    # depreciation_amortization, plus long_term_debt/cash_and_equivalents.
    assert COMPS_SUBJECT_FIELDS_BY_BASIS["ev_ebitda"] == frozenset({
        "operating_income", "depreciation_amortization",
        "long_term_debt", "cash_and_equivalents",
    })


def test_comps_ev_revenue_fields_match_verified_source_reading():
    assert COMPS_SUBJECT_FIELDS_BY_BASIS["ev_revenue"] == frozenset({
        "revenue", "long_term_debt", "cash_and_equivalents",
    })


def test_precedent_fields_match_verified_source_reading():
    assert PRECEDENT_SUBJECT_FIELDS == frozenset({
        "revenue", "operating_income", "depreciation_amortization",
        "long_term_debt", "cash_and_equivalents",
    })


def test_method_fields_rejects_unknown_method():
    with pytest.raises(ValueError):
        method_fields("dealab")


def test_method_fields_rejects_unknown_basis():
    with pytest.raises(ValueError):
        method_fields("comps", comps_basis="ev_nonsense")


def test_dcf_comps_overlap_under_real_run_configuration():
    # Real run uses ev_ebitda: dcf {debt, cash} vs comps {oi, da, debt, cash}
    # -> shared = {debt, cash}, union has 4 -> 2/4 = 50%.
    shared, frac = overlap("dcf", "comps", comps_basis="ev_ebitda")
    assert shared == frozenset({"long_term_debt", "cash_and_equivalents"})
    assert frac == pytest.approx(0.5)


def test_precedent_comps_overlap_is_the_highest_pair():
    # precedent's 5 fields are a superset of comps' ev_ebitda 4 -> 4/5 = 80%,
    # the highest of the three pairs — matches what run_planted_corruption
    # found by actually running it.
    shared, frac = overlap("precedent", "comps", comps_basis="ev_ebitda")
    assert frac == pytest.approx(0.8)


def test_revenue_only_shared_by_precedent_under_ev_ebitda_basis():
    # The real run's basis (ev_ebitda) means comps never touches "revenue" on
    # the subject side — only precedent does. Corrupting revenue in this
    # configuration would NOT demonstrate three-way sharing.
    dcf_fields = method_fields("dcf")
    comps_fields = method_fields("comps", comps_basis="ev_ebitda")
    prec_fields = method_fields("precedent")
    assert "revenue" not in dcf_fields
    assert "revenue" not in comps_fields
    assert "revenue" in prec_fields


def test_all_pairwise_overlaps_covers_exactly_three_pairs():
    pairs = all_pairwise_overlaps()
    assert set(pairs.keys()) == {("dcf", "comps"), ("dcf", "precedent"), ("comps", "precedent")}
