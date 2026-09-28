"""credit_scoring.py — scores a genuinely new, unlabeled company against
CreditRiskLab's real logistic model. This function does not exist anywhere in
CreditRiskLab today (confirmed: pipeline.py's run() produces out-of-fold
predictions for the labeled training panel only; there is no predict-one-new-
company entry point). Built from CreditRiskLab's own real, tested pieces
(models/logistic.py::fit, models/calibration.py::prior_correct), not a
reimplementation of them.

DESIGN DECISION, stated rather than hidden: the real pipeline (pipeline.py's
run()) applies two corrections to get a reported PD: cross-fitted isotonic/
Platt recalibration (cross_fitted_calibration), THEN prior_correct(). This
module applies only prior_correct(). Reason: cross_fitted_calibration fits a
separate OutOfFoldCalibrator PER CROSS-VALIDATION FOLD, each discarded after
transforming that fold's held-out slice -- the function returns the panel's
own calibrated out-of-fold predictions, not one reusable calibrator object a
new, never-folded company could be passed through. Fitting a single calibrator
on the full labeled OOF set as a substitute would be a real design choice
CreditRiskLab's own code doesn't make anywhere, and at 10 positives it
wouldn't even use isotonic regardless (OutOfFoldCalibrator's own fallback
threshold is min_positives_for_isotonic=25 -- always Platt at this N). Skipping
it and saying so is more honest than quietly approximating a step the model
doesn't cleanly support yet for new-point scoring.

Consistent with credit_domain_screen.py's discipline: raises rather than
imputes a missing subject feature (reuses that module's
IncompleteSubjectError, not a new exception class, since it's the same
failure mode).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from creditrisklab.config import feature_names, load_model_config
from creditrisklab.models import logistic
from creditrisklab.models.calibration import prior_correct

from keystone.domain_screen import FEATURES, IncompleteSubjectError

# Matches config/model.yaml's own stated central estimate and cited basis
# (S&P Global / Moody's annual default studies, ~1.0-1.7% all-rated). Kept
# here rather than re-typed as a bare 0.02 so the source is visible at the
# call site, not just in a comment in a different file.
DEFAULT_POPULATION_RATE = 0.02
SENSITIVITY_GRID = (0.01, 0.02, 0.03)  # matches model.yaml's own stated sensitivity range


@dataclass(frozen=True)
class ScoredIssuer:
    raw_probability: float          # pipeline's own predict_proba, sample-space (not meaningful alone)
    sample_default_rate: float      # empirical positive rate in the training panel used to fit
    population_default_rate: float  # tau used for the headline pd
    pd: float                       # prior_correct(raw_probability, population_default_rate, sample_default_rate)
    pd_sensitivity: dict[float, float]  # tau -> corrected pd, across SENSITIVITY_GRID
    calibration_note: str = (
        "prior_correct only -- cross-fitted isotonic/Platt recalibration skipped "
        "for new-issuer scoring (see module docstring); at this panel's positive "
        "count that step would fall back to Platt regardless.")


def fit_from_panel(panel: pd.DataFrame, model_cfg: dict | None = None) -> Pipeline:
    """Thin, explicit wrapper over logistic.fit -- exists so a caller scoring
    several new companies fits once and reuses the pipeline, rather than
    every score_new_issuer() call silently refitting from scratch."""
    return logistic.fit(panel, model_cfg or load_model_config())


def _feature_row(subject_ratios: Mapping[str, float | None],
                  features: Sequence[str]) -> np.ndarray:
    missing = [f for f in features if subject_ratios.get(f) is None]
    if missing:
        raise IncompleteSubjectError(
            f"Subject is missing required feature(s) {missing!r} -- cannot "
            f"score without them. (Not imputed: a new company's own missing "
            f"ratio silently filled from the training median would misrepresent "
            f"what's actually known about it.)")
    return np.array([[float(subject_ratios[f]) for f in features]], dtype="float64")


def score_with_fitted_pipe(
    subject_ratios: Mapping[str, float | None],
    fitted_pipe: Pipeline,
    sample_default_rate: float,
    *,
    population_default_rate: float = DEFAULT_POPULATION_RATE,
    sensitivity_grid: Sequence[float] = SENSITIVITY_GRID,
    model_cfg: dict | None = None,
) -> ScoredIssuer:
    cfg = model_cfg or load_model_config()
    features = feature_names(cfg)
    assert tuple(features) == FEATURES, (
        f"config/model.yaml's feature order {features!r} no longer matches "
        f"credit_domain_screen.FEATURES {FEATURES!r} -- these two modules "
        f"must agree on feature identity; update one to match the other, "
        f"don't let them silently diverge.")

    row = _feature_row(subject_ratios, features)
    raw_p = float(fitted_pipe.predict_proba(row)[0, 1])
    corrected = float(prior_correct(raw_p, population_default_rate, sample_default_rate))
    sensitivity = {
        tau: float(prior_correct(raw_p, tau, sample_default_rate))
        for tau in sensitivity_grid
    }
    return ScoredIssuer(
        raw_probability=raw_p,
        sample_default_rate=sample_default_rate,
        population_default_rate=population_default_rate,
        pd=corrected,
        pd_sensitivity=sensitivity,
    )


def score_new_issuer(
    subject_ratios: Mapping[str, float | None],
    training_panel: pd.DataFrame,
    *,
    population_default_rate: float = DEFAULT_POPULATION_RATE,
    sensitivity_grid: Sequence[float] = SENSITIVITY_GRID,
    model_cfg: dict | None = None,
) -> ScoredIssuer:
    """Convenience entry point: fits fresh on training_panel, then scores.
    For scoring several companies against the same panel, call
    fit_from_panel() once and use score_with_fitted_pipe() directly instead --
    this refits every call, which is correct but wasteful for a batch."""
    cfg = model_cfg or load_model_config()
    pipe = fit_from_panel(training_panel, cfg)
    sample_rate = float(training_panel["label"].astype(int).mean())
    return score_with_fitted_pipe(
        subject_ratios, pipe, sample_rate,
        population_default_rate=population_default_rate,
        sensitivity_grid=sensitivity_grid, model_cfg=cfg,
    )


# --- Gated read: combines credit_domain_screen's verdict with score_new_issuer ---
from creditrisklab.models.altman import z_double_prime, zone as altman_zone
from keystone.domain_screen import DomainVerdict, FeatureRange


@dataclass(frozen=True)
class CreditRead:
    """What Keystone's report actually shows for the credit leg.

    domain_verdict is None when the subject is missing a feature the screen
    itself needs (e.g. a bank missing wc_ta) -- genuinely different from an
    in-domain verdict that came back False, and reported differently:
    incomplete_reason explains why no verdict could be reached at all, rather
    than the domain_verdict.explain() text for "reached a verdict, it was
    out-of-domain." scored is None in both that case and the ordinary
    out-of-domain case -- the PD is never computed and shown "with a caveat"
    alongside a refusal, matching the architecture doc's locked design."""
    domain_verdict: DomainVerdict | None
    scored: ScoredIssuer | None
    altman_z_double_prime: float | None  # always attempted independently -- no domain dependency
    altman_zone: str
    incomplete_reason: str | None = None  # populated only when domain_verdict is None

    def explain(self) -> str:
        z_text = (f"Altman Z\u2033 = {self.altman_z_double_prime:.2f} ({self.altman_zone})"
                  if self.altman_z_double_prime is not None else "Altman Z\u2033 = unscored")
        if self.domain_verdict is None:
            return f"Cannot screen: {self.incomplete_reason}\n{z_text} (attempted independently)."
        if self.scored is None:
            return f"{self.domain_verdict.explain()}\n{z_text} (domain-independent, still shown)."
        return (f"{self.domain_verdict.explain()}\nPD = {self.scored.pd:.2%} "
                f"(population default rate {self.scored.population_default_rate:.1%}). {z_text}.")


