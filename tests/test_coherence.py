"""The redesigned verdict: credit (PD vs base rate) against market pricing (equity cushion).
Credit reads are built directly; the market figures are the real ones from the live runs."""
import pytest

from keystone.coherence import (DEFAULT_CUSHION_THRESHOLD, Verdict, credit_equity_coherence,
                                equity_cushion)
from keystone.credit import CreditRead, ScoredIssuer

CYH = dict(market_price=2.79, shares_outstanding=141_010_000, net_debt=10_120_000_000)
JNJ = dict(market_price=267.20, shares_outstanding=2_409_898_597, net_debt=19_729_000_000)


def read(pd=None, z=None, zone="unscored"):
    scored = None if pd is None else ScoredIssuer(raw_probability=pd, sample_default_rate=0.1,
                                                   population_default_rate=0.02, pd=pd,
                                                   pd_sensitivity={0.02: pd})
    return CreditRead(domain_verdict=None, scored=scored, altman_z_double_prime=z, altman_zone=zone)


def test_equity_cushion_and_net_cash():
    assert equity_cushion(100.0, 300.0) == pytest.approx(0.25)
    assert equity_cushion(100.0, -50.0) == 1.0          # net cash: no debt claim ahead of equity
    assert equity_cushion(0.0, 10.0) is None


def test_cyh_live_numbers_support_and_are_robust():
    r = credit_equity_coherence(read(pd=0.0726), **CYH, method_mids=[94.34, 24.84, 0.0])
    assert r.verdict is Verdict.SUPPORTS and r.credit_elevated and r.market_stressed
    assert r.equity_cushion == pytest.approx(0.0374, abs=5e-4)
    assert r.robust is True
    assert r.market_to_median_value == pytest.approx(2.79 / 24.84)


def test_jnj_live_numbers_support_and_are_robust():
    r = credit_equity_coherence(read(pd=0.0078), **JNJ, method_mids=[251.07, 139.99, 326.28])
    assert r.verdict is Verdict.SUPPORTS and not r.credit_elevated and not r.market_stressed
    assert r.robust is True


def test_elevated_credit_with_a_thick_cushion_undercuts():
    r = credit_equity_coherence(read(pd=0.0726), **JNJ)
    assert r.verdict is Verdict.UNDERCUTS
    assert "disagree" in r.explain()


def test_benign_credit_with_a_thin_cushion_undercuts():
    r = credit_equity_coherence(read(pd=0.005), **CYH)
    assert r.verdict is Verdict.UNDERCUTS


def test_near_threshold_verdict_is_flagged_fragile():
    # cushion 22% (market cap 22 vs net debt 78) sits between the 15% and 35% robustness bounds
    r = credit_equity_coherence(read(pd=0.10), market_price=22.0, shares_outstanding=1.0, net_debt=78.0)
    assert r.robust is False and "FRAGILE" in r.explain()


def test_altman_fallback_when_pd_suppressed():
    r = credit_equity_coherence(read(z=0.28, zone="distress"), **CYH)
    assert r.credit_elevated is True and r.pd_multiple is None
    assert r.verdict is Verdict.SUPPORTS


def test_silent_and_unpriced():
    assert credit_equity_coherence(read(), **CYH).verdict is Verdict.SILENT
    r = credit_equity_coherence(read(pd=0.0726), market_price=None, shares_outstanding=None, net_debt=None)
    assert r.verdict is Verdict.UNPRICED


def test_the_old_floor_degeneracy_cannot_recur():
    """The retired rule: with any method floored at $0, 'market below the lowest method'
    could never fire. The new verdict ignores method floors entirely."""
    a = credit_equity_coherence(read(pd=0.0726), **CYH, method_mids=[0.0, 0.0, 0.0])
    b = credit_equity_coherence(read(pd=0.0726), **CYH, method_mids=[94.34, 24.84, 0.0])
    assert a.verdict is b.verdict is Verdict.SUPPORTS


def test_threshold_is_the_stated_principled_value():
    assert DEFAULT_CUSHION_THRESHOLD == 0.25
