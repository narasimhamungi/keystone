"""run_domain_screen_experiment.py — v2, fixed against the first live run's output.

What the v1 run found and why it needed fixing (not a live-data fluke — a real
bug in this script, confirmed by reading CreditRiskLab's own CLI):

    v1 built the training panel with `as_of_snapshot(long_frame, as_of_today())`
    for every issuer, training rows included. That's correct for a CANDIDATE
    (screen a company as of now) and wrong for TRAINING (most of the 10
    defaulted issuers stopped filing years ago post-bankruptcy — JCP, FTR, REV,
    PRTY, BBBY, RAD, TOYS all came back "no visible snapshot as of today", and
    the training panel silently shrank to 8/15 issuers, 7/10 of them missing
    entirely from the defaulted side). Confirmed by reading cli.py's own
    `cmd_run`: the real pipeline builds training rows via
    `build_panel(issuers, fundamentals)`, which iterates each issuer's actual
    historical observation window (2015-2024, from universe.yaml), not "today".
    Fixed below by calling that function directly instead of re-deriving a
    worse version of it. Verified offline (no network) against CreditRiskLab's
    own synthetic demo data before shipping this back: build_panel's output
    feeds compute_training_ranges cleanly, 108 synthetic rows, all 12 features
    populated on every row.

    v1 also crashed ("FAILED: ... missing required feature 'wc_ta'") on JPM/
    BAC/WFC instead of reporting a clean decline. That's not a bug to hide —
    `wc_ta` needs current_assets/current_liabilities, concepts that don't map
    cleanly onto a bank's balance sheet the way they do a non-financial's. It's
    live evidence for exactly the exclusion Trellis's SIC gate already encodes.
    Fixed to report it as a decline, not a stack trace.

    v3 (this version): now reports a real PD via credit_scoring.credit_read(),
    not just the domain verdict. Everything before this used the gate alone —
    whether a PD would be shown at all — with nothing behind it producing an
    actual number. score_new_issuer() didn't exist anywhere in CreditRiskLab;
    it's new code, built and offline-verified against CreditRiskLab's own
    synthetic demo data before being wired in here.

Still needs SEC EDGAR access — run where Trellis/CreditRiskLab already reach
it (your local environment). The sandbox that wrote this can't run it: 403 on
data.sec.gov through that environment's egress proxy.

Setup: pip install -e /path/to/CreditRiskLab   (or Trellis-backed via the
existing trellis_adapter, if Trellis is also installed alongside it)
"""
from __future__ import annotations

from creditrisklab.features.panel import build_panel
from creditrisklab.features.point_in_time import as_of_snapshot
from creditrisklab.features.ratios import compute_ratios
from creditrisklab.ingest.edgar_client import as_of_today, company_fundamentals, resolve_cik_by_ticker
from creditrisklab.ingest.trellis_adapter import fetch_fundamentals
from creditrisklab.universe import load_issuers

from keystone.domain_screen import compute_training_ranges
from keystone.credit import credit_read

# Candidate panel — edit before running.
CANDIDATES: list[str] = [
    "PFE",   # Pfizer — domain-check control (Trellis-validated, absent from training universe)
    "CYH",   # Community Health Systems — in-domain distress read, the genuine middle-of-spectrum case
    # "????" # pre-profit growth company — pick one, confirm current status first
    "JPM", "BAC", "WFC",  # bank candidates — expect all three declined (SIC-gate-equivalent evidence)
]


def _build_training_panel():
    """Real recipe, matching cli.py's cmd_ingest + cmd_run exactly: fetch each
    training issuer's fundamentals (trellis_adapter tries Trellis, falls back
    to direct EDGAR — same defensive pattern CreditRiskLab uses for itself),
    group by CIK, build the point-in-time panel. Returns the panel itself now,
    not just the ranges derived from it — credit_scoring.score_new_issuer()
    needs to fit the real logistic model on it, not just compute bounds."""
    issuers = load_issuers()
    fundamentals = {}
    for issuer in issuers:
        try:
            frame = fetch_fundamentals(issuer.cik, ticker=issuer.ticker)
            fundamentals[issuer.cik] = frame
            print(f"  {issuer.ticker:6s} {len(frame):5d} facts")
        except Exception as exc:  # noqa: BLE001 — Phase-0 script, surface and continue
            print(f"  {issuer.ticker:6s} FAILED to ingest: {exc}")

    panel = build_panel(issuers, fundamentals)
    n_issuers = panel["ticker"].nunique() if len(panel) else 0
    print(f"  Panel: {len(panel)} issuer-observations across {n_issuers}/{len(issuers)} issuers.\n")
    if n_issuers < len(issuers):
        missing = sorted(set(i.ticker for i in issuers) - set(panel["ticker"].unique()))
        print(f"  Issuers with zero usable observations: {missing}")
        print("  (Real question, not swept under the rug: does the observation window in "
              "universe.yaml actually reach each issuer's pre-default period? Worth checking "
              "before trusting the range this builds.)\n")
    return panel


