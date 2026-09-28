"""convergence_diagnostic.py — Keystone's wrapper around valuationlab.triangulate.

"Convergence diagnostic" is the doc's renamed term for what triangulate.py
already does (a disagreement-and-disqualification engine, not a blender) — see
the architecture doc's "The convergence diagnostic" section. This module is
what Keystone adds on top of the real, unmodified triangulate() function, not
a replacement for it:

1. MANDATORY MEASURED PARAMETERS. triangulate()'s five measured-check
   parameters (terminal_value_share, equity_weight_in_wacc,
   precedent_tier_spread, comps_multiple_spread, comps_peer_count) are all
   optional in its own signature — confirmed by reading triangulate.py:335-341.
   Call it with just the three ranges and every structural disqualification
   check silently no-ops; the tightest range wins by default. That's not a
   theoretical risk: it's exactly what a naive Keystone integration would do
   without this wrapper. scripts/run_valuation.py already computes all five
   correctly (verified by reading it directly) — this module generalizes that
   exact recipe into a reusable function and refuses to proceed without it,
   rather than trusting every future caller to remember it by hand.

2. SHARED-INPUT AWARENESS. triangulate.Divergence carries no machine-readable
   record of which subject-side inputs the two compared methods actually
   share (MethodRange.provenance is free text). shared_input_lineage.py
   supplies that separately, verified against dcf.py/comps.py/precedent.py's
   real field usage. This module attaches an overlap note to EVERY divergence
   pair — not only ones currently reading "Converge" — because how much two
   methods depend on the same raw numbers is useful context regardless of
   whether they currently agree or disagree; a corrupted shared input can
   just as easily manufacture a false disagreement's true cause being hidden
   as manufacture false agreement.

Neither of these is a fork of triangulate.py. triangulate.Conclusion is used
unmodified (it's a frozen dataclass from an external, tested package) — this
module wraps it, it doesn't reach inside it or copy its logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from valuationlab.triangulate import (
    Conclusion,
    Method,
    MethodRange,
    format_conclusion as _format_conclusion,
    triangulate as _triangulate,
)

from keystone.lineage import overlap

# A pair sharing at least half their subject-side input fields is treated as
# weak-evidence-if-they-agree. This threshold is a stated judgement call, not
# a derived one -- same discipline triangulate.py itself uses for its own
# hardcoded thresholds (75% TV share, 2.0x spread, etc.): a number the report
# can show, not one dressed up as empirically calibrated.
HIGH_OVERLAP_THRESHOLD = 0.5

_METHOD_KEY = {Method.DCF: "dcf", Method.TRADING_COMPS: "comps", Method.PRECEDENT: "precedent"}


class MissingMeasuredInputError(ValueError):
    """Refuses to compute triangulate()'s measured parameters without real
    DCF/comps/precedent results to derive them from. The gap this closes:
    triangulate() itself will happily run without them (optional params,
    silent no-check mode) -- this wrapper is the only thing stopping a
    Keystone caller from doing the same thing by accident."""


@dataclass(frozen=True)
class SharedInputNote:
    method_a: str
    method_b: str
    shared_fields: frozenset
    overlap_fraction: float
    reading: str          # the Divergence's own reading text, carried through for convenience
    weak_evidence: bool   # True when overlap is high AND the reading currently reads as agreement


@dataclass(frozen=True)
class AnnotatedConclusion:
    """Wraps triangulate.Conclusion (unmodified) with the shared-input layer."""
    conclusion: Conclusion
    shared_input_notes: tuple[SharedInputNote, ...]

    def format(self) -> str:
        base = _format_conclusion(self.conclusion)
        lines = [base, "", "Shared-input analysis (every pair, not just agreeing ones):"]
        for n in self.shared_input_notes:
            fields = ", ".join(sorted(n.shared_fields)) if n.shared_fields else "none"
            flag = "  <-- WEAK EVIDENCE IF READ AS AGREEMENT" if n.weak_evidence else ""
            lines.append(
                f"  {n.method_a} vs {n.method_b}: share {n.overlap_fraction:.0%} of "
                f"subject-side inputs ({fields}){flag}")
        return "\n".join(lines)


def convergence_diagnostic(
    ranges: list[MethodRange],
    *,
    dcf_result,
    comps_result,
    precedent_prices: list[float],
    comps_basis: str = "ev_ebitda",
    market_price: float | None = None,
    anchor: Method | None = None,
    reasoning: str = "",
) -> AnnotatedConclusion:
    """The only sanctioned way for Keystone to call triangulate() — computes
    all five measured parameters from real method results (never left
    optional), then attaches the shared-input overlap analysis to every
    divergence pair, agreeing or not."""
    if dcf_result is None or comps_result is None or precedent_prices is None:
        raise MissingMeasuredInputError(
            "convergence_diagnostic requires real dcf_result, comps_result, and "
            "precedent_prices to compute triangulate()'s measured parameters — "
            "it will not fall back to calling triangulate() unparameterized. "
            f"Got: dcf_result={dcf_result!r}, comps_result={comps_result!r}, "
            f"precedent_prices={precedent_prices!r}")
    if not precedent_prices:
        raise MissingMeasuredInputError(
            f"precedent_prices must be non-empty to compute precedent_tier_spread — got "
            f"{precedent_prices!r}")
    if min(precedent_prices) < 0:
        # A NEGATIVE price is an adapter contract violation, not missing data: equity can't
        # be worth less than $0 (limited liability), so adapters floor at 0 and disclose the
        # unfloored value. $0 itself IS allowed -- the first version rejected any price
        # <= 0, which treated a genuine "equity is worthless at this deal multiple" result
        # (CYH, found live) as if the input were missing.
        raise MissingMeasuredInputError(
            f"precedent_prices contains a negative price {precedent_prices!r} -- floor at 0 "
            f"upstream (limited liability) and disclose the unfloored value in the caveat.")
    if comps_result.low <= 0:
        raise MissingMeasuredInputError(
            f"comps_result.low must be positive to compute comps_multiple_spread "
            f"— got {comps_result.low!r}")

    terminal_value_share = dcf_result.pv_terminal_value / dcf_result.enterprise_value
    equity_weight_in_wacc = dcf_result.wacc.weight_equity
    # Spread is max/min over deals. With $0 prices: if some deals say equity has value and
    # others say it's worthless, that's maximal disagreement (inf -> disqualified); if every
    # deal says $0, they agree (1.0) -- and a zero-width, zero-mid range is disqualified by
    # triangulate's own width check (width_pct is inf when mid is 0).
    lo, hi = min(precedent_prices), max(precedent_prices)
    precedent_tier_spread = (hi / lo) if lo > 0 else (float("inf") if hi > 0 else 1.0)
    comps_multiple_spread = comps_result.high / comps_result.low
    comps_peer_count = len(comps_result.included)

    conclusion = _triangulate(
        ranges, anchor=anchor, reasoning=reasoning, market_price=market_price,
        terminal_value_share=terminal_value_share,
        equity_weight_in_wacc=equity_weight_in_wacc,
        precedent_tier_spread=precedent_tier_spread,
        comps_multiple_spread=comps_multiple_spread,
        comps_peer_count=comps_peer_count,
        # Sixth mandatory measured parameter, added after CYH: a one-deal precedent set
        # passed every other check and became the anchor. Always passed, never optional.
        precedent_deal_count=len(precedent_prices),
    )

    notes = []
    for d in conclusion.divergences:
        a, b = _METHOD_KEY[d.method_a], _METHOD_KEY[d.method_b]
        shared, frac = overlap(a, b, comps_basis)
        reads_as_agreement = d.reading.lower().startswith(("converg", "partial", "inconclusive"))
        weak = frac >= HIGH_OVERLAP_THRESHOLD and reads_as_agreement
        notes.append(SharedInputNote(a, b, shared, frac, d.reading, weak))

    return AnnotatedConclusion(conclusion=conclusion, shared_input_notes=tuple(notes))
