"""run_missing_parameters_guard.py — Phase-0 experiment: does calling
triangulate() without its measured parameters actually degrade the checks it
runs, or was that a theoretical worry?

Runs entirely offline (committed J&J snapshots, no SEC EDGAR needed).

What this proves, precisely (verified against real output, not assumed):
bare triangulate() with no measured parameters does NOT silently skip
disqualification — it still disqualifies all three methods in this run. But
it disqualifies them for a DIFFERENT reason than the real, parameterized run:
purely on range width ("spans 76% of its midpoint"), never touching the
terminal-value-share, equity-weight, precedent-tier-spread, or comps-spread
checks at all -- those need the parameters this call omitted, and they
silently no-op without them. In THIS run the width check happens to catch
the same three methods anyway, which could make the gap look harmless if you
only check the disqualification COUNT. It won't always coincide: a different
subject could have tight-enough ranges to clear the width bar while still
failing (say) the comps-spread bar those methods never got asked about.
"""
from keystone.convergence import MissingMeasuredInputError, convergence_diagnostic
from valuationlab.triangulate import triangulate as bare_triangulate
from keystone.jnj_fixture import build_jnj_methods


def main() -> None:
    built = build_jnj_methods()
    ranges = [built["dcf_range"], built["comps_range"], built["precedent_range"]]

    print("=" * 78)
    print("1. Bare triangulate() -- no measured parameters supplied")
    print("=" * 78)
    bare = bare_triangulate(ranges)
    print(f"Recommendation: {bare.recommendation.method}")
    print(f"Disqualified: {len(bare.recommendation.disqualified)}")
    for d in bare.recommendation.disqualified:
        print(f"  - {d.method.value}: {d.reason}")
        print(f"      measured: {d.measured}")
    print("\n^ Every one of these is a WIDTH check (MethodRange.width_pct alone --")
    print("  computed from the ranges themselves, never needs the 5 optional params).")
    print("  The terminal-value-share, equity-weight, precedent-tier-spread, and")
    print("  comps-multiple-spread checks did not run at all -- not 'ran and passed',")
    print("  did not run. Nothing in this output tells you that without knowing to look.")

    print()
    print("=" * 78)
    print("2. Same three ranges through convergence_diagnostic() -- the real run")
    print("=" * 78)
    ac = convergence_diagnostic(
        ranges, dcf_result=built["dcf"], comps_result=built["comps"],
        precedent_prices=built["precedent_prices"], market_price=built["market"].share_price,
    )
    for d in ac.conclusion.recommendation.disqualified:
        print(f"  - {d.method.value}: {d.reason}")
        print(f"      measured: {d.measured}")
    print("\n^ Different reasons entirely -- terminal value share, precedent tier")
    print("  spread, comps multiple spread. The width checks above never got the")
    print("  chance to be the only thing standing between a bad method and an anchor.")

    print()
    print("=" * 78)
    print("3. convergence_diagnostic() refuses outright if you try to skip the inputs")
    print("=" * 78)
    try:
        convergence_diagnostic(ranges, dcf_result=None, comps_result=built["comps"],
                                precedent_prices=built["precedent_prices"])
        print("FAIL: expected MissingMeasuredInputError, none raised")
    except MissingMeasuredInputError as exc:
        print(f"Correctly refused: {exc}")


if __name__ == "__main__":
    main()
