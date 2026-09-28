"""test_credit_domain_screen.py

IMPORTANT — fact discipline: this sandbox has no SEC EDGAR access (confirmed:
https://data.sec.gov returns 403 through the egress proxy this environment
uses). None of the numbers below came from CreditRiskLab's actual
compute_ratios() run against real filings. They are hand-built, illustrative
fixtures, constructed to be roughly directionally consistent with public
information already gathered earlier in this project (J&J and Pfizer as
deep-investment-grade pharma; Community Health Systems' September 2026 credit
profile — Debt/EBITDA ~5.8x, interest coverage ~1.1-1.3x, negative equity,
Altman Z-score ~1.06 — found via web search, not computed by CreditRiskLab).

These tests validate the SCREEN'S LOGIC — does it correctly classify a value
as in/out of a given range, does it raise on missing data, does it report the
right driving features. They do NOT validate what CreditRiskLab would actually
report for J&J, Pfizer, or CYH. Running that is the subject of
run_domain_screen_experiment.py, which needs to run somewhere with real EDGAR
access — this sandbox isn't it.
"""
import pytest

from keystone.domain_screen import (
    FEATURES,
    DomainVerdict,
    IncompleteSubjectError,
    IncompleteTrainingPanelError,
    compute_training_ranges,
    screen,
)

# Six illustrative training rows (real CreditRiskLab trains on 15 issuers / 105
# issuer-years — this is a small stand-in, large enough to exercise the logic,
# not a substitute for the real panel).
_SAFE_ROW = {
    "wc_ta": 0.15, "re_ta": 0.40, "ebit_ta": 0.18, "equity_tl": 1.20,
    "current_ratio": 1.30, "debt_ebitda": 1.8, "interest_cover": 18.0,
    "cfo_total_debt": 0.55, "net_margin": 0.20, "log_assets": 12.5,
    "negative_equity": 0.0, "two_year_loss": 0.0,
}
_DISTRESSED_ROW = {
    "wc_ta": -0.05, "re_ta": -0.30, "ebit_ta": 0.03, "equity_tl": -0.40,
    "current_ratio": 0.85, "debt_ebitda": 6.5, "interest_cover": 1.1,
    "cfo_total_debt": 0.06, "net_margin": -0.02, "log_assets": 9.8,
    "negative_equity": 1.0, "two_year_loss": 1.0,
}


def _jitter(row: dict, seed: float) -> dict:
    """Small deterministic variation so min/max spans more than one point."""
    return {k: (v + seed if isinstance(v, float) and k not in
                 ("negative_equity", "two_year_loss") else v)
            for k, v in row.items()}


TRAINING_PANEL = (
    [_jitter(_SAFE_ROW, s) for s in (0.0, 0.01, -0.01)]
    + [_jitter(_DISTRESSED_ROW, s) for s in (0.0, 0.02, -0.02)]
)


def test_compute_training_ranges_covers_all_features():
    ranges = compute_training_ranges(TRAINING_PANEL)
    assert {r.feature for r in ranges} == set(FEATURES)


def test_compute_training_ranges_raises_on_missing_feature():
    panel = [{k: v for k, v in _SAFE_ROW.items() if k != "wc_ta"}]
    with pytest.raises(IncompleteTrainingPanelError):
        compute_training_ranges(panel)


def test_subject_resembling_safe_cohort_is_in_domain():
    ranges = compute_training_ranges(TRAINING_PANEL)
    # Illustrative "J&J-shaped" vector: at the safe cluster's own centre point,
    # so it's inside the jittered [min, max] span on every feature by construction.
    subject = dict(_SAFE_ROW)
    verdict = screen(subject, ranges)
    assert verdict.in_domain
    assert verdict.driving_features == ()


def test_subject_far_outside_cohort_is_out_of_domain():
    ranges = compute_training_ranges(TRAINING_PANEL)
    # Illustrative "extremely conservative, off-the-charts" vector — designed
    # to sit outside the small synthetic panel's range on purpose.
    subject = {**_SAFE_ROW, "debt_ebitda": 0.05, "interest_cover": 500.0}
    verdict = screen(subject, ranges)
    assert not verdict.in_domain
    assert "interest_cover" in verdict.driving_features
    assert "debt_ebitda" in verdict.driving_features


