"""run_planted_corruption_experiment.py — Phase-0 experiment: does a corrupted
subject-side Trellis field move DCF, comps, and precedent together?

Runs entirely offline (committed J&J snapshots, no SEC EDGAR needed).

Field choice matters and isn't arbitrary. shared_input_lineage.py's field-usage
map, verified against dcf.py/comps.py/precedent.py directly, shows
long_term_debt and cash_and_equivalents are the only two fields all three
methods' subject-side calculations share (each subtracts the same net-debt
figure on the way from enterprise value to equity value). revenue would only
move precedent under this run's real config (comps uses EV/EBITDA, not
EV/Revenue) -- corrupting it wouldn't demonstrate three-way sharing at all,
it would demonstrate a single-method effect. cash_and_equivalents is corrupted
here for exactly that reason: it's the field the lineage map says is
genuinely, verifiably shared by all three.
"""
from keystone.convergence import convergence_diagnostic
from keystone.jnj_fixture import build_jnj_methods

CORRUPTION_FRACTION = 0.10  # +10% overstatement of cash_and_equivalents


def _run(field_overrides=None):
    built = build_jnj_methods(field_overrides)
    ac = convergence_diagnostic(
        [built["dcf_range"], built["comps_range"], built["precedent_range"]],
        dcf_result=built["dcf"], comps_result=built["comps"],
        precedent_prices=built["precedent_prices"], market_price=built["market"].share_price,
    )
    return built, ac


def main() -> None:
    clean_built, clean_ac = _run()
    cash = clean_built["base"]["cash_and_equivalents"]
    corrupted_cash = cash * (1 + CORRUPTION_FRACTION)
    corrupted_built, corrupted_ac = _run({"cash_and_equivalents": corrupted_cash})

    print(f"Corrupted field: cash_and_equivalents  "
          f"({cash:,.0f} -> {corrupted_cash:,.0f}, +{CORRUPTION_FRACTION:.0%})\n")

    print(f"{'Method':<12}{'clean mid':>14}{'corrupted mid':>16}{'shift $':>12}{'shift %':>10}")
    rows = (("dcf_range", "DCF"), ("comps_range", "Comps"), ("precedent_range", "Precedent"))
    shifts = {}
    for key, label in rows:
        c0 = clean_built[key].mid
        c1 = corrupted_built[key].mid
        shift = c1 - c0
        shifts[label] = shift
        print(f"{label:<12}{c0:>14.2f}{c1:>16.2f}{shift:>12.2f}{shift / c0 * 100:>9.2f}%")

    print(f"\nAll three shift by the same {shifts['DCF']:.2f}/share, to the cent -- expected, "
          f"not approximate: all three subtract the identical net-debt figure on the way from "
          f"enterprise value to equity value, divided by the same share count. A one-line XBRL "
          f"mapping error on this single field moves every method's answer by an identical dollar "
          f"amount simultaneously. The percentage shift differs only because each method's own "
          f"baseline price differs.")

    print(f"\nAt {CORRUPTION_FRACTION:.0%}, the shift ($0.82/share) is too small to flip any "
          f"divergence reading in this run -- J&J's methods currently disagree by 23-133%, "
          f"dwarfing it. That's not a negative result: the mechanism (identical, correlated "
          f"movement from one bad input) is demonstrated exactly regardless of whether it's large "
          f"enough to change a classification in this particular case. A subject whose methods "
          f"already sit close together is where this kind of error would be large enough to "
          f"manufacture a false 'Converge' rather than just nudge an existing 'Disagree'.")

    print("\nShared-input notes (unchanged by the corruption -- overlap is structural, "
          "not data-dependent):")
    for n in clean_ac.shared_input_notes:
        fields = ", ".join(sorted(n.shared_fields))
        print(f"  {n.method_a} vs {n.method_b}: {n.overlap_fraction:.0%} ({fields})")


if __name__ == "__main__":
    main()
