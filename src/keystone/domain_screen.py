"""credit_domain_screen.py — Keystone's applicability screen for CreditRiskLab's PD model.

CreditRiskLab has no inference-time domain check today. `universe.yaml` /
`universe.py` gate which issuers are used as *training labels* — nothing in the
codebase checks whether a new, unlabeled company resembles that training
population before a PD is reported for it (confirmed by reading the code and by
grepping the whole repo for domain/distance/range/applicability logic — nothing
found outside cross-validation's own "out-of-sample fold" bookkeeping, which is
a different concept).

Design decision, locked in Keystone's architecture doc: a per-feature range
screen against the training cohort, not Mahalanobis distance. At the training
panel's real size — 105 issuer-years, 15 issuers, 10 positive (defaulted) —
the 12-feature covariance matrix is near-singular, so a Mahalanobis distance
would be a number computed from an unstable, near-degenerate matrix: precise-
looking, not trustworthy. A per-feature min/max range is a weaker claim, and
it's the claim this sample size can actually support.

This is a heuristic screen, not a validated statistical boundary. Say so in
any report that surfaces it — don't let the DomainVerdict's clean shape imply
more rigor than a 15-issuer panel can carry.

CAP-DEGENERACY FIX (found by running this against the real panel, not by
reading the code): `debt_ebitda` and `interest_cover` train ranges came back
as [0.28, 50.00] and [-50.00, 50.00]. Those upper/lower bounds are
creditrisklab.features.ratios.CAPS — the logistic regression's winsorization
cap — not real company extremes; at least one training issuer saturated the
cap rather than genuinely reaching it. A range bounded by an arbitrary cap is
wide enough that almost nothing will ever be flagged on that feature, which
is silent non-discrimination wearing the shape of a working check. Fixed
below by detecting when a computed bound coincides with the known cap and
surfacing that explicitly (min_is_capped / max_is_capped / low_confidence),
rather than by trying to reconstruct the uncapped ratio ourselves —
duplicating creditrisklab's private pre-cap computation would be a second,
divergent copy of logic that already lives in one place, exactly the kind of
split-source risk this whole portfolio's comments keep warning about
elsewhere (Trellis's alias/priority merge rationale, DealLab's
"cannot fall back to a plausible default"). This module still doesn't import
creditrisklab for its own sake — CAPS is optional, imported defensively, with
a hardcoded fallback used ONLY if CreditRiskLab isn't installed, and that
fallback is loud about being a possibly-stale copy, not silent.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Mapping, Sequence

# Must match creditrisklab.features.ratios.compute_ratios()'s output keys exactly.
# Binary features (negative_equity, two_year_loss) are included: for those, "range"
# collapses to "was this value (0 or 1) ever observed in training", which is the
# right question for a binary feature anyway.
FEATURES: tuple[str, ...] = (
    "wc_ta", "re_ta", "ebit_ta", "equity_tl", "current_ratio",
    "debt_ebitda", "interest_cover", "cfo_total_debt", "net_margin",
    "log_assets", "negative_equity", "two_year_loss",
)

try:
    from creditrisklab.features.ratios import CAPS as _RATIO_CAPS
except ImportError:
    warnings.warn(
        "credit_domain_screen: could not import CAPS from "
        "creditrisklab.features.ratios (CreditRiskLab not installed here) — "
        "falling back to a hardcoded copy that may drift out of sync with the "
        "real one. Cap-derived-boundary detection may be wrong if CreditRiskLab's "
        "CAPS dict has changed since this copy was taken.",
        stacklevel=2,
    )
    _RATIO_CAPS: dict[str, tuple[float, float]] = {
        "debt_ebitda": (-50.0, 50.0),
        "interest_cover": (-50.0, 50.0),
        "cfo_total_debt": (-5.0, 5.0),
        "current_ratio": (0.0, 20.0),
        "equity_tl": (-10.0, 20.0),
        "net_margin": (-5.0, 5.0),
        "wc_ta": (-5.0, 5.0),
        "re_ta": (-20.0, 5.0),
        "ebit_ta": (-5.0, 5.0),
    }
# log_assets, negative_equity, two_year_loss are not in CAPS — uncapped, or
# (the two binaries) already naturally bounded to {0, 1}.


def _is_capped_bound(feature: str, bound: float) -> bool:
    cap = _RATIO_CAPS.get(feature)
    return cap is not None and bound in (cap[0], cap[1])


@dataclass(frozen=True)
class FeatureRange:
    feature: str
    train_min: float
    train_max: float
    n_observed: int  # how many training rows actually had this feature (missingness varies)
    min_is_capped: bool  # True if train_min equals this feature's known winsorization floor
    max_is_capped: bool  # True if train_max equals this feature's known winsorization ceiling


@dataclass(frozen=True)
class FeatureVerdict:
    feature: str
    value: float
    train_min: float
    train_max: float
    in_range: bool
    low_confidence: bool  # True if either bound is cap-derived, not a real observed extreme


@dataclass(frozen=True)
class DomainVerdict:
    in_domain: bool
    feature_verdicts: tuple[FeatureVerdict, ...]
    driving_features: tuple[str, ...]  # feature(s) that put the subject out of domain
    low_confidence_features: tuple[str, ...]  # in-range, but only because a cap widened the window

    def explain(self) -> str:
        if self.in_domain:
            base = ("In domain: within the training cohort's observed range on "
                    "all available features. This means 'resembles the cohort on "
                    "these 12 ratios' — not 'the PD is accurate', and not a "
                    "statistically validated boundary at n=105.")
            if self.low_confidence_features:
                names = ", ".join(self.low_confidence_features)
                plural = len(self.low_confidence_features) != 1
                base += (f" Treat this with less confidence on {names}: the training "
                         f"range there is widened by a modeling cap, not a real company "
                         f"extreme, so 'in range' on {'those features' if plural else 'that feature'} "
                         f"carries less information than the others.")
            return base
        detail = "; ".join(
            f"{v.feature}={v.value:.3f} (training range "
            f"[{v.train_min:.3f}, {v.train_max:.3f}])"
            for v in self.feature_verdicts if not v.in_range
        )
        return (f"Out of domain on {len(self.driving_features)} feature(s): {detail}. "
                f"Absolute PD suppressed — report the qualitative feature comparison "
                f"instead, not a suppressed-but-still-shown number.")


class IncompleteTrainingPanelError(ValueError):
    """A feature has zero non-missing observations across the whole training panel.
    Raised, not silently skipped — a feature the screen can't check on is a feature
    the domain claim can't cover, and that should stop the run, not quietly narrow it."""