def test_subject_resembling_distressed_cohort_is_in_domain():
    ranges = compute_training_ranges(TRAINING_PANEL)
    # Illustrative "CYH-shaped" vector, per the September 2026 figures noted
    # above — NOT CreditRiskLab's computed output for CYH.
    subject = {**_DISTRESSED_ROW, "debt_ebitda": 5.8, "interest_cover": 1.2}
    verdict = screen(subject, ranges)
    assert verdict.in_domain


def test_screen_raises_on_missing_subject_feature():
    ranges = compute_training_ranges(TRAINING_PANEL)
    subject = {k: v for k, v in _SAFE_ROW.items() if k != "current_ratio"}
    with pytest.raises(IncompleteSubjectError):
        screen(subject, ranges)


def test_explain_names_driving_features_out_of_domain():
    ranges = compute_training_ranges(TRAINING_PANEL)
    subject = {**_SAFE_ROW, "log_assets": 30.0}  # absurdly large, out of range
    verdict = screen(subject, ranges)
    assert "log_assets" in verdict.explain()
    assert "PD suppressed" in verdict.explain()


def test_explain_in_domain_does_not_overclaim():
    ranges = compute_training_ranges(TRAINING_PANEL)
    subject = dict(_SAFE_ROW)
    verdict = screen(subject, ranges)
    text = verdict.explain()
    assert "not 'the PD is accurate'" in text


# --- Cap-degeneracy tests -----------------------------------------------
# Real finding from the live run (documented in credit_domain_screen.py's
# module docstring): debt_ebitda/interest_cover's training ranges came back
# as [0.28, 50.00] / [-50.00, 50.00] — 50.0 and -50.0 are the winsorization
# cap from creditrisklab.features.ratios.CAPS, not real company extremes.
# These tests build a panel where one training row deliberately hits that
# cap, and check the screen surfaces it instead of silently trusting it.

_CAP_HIT_ROW = {**_DISTRESSED_ROW, "debt_ebitda": 50.0}  # saturates the real cap


def test_training_range_flags_capped_max_only_on_the_saturating_feature():
    panel = TRAINING_PANEL + [_CAP_HIT_ROW]
    ranges = {r.feature: r for r in compute_training_ranges(panel)}
    assert ranges["debt_ebitda"].max_is_capped is True
    assert ranges["debt_ebitda"].min_is_capped is False  # low end (0.28-ish) is real
    # A feature nothing in the panel pushed to its cap should read clean.
    assert ranges["net_margin"].max_is_capped is False
    assert ranges["net_margin"].min_is_capped is False


def test_screen_marks_low_confidence_when_in_range_only_via_cap():
    panel = TRAINING_PANEL + [_CAP_HIT_ROW]
    ranges = compute_training_ranges(panel)
    # In range on debt_ebitda only because the cap widened the window to 50 —
    # genuinely nowhere near any real training company's actual leverage.
    subject = {**_SAFE_ROW, "debt_ebitda": 45.0}
    verdict = screen(subject, ranges)
    assert verdict.in_domain  # still technically in range
    assert "debt_ebitda" in verdict.low_confidence_features


def test_explain_mentions_low_confidence_features():
    panel = TRAINING_PANEL + [_CAP_HIT_ROW]
    ranges = compute_training_ranges(panel)
    subject = {**_SAFE_ROW, "debt_ebitda": 45.0}
    verdict = screen(subject, ranges)
    text = verdict.explain()
    assert "debt_ebitda" in text
    assert "less confidence" in text


def test_uncapped_feature_is_not_flagged_low_confidence():
    # debt_ebitda IS legitimately low-confidence in this panel (one row
    # saturates the real cap) — for ANY subject value, not just values near
    # the cap, since we deliberately don't reconstruct the true uncapped
    # extreme (see module docstring). What should stay clean is a feature
    # nothing in this panel ever pushed to its cap.
    panel = TRAINING_PANEL + [_CAP_HIT_ROW]
    ranges = compute_training_ranges(panel)
    subject = dict(_SAFE_ROW)
    verdict = screen(subject, ranges)
    assert "debt_ebitda" in verdict.low_confidence_features  # expected, not a bug
    assert "net_margin" not in verdict.low_confidence_features