def _screen_candidate(ticker: str, ranges, panel) -> tuple[str, object | None]:
    """Returns (summary line, CreditRead or None — None only on CIK/snapshot
    failure, not on a clean decline, which now returns a real CreditRead with
    scored=None). The read is returned so main() can show per-feature detail
    for the cases under interrogation (PFE, CYH)."""
    cik = resolve_cik_by_ticker(ticker)
    if cik is None:
        return f"{ticker:<8}could not resolve CIK — skipped", None

    long_frame = company_fundamentals(cik, ticker=ticker)
    snap = as_of_snapshot(long_frame, as_of_today())
    if snap is None:
        return f"{ticker:<8}no visible snapshot as of today (stale or no recent filings)", None

    ratios = compute_ratios(snap)
    read = credit_read(ratios, ranges, panel)

    if read.domain_verdict is None:
        z_str = (f"{read.altman_z_double_prime:.2f}" if read.altman_z_double_prime is not None else "n/a")
        line = (f"{ticker:<8}DECLINED — {read.incomplete_reason} "
                f"(consistent with a financial-sector balance sheet; "
                f"Altman Z\u2033={z_str} ({read.altman_zone}))")
    elif read.scored is None:
        z_str = (f"{read.altman_z_double_prime:.2f}" if read.altman_z_double_prime is not None else "n/a")
        line = (f"{ticker:<8}OUT OF DOMAIN — "
                f"driving feature(s): {', '.join(read.domain_verdict.driving_features)}; "
                f"Altman Z\u2033={z_str} ({read.altman_zone})")
    else:
        line = (f"{ticker:<8}{'True':<14}"
                f"{', '.join(read.domain_verdict.driving_features) or '-':<28}"
                f"PD={read.scored.pd:>7.2%}  "
                f"Z\u2033={read.altman_z_double_prime:.2f} ({read.altman_zone})")
    return line, read


def _print_position_detail(ticker: str, read) -> None:
    """Where inside each feature's range the subject actually sits — 0% is at
    the training minimum, 100% at the training maximum. LOW-CONF marks a
    feature whose training range is bounded by CreditRiskLab's winsorization
    cap on at least one side, not a real company extreme (found on an earlier
    live run: debt_ebitda/interest_cover came back [0.28, 50.00] and
    [-50.00, 50.00] — 50/-50 is the cap, not an observed issuer)."""
    verdict = read.domain_verdict
    if verdict is None:
        print(f"\n  {ticker}: cannot show position detail — {read.incomplete_reason}")
        return
    print(f"\n  {ticker} — position within each feature's training range:")
    for v in verdict.feature_verdicts:
        span = v.train_max - v.train_min
        pct = ((v.value - v.train_min) / span * 100) if span else float("nan")
        flags = []
        if not v.in_range:
            flags.append("OUT OF RANGE")
        if v.low_confidence:
            flags.append("LOW-CONF (cap-bounded range)")
        flag_str = f"  <-- {', '.join(flags)}" if flags else ""
        print(f"    {v.feature:<16} value={v.value:>10.3f}  "
              f"range=[{v.train_min:>9.3f}, {v.train_max:>9.3f}]  "
              f"position={pct:>6.1f}%{flag_str}")
    if verdict.low_confidence_features:
        print(f"  ({ticker}: treat the in-domain read on "
              f"{', '.join(verdict.low_confidence_features)} as weak evidence — "
              f"see credit_domain_screen.py's cap-degeneracy note.)")
    if read.scored is not None:
        print(f"  PD sensitivity (population default rate assumption):")
        for tau, pd in sorted(read.scored.pd_sensitivity.items()):
            print(f"    tau={tau:.0%}: PD={pd:.2%}")
        print(f"  {read.scored.calibration_note}")


DETAIL_TICKERS = {"PFE", "CYH"}  # the two cases actually under interrogation this round


def main() -> None:
    print("Building training panel from the real 15-issuer universe...")
    panel = _build_training_panel()
    ranges = compute_training_ranges(panel.to_dict("records"))

    print(f"{'Ticker':<8}{'In domain?':<14}{'Driving feature(s)':<28}{'Credit read'}")
    detail_pending = []
    for ticker in CANDIDATES:
        try:
            line, read = _screen_candidate(ticker, ranges, panel)
            print(line)
            if read is not None and ticker in DETAIL_TICKERS:
                detail_pending.append((ticker, read))
        except Exception as exc:  # noqa: BLE001
            print(f"{ticker:<8}FAILED: {exc}")

    for ticker, read in detail_pending:
        _print_position_detail(ticker, read)


if __name__ == "__main__":
    main()
