"""test_bridgework_dcf_adapter.py — against REAL J&J ValuationLab data
(offline snapshots, no network) combined with the real Bridgework Shapley
engine. Needs both packages installed."""
import pytest

from bridgework.dcf import DCF_ROLES
from keystone.attribution import attribute_dcf_change, to_dcf_drivers
from keystone.jnj_fixture import build_jnj_methods


def _net_debt(built):
    return built["base"].get("long_term_debt", 0.0) - built["base"].get("cash_and_equivalents", 0.0)


@pytest.fixture(scope="module")
def clean_and_corrupted():
    clean = build_jnj_methods()
    cash = clean["base"]["cash_and_equivalents"]
    corrupted = build_jnj_methods({"cash_and_equivalents": cash * 1.10})
    shares = clean["market"].shares_outstanding
    v1 = to_dcf_drivers(clean["dcf"], _net_debt(clean), shares)
    v2 = to_dcf_drivers(corrupted["dcf"], _net_debt(corrupted), shares)
    return v1, v2


def test_reconstruction_error_is_small(clean_and_corrupted):
    """Not zero -- the two models have genuinely different shapes -- but
    small enough to be usable. 1% is a generous ceiling; real result is ~0.31%."""
    v1, _ = clean_and_corrupted
    assert abs(v1.reconstruction_error_pct) < 0.01


def test_reconstruction_error_identical_across_versions_when_only_net_debt_changes(clean_and_corrupted):
    """The FCF path itself is unchanged between clean and corrupted (only
    net_debt differs), so the approximation error -- which comes entirely
    from the FCF-path fit -- should be identical for both, to high precision."""
    v1, v2 = clean_and_corrupted
    assert v1.reconstruction_error == pytest.approx(v2.reconstruction_error, abs=1e-6)


def test_reconstructed_shift_matches_real_shift_exactly(clean_and_corrupted):
    """The practical payoff of the identical-error property above: even
    though the reconstructed LEVEL is off, the reconstructed CHANGE isn't --
    the constant offset cancels in the subtraction."""
    v1, v2 = clean_and_corrupted
    real_shift = v2.real_price - v1.real_price
    reconstructed_shift = v2.reconstructed_price - v1.reconstructed_price
    assert reconstructed_shift == pytest.approx(real_shift, abs=1e-6)


def test_attribution_assigns_all_variance_to_net_debt(clean_and_corrupted):
    """The actual regression test: only net_debt differs between v1 and v2,
    so Shapley attribution must assign it (approximately) 100% of the price
    variance -- this is the wiring's own self-check, not just a plausible
    number."""
    v1, v2 = clean_and_corrupted
    result = attribute_dcf_change(v1, v2)
    assert list(result["driver"]) == ["net_debt"]
    assert result["pct_of_variance"].iloc[0] == pytest.approx(100.0, abs=0.01)


def test_attribute_dcf_change_rejects_identical_drivers(clean_and_corrupted):
    v1, _ = clean_and_corrupted
    with pytest.raises(ValueError):
        attribute_dcf_change(v1, v1)


def test_to_dcf_drivers_rejects_single_year_forecast():
    from keystone.attribution import to_dcf_drivers

    class _FakeUFCF:
        def __init__(self, ufcf):
            self.ufcf = ufcf

    class _FakeWACC:
        wacc = 0.07

    class _FakeDCFResult:
        ufcf_by_year = [_FakeUFCF(100.0)]
        wacc = _FakeWACC()
        terminal_growth = 0.02
        implied_share_price = 50.0

    with pytest.raises(ValueError):
        to_dcf_drivers(_FakeDCFResult(), net_debt=10.0, shares_outstanding=5.0)


def test_reconstructed_drivers_use_all_dcf_roles(clean_and_corrupted):
    v1, _ = clean_and_corrupted
    for role in DCF_ROLES:
        assert hasattr(v1.drivers, role)


def test_two_driver_change_attributes_correctly_and_reconciles():
    """A genuine interaction case, not just the single-driver sanity check
    above: WACC and terminal growth both move at once (real J&J baseline vs
    the sensitivity grid's own stress endpoints, 6.86%/2.5% -> 8.0%/1.5%) --
    exactly the multiplicative interaction bridgework/dcf.py's own docstring
    says is why a DCF is a better Shapley demo than a simple FCF model."""
    from dataclasses import replace

    from bridgework.dcf import compute_share_price
    from keystone.attribution import ReconstructedDCF

    built = build_jnj_methods()
    v1 = to_dcf_drivers(built["dcf"], _net_debt(built), built["market"].shares_outstanding)
    v2_drivers = replace(v1.drivers, wacc=0.080, terminal_growth=0.015)
    v2_price = compute_share_price(v2_drivers)
    v2 = ReconstructedDCF(v2_drivers, real_price=v2_price, reconstructed_price=v2_price)

    result = attribute_dcf_change(v1, v2)
    assert set(result["driver"]) == {"wacc", "terminal_growth"}
    # Efficiency axiom: contributions sum exactly to the real price shift --
    # shapley_attribution() already asserts this internally (ReconciliationError
    # on failure), this just confirms it from the caller's side too.
    total_shift = v2.reconstructed_price - v1.reconstructed_price
    assert result["shapley_contribution"].sum() == pytest.approx(total_shift, abs=1e-6)
    # Both drivers pushed price down (higher WACC, lower terminal growth are
    # each individually price-negative) -- both contributions should be negative.
    assert (result["shapley_contribution"] < 0).all()
