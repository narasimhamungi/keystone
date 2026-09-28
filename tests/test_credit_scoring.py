"""test_credit_scoring.py — against CreditRiskLab's own synthetic demo data
(creditrisklab.synthetic), the same offline, no-network fixture used to
verify credit_domain_screen.py earlier. Real code path (real fit(), real
prior_correct()), synthetic company data, watermarked as such by
CreditRiskLab's own synthetic module -- not live EDGAR, by design, same as
before."""
import pytest

from creditrisklab.config import feature_names, load_model_config
from creditrisklab.features.panel import build_panel
from creditrisklab.synthetic import build_fundamentals, stamped_issuers
from creditrisklab.universe import load_issuers

from keystone.domain_screen import IncompleteSubjectError, compute_training_ranges
from keystone.credit import (
    CreditRead,
    ScoredIssuer,
    credit_read,
    fit_from_panel,
    score_new_issuer,
    score_with_fitted_pipe,
)

FEATURES = feature_names(load_model_config())


@pytest.fixture(scope="module")
def panel():
    issuers = stamped_issuers(load_issuers())
    fundamentals = build_fundamentals(issuers, seed=42)
    return build_panel(issuers, fundamentals)


@pytest.fixture(scope="module")
def ranges(panel):
    return compute_training_ranges(panel.to_dict("records"))


def _ratios_for_label(panel, label):
    row = panel[panel["label"] == label].iloc[0]
    return {f: row[f] for f in FEATURES}


def test_scored_positive_label_gets_higher_pd_than_negative(panel):
    pipe = fit_from_panel(panel)
    sample_rate = float(panel["label"].astype(int).mean())
    neg = score_with_fitted_pipe(_ratios_for_label(panel, 0), pipe, sample_rate)
    pos = score_with_fitted_pipe(_ratios_for_label(panel, 1), pipe, sample_rate)
    assert pos.pd > neg.pd


def test_pd_is_a_probability(panel):
    scored = score_new_issuer(_ratios_for_label(panel, 1), panel)
    assert 0.0 <= scored.pd <= 1.0
    assert 0.0 <= scored.raw_probability <= 1.0


def test_sensitivity_grid_monotonic_in_tau(panel):
    """Higher assumed population default rate -> higher corrected PD, holding
    the raw model output fixed -- prior_correct's own documented direction."""
    scored = score_new_issuer(_ratios_for_label(panel, 1), panel)
    taus = sorted(scored.pd_sensitivity)
    pds = [scored.pd_sensitivity[t] for t in taus]
    assert pds == sorted(pds)


def test_missing_feature_raises_incomplete_subject_error(panel):
    ratios = _ratios_for_label(panel, 0)
    del ratios[FEATURES[0]]
    with pytest.raises(IncompleteSubjectError):
        score_new_issuer(ratios, panel)


def test_fit_once_score_many_matches_convenience_function(panel):
    """score_new_issuer (refits every call) and fit_from_panel +
    score_with_fitted_pipe (fit once) should agree exactly on the same input —
    they're the same underlying computation, just batched differently."""
    ratios = _ratios_for_label(panel, 1)
    a = score_new_issuer(ratios, panel)
    pipe = fit_from_panel(panel)
    sample_rate = float(panel["label"].astype(int).mean())
    b = score_with_fitted_pipe(ratios, pipe, sample_rate)
    assert a.pd == pytest.approx(b.pd)
    assert a.raw_probability == pytest.approx(b.raw_probability)


def test_feature_order_assertion_catches_schema_drift(panel, monkeypatch):
    """If credit_domain_screen.FEATURES and model.yaml's real feature order
    ever diverge, this should fail loudly, not silently score against the
    wrong columns. Patches credit_scoring's own bound name directly -- `from
    X import Y` copies the reference at import time, so patching X.Y after
    the fact (as a first draft of this test did) doesn't reach a name already
    bound into this module's namespace."""
    import keystone.credit as credit_scoring
    monkeypatch.setattr(credit_scoring, "FEATURES", ("not", "the", "real", "features"))
    with pytest.raises(AssertionError):
        score_new_issuer(_ratios_for_label(panel, 0), panel)


def test_credit_read_in_domain_populates_scored(panel, ranges):
    ratios = _ratios_for_label(panel, 0)
    read = credit_read(ratios, ranges, panel)
    assert isinstance(read, CreditRead)
    assert read.domain_verdict.in_domain
    assert isinstance(read.scored, ScoredIssuer)
    assert read.altman_z_double_prime is not None


def test_credit_read_out_of_domain_suppresses_pd_but_keeps_altman(panel, ranges):
    ratios = dict(_ratios_for_label(panel, 0))
    ratios["log_assets"] = 999.0  # guaranteed out of any real training range
    read = credit_read(ratios, ranges, panel)
    assert not read.domain_verdict.in_domain
    assert read.scored is None
    assert read.altman_z_double_prime is not None  # domain-independent, still computed


def test_credit_read_explain_never_shows_pd_when_out_of_domain(panel, ranges):
    ratios = dict(_ratios_for_label(panel, 0))
    ratios["log_assets"] = 999.0
    read = credit_read(ratios, ranges, panel)
    assert "PD =" not in read.explain()
    assert "suppressed" in read.explain()


def test_credit_read_missing_feature_does_not_raise(panel, ranges):
    """Regression test: an earlier version of run_domain_screen_experiment.py
    called credit_read() without a try/except, on the (wrong) assumption that
    a missing-feature subject would come back as an ordinary out-of-domain
    CreditRead. It doesn't -- screen() raises IncompleteSubjectError, which
    used to propagate straight out of credit_read() uncaught. This is the
    fix: credit_read() must never raise for this case, for any caller."""
    ratios = {k: v for k, v in _ratios_for_label(panel, 0).items() if k != "wc_ta"}
    read = credit_read(ratios, ranges, panel)  # must not raise
    assert read.domain_verdict is None
    assert read.scored is None
    assert read.incomplete_reason is not None
    assert "wc_ta" in read.incomplete_reason


def test_credit_read_missing_feature_explain_is_distinct_from_out_of_domain(panel, ranges):
    ratios = {k: v for k, v in _ratios_for_label(panel, 0).items() if k != "wc_ta"}
    read = credit_read(ratios, ranges, panel)
    text = read.explain()
    assert "Cannot screen" in text
    assert "PD =" not in text
