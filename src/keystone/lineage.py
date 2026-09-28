"""shared_input_lineage.py — which subject-side Trellis fields each ValuationLab
method actually reads, verified against source (dcf.py, comps.py, precedent.py,
and how scripts/run_valuation.py assembles precedent's inputs) — not assumed.

`MethodRange.provenance` is a free-text string in triangulate.py; there's no
machine-readable lineage there to introspect. This module is the independent
map Keystone needs instead, built by reading what each function actually pulls
from the subject's base-year Trellis table (`base_year_actuals` in dcf.py,
`subject_year_data` in comps.py's implied_value, and the fields run_valuation.py
extracts by hand before calling precedent.apply_multiple).

Non-obvious finding from doing this precisely, not from assuming "everything
touches Trellis so everything is shared": under the EV/EBITDA comps basis
(scripts/run_valuation.py's actual, real configuration), the only fields
genuinely shared by all three methods are long_term_debt and
cash_and_equivalents — both feed each method's own net-debt bridge
(EV -> equity value). revenue is NOT a shared input in this configuration:
only precedent reads it (comps would too, under an ev_revenue basis, but the
real run uses ev_ebitda). operating_income and depreciation_amortization are
shared by comps and precedent, not DCF — DCF's own operating_income comes from
Trellis's separately-derived *forecast* years, not the base-year table, so a
base-year-only corruption doesn't reach DCF's calculation at all when tested
against a frozen snapshot (see run_planted_corruption_experiment.py's own
notes for what this does and doesn't prove).
"""
from __future__ import annotations

DCF_SUBJECT_FIELDS: frozenset[str] = frozenset({
    "long_term_debt", "cash_and_equivalents",
})

COMPS_SUBJECT_FIELDS_BY_BASIS: dict[str, frozenset[str]] = {
    "ev_revenue": frozenset({"revenue", "long_term_debt", "cash_and_equivalents"}),
    "ev_ebitda": frozenset({
        "operating_income", "depreciation_amortization",
        "long_term_debt", "cash_and_equivalents",
    }),
}

PRECEDENT_SUBJECT_FIELDS: frozenset[str] = frozenset({
    "revenue", "operating_income", "depreciation_amortization",
    "long_term_debt", "cash_and_equivalents",
})

METHODS = ("dcf", "comps", "precedent")


def method_fields(method: str, comps_basis: str = "ev_ebitda") -> frozenset[str]:
    if method == "dcf":
        return DCF_SUBJECT_FIELDS
    if method == "comps":
        try:
            return COMPS_SUBJECT_FIELDS_BY_BASIS[comps_basis]
        except KeyError:
            raise ValueError(
                f"unknown comps_basis {comps_basis!r} — expected one of "
                f"{sorted(COMPS_SUBJECT_FIELDS_BY_BASIS)}") from None
    if method == "precedent":
        return PRECEDENT_SUBJECT_FIELDS
    raise ValueError(f"unknown method {method!r} — expected one of {METHODS}")


def overlap(method_a: str, method_b: str,
            comps_basis: str = "ev_ebitda") -> tuple[frozenset[str], float]:
    """Returns (shared fields, Jaccard overlap fraction = |shared| / |union|)."""
    fa = method_fields(method_a, comps_basis)
    fb = method_fields(method_b, comps_basis)
    shared = fa & fb
    union = fa | fb
    fraction = len(shared) / len(union) if union else 0.0
    return shared, fraction


def all_pairwise_overlaps(comps_basis: str = "ev_ebitda"
                           ) -> dict[tuple[str, str], tuple[frozenset[str], float]]:
    """One entry per unordered method pair — always computed, not conditional
    on whether the pair's divergence reading happens to be 'Converge'. How much
    two methods' conclusions depend on the same raw numbers is context worth
    having regardless of whether they currently agree."""
    out = {}
    for i, a in enumerate(METHODS):
        for b in METHODS[i + 1:]:
            out[(a, b)] = overlap(a, b, comps_basis)
    return out
