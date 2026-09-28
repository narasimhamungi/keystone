"""test_convergence_diagnostic.py — against REAL J&J snapshot data (the
committed golden fixtures in valuationlab/data/snapshots/), not synthetic
stand-ins. No network needed; these are offline fixtures by design (see
valuationlab/data/snapshots/README.md)."""
import pytest

from keystone.convergence import (
    HIGH_OVERLAP_THRESHOLD,
    MissingMeasuredInputError,
    convergence_diagnostic,
)
from valuationlab.triangulate import triangulate as bare_triangulate
from keystone.jnj_fixture import build_jnj_methods


@pytest.fixture(scope="module")
def clean():
    return build_jnj_methods()


@pytest.fixture(scope="module")
def clean_ranges(clean):
    return [clean["dcf_range"], clean["comps_range"], clean["precedent_range"]]


def test_refuses_without_dcf_result(clean, clean_ranges):
    with pytest.raises(MissingMeasuredInputError):
        convergence_diagnostic(clean_ranges, dcf_result=None,
                                comps_result=clean["comps"],
                                precedent_prices=clean["precedent_prices"])


def test_refuses_without_comps_result(clean, clean_ranges):
    with pytest.raises(MissingMeasuredInputError):
        convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                comps_result=None,
                                precedent_prices=clean["precedent_prices"])


def test_refuses_without_precedent_prices(clean, clean_ranges):
    with pytest.raises(MissingMeasuredInputError):
        convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                comps_result=clean["comps"], precedent_prices=None)


def test_refuses_on_empty_precedent_prices(clean, clean_ranges):
    with pytest.raises(MissingMeasuredInputError):
        convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                comps_result=clean["comps"], precedent_prices=[])


def test_computes_measured_parameters_matching_run_valuation_recipe(clean, clean_ranges):
    """Cross-check against the exact formulas scripts/run_valuation.py uses —
    not just 'it ran without error'."""
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                 comps_result=clean["comps"],
                                 precedent_prices=clean["precedent_prices"],
                                 market_price=clean["market"].share_price)
    expected_tv_share = clean["dcf"].pv_terminal_value / clean["dcf"].enterprise_value
    expected_spread = max(clean["precedent_prices"]) / min(clean["precedent_prices"])
    # Recovered indirectly: the disqualification's "measured" string should
    # report these same figures (rounded), since triangulate() derives its
    # own disqualification checks from exactly these parameters.
    dq_reasons = {d.method: d.measured for d in ac.conclusion.recommendation.disqualified}
    assert f"{expected_tv_share:.0%}" in next(v for k, v in dq_reasons.items()
                                               if "DCF" in str(k) or "dcf" in str(k).lower())
    assert f"{expected_spread:.1f}x" in next(v for k, v in dq_reasons.items()
                                              if "PRECEDENT" in str(k).upper())


def test_bare_triangulate_disqualifies_for_different_reason_than_wrapper(clean, clean_ranges):
    """The actual missing-parameters-guard claim, as a regression test: bare
    triangulate() and the wrapper both disqualify all three methods on this
    real data, but for verifiably different reasons — confirms the finding
    run_missing_parameters_guard.py demonstrates stays true, not just once."""
    bare = bare_triangulate(clean_ranges)
    wrapped = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                      comps_result=clean["comps"],
                                      precedent_prices=clean["precedent_prices"])
    bare_reasons = {d.method: d.reason for d in bare.recommendation.disqualified}
    wrapped_reasons = {d.method: d.reason for d in wrapped.conclusion.recommendation.disqualified}
    assert set(bare_reasons) == set(wrapped_reasons)  # same 3 methods, this run
    for method in bare_reasons:
        assert bare_reasons[method] != wrapped_reasons[method]  # different WHY
        assert "too wide to discriminate" in bare_reasons[method]  # bare = width-only


def test_shared_input_notes_cover_every_divergence_pair(clean, clean_ranges):
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                 comps_result=clean["comps"],
                                 precedent_prices=clean["precedent_prices"])
    pairs = {(n.method_a, n.method_b) for n in ac.shared_input_notes}
    assert len(pairs) == 3  # every pair, not just ones reading "Converge"


def test_precedent_comps_is_the_highest_overlap_pair(clean, clean_ranges):
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                 comps_result=clean["comps"],
                                 precedent_prices=clean["precedent_prices"])
    note = next(n for n in ac.shared_input_notes
                if {n.method_a, n.method_b} == {"precedent", "comps"})
    assert note.overlap_fraction == pytest.approx(0.8)
    assert note.overlap_fraction >= HIGH_OVERLAP_THRESHOLD


def test_format_includes_shared_input_section(clean, clean_ranges):
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"],
                                 comps_result=clean["comps"],
                                 precedent_prices=clean["precedent_prices"])
    text = ac.format()
    assert "Shared-input analysis" in text
    assert "precedent vs comps" in text or "comps vs precedent" in text


def test_cash_corruption_shifts_all_three_methods_by_identical_dollar_amount():
    """Regression test for the planted-corruption experiment's actual finding:
    a shared net-debt-component corruption moves DCF, comps, and precedent by
    the same absolute amount (they share the same net-debt bridge), not just
    'some amount each'."""
    clean_built = build_jnj_methods()
    cash = clean_built["base"]["cash_and_equivalents"]
    corrupted_built = build_jnj_methods({"cash_and_equivalents": cash * 1.10})

    shifts = {
        label: corrupted_built[key].mid - clean_built[key].mid
        for key, label in (("dcf_range", "dcf"), ("comps_range", "comps"),
                            ("precedent_range", "precedent"))
    }
    # All three within half a cent of each other -- floating point, not exact
    # equality, but "identical to the cent" as the experiment script reports.
    values = list(shifts.values())
    assert max(values) - min(values) < 0.005
    assert all(v > 0 for v in values)  # cash increase -> price increase, all three


def test_zero_precedent_price_is_accepted_not_treated_as_missing(clean, clean_ranges):
    """CYH, found live: a deal implying equity <= 0 is floored to $0 upstream. The first
    version rejected any price <= 0 as 'missing input'."""
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"], comps_result=clean["comps"],
                                precedent_prices=[0.0])
    assert ac.conclusion is not None


def test_negative_precedent_price_is_rejected_as_contract_violation(clean, clean_ranges):
    with pytest.raises(MissingMeasuredInputError, match="floor at 0"):
        convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"], comps_result=clean["comps"],
                               precedent_prices=[-11.23])


def test_mixed_zero_and_positive_deals_are_maximal_disagreement(clean, clean_ranges):
    ac = convergence_diagnostic(clean_ranges, dcf_result=clean["dcf"], comps_result=clean["comps"],
                                precedent_prices=[0.0, 50.0])
    dq = [d for d in ac.conclusion.recommendation.disqualified if "PRECEDENT" in str(d.method).upper()]
    assert dq and "inf" in dq[0].measured