class IncompleteSubjectError(ValueError):
    """The subject is missing a feature the screen needs. Raised, not defaulted —
    the same 'raise, don't default' discipline the rest of this portfolio uses
    (DealLab's MissingInputError, ValuationLab's comps refusing a missing component)."""


def compute_training_ranges(
    training_panel: Sequence[Mapping[str, float | None]],
) -> tuple[FeatureRange, ...]:
    """training_panel: one dict per labeled issuer-year, keys matching FEATURES,
    values as returned by creditrisklab.features.ratios.compute_ratios(). Pass the
    FULL labeled panel — defaults and non_defaults combined. The question this
    screen asks is "does the subject resemble ANYTHING in the cohort", not
    "does it resemble specifically the survivors" — narrowing to one class would
    answer a different, easier question than the one CreditRiskLab's PD needs
    covered.
    """
    ranges = []
    for feat in FEATURES:
        values = [row[feat] for row in training_panel
                  if row.get(feat) is not None]
        if not values:
            raise IncompleteTrainingPanelError(
                f"No non-missing training values for feature {feat!r} — "
                f"panel has {len(training_panel)} rows but none carry this feature.")
        lo, hi = min(values), max(values)
        ranges.append(FeatureRange(
            feat, lo, hi, len(values),
            min_is_capped=_is_capped_bound(feat, lo),
            max_is_capped=_is_capped_bound(feat, hi),
        ))
    return tuple(ranges)


def screen(
    subject_ratios: Mapping[str, float | None],
    training_ranges: Sequence[FeatureRange],
) -> DomainVerdict:
    verdicts = []
    for r in training_ranges:
        value = subject_ratios.get(r.feature)
        if value is None:
            raise IncompleteSubjectError(
                f"Subject is missing required feature {r.feature!r} — "
                f"cannot screen without it.")
        in_range = r.train_min <= value <= r.train_max
        low_conf = r.min_is_capped or r.max_is_capped
        verdicts.append(FeatureVerdict(
            r.feature, value, r.train_min, r.train_max, in_range, low_conf,
        ))
    driving = tuple(v.feature for v in verdicts if not v.in_range)
    low_confidence = tuple(v.feature for v in verdicts if v.in_range and v.low_confidence)
    return DomainVerdict(
        in_domain=not driving,
        feature_verdicts=tuple(verdicts),
        driving_features=driving,
        low_confidence_features=low_confidence,
    )