def credit_read(
    subject_ratios: Mapping[str, float | None],
    training_ranges: Sequence[FeatureRange],
    training_panel: pd.DataFrame,
    *,
    population_default_rate: float = DEFAULT_POPULATION_RATE,
) -> CreditRead:
    """The single entry point Keystone's report assembly should call for the
    credit leg. Always returns a CreditRead -- never lets IncompleteSubjectError
    propagate to the caller, the same "don't trust every future caller to
    remember a try/except" reasoning behind convergence_diagnostic()'s
    mandatory parameters. Computes Altman Z'' first, independently of whether
    the domain screen can even run, since a feature missing for the screen
    isn't necessarily one Z'' needs (though for a bank's missing wc_ta, both
    fail together -- wc_ta is one of Z'''s own four inputs too)."""
    from keystone.domain_screen import IncompleteSubjectError, screen

    try:
        z = z_double_prime(subject_ratios)
    except Exception:  # noqa: BLE001 -- z_double_prime returns None on missing inputs; belt+suspenders
        z = None
    z_zone = altman_zone(z)

    try:
        verdict = screen(subject_ratios, training_ranges)
    except IncompleteSubjectError as exc:
        return CreditRead(domain_verdict=None, scored=None,
                           altman_z_double_prime=z, altman_zone=z_zone,
                           incomplete_reason=str(exc))

    scored = None
    if verdict.in_domain:
        scored = score_new_issuer(subject_ratios, training_panel,
                                   population_default_rate=population_default_rate)

    return CreditRead(domain_verdict=verdict, scored=scored,
                       altman_z_double_prime=z, altman_zone=z_zone)
