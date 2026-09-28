"""run_universe_tieout.py — the proactive check: does the same kind of silent
derived-field gap that hit J&J's operating_income exist anywhere else across
CreditRiskLab's 15-issuer training panel, for a DIFFERENT field, before it
distorts a different company's PD?

Not a variant of keystone_run.py's tie-out: that one reuses ValuationLab's
J&J-scoped snapshot. Most of the 15 training issuers have no ValuationLab
snapshot at all (peer groups/CAPM/deals are J&J-specific, per the automation-
boundary decision). This script calls Trellis directly for each of the 15,
independent of ValuationLab.

PERIOD ALIGNMENT: CreditRiskLab is asked about the SAME fiscal year Trellis
resolved, at Trellis's own known `filed` date for that year -- not a generic
days-after-year-end guess. That guess was the actual bug behind Party City's
first period-mismatch result: its FY2022 10-K wasn't filed until 2024-03-28
(bankruptcy-related, ~14 months late), so a fixed 150-day buffer asked
CreditRiskLab about a period that, at that point in time, genuinely didn't
exist anywhere yet. Several training issuers are also delisted and stopped
filing years before their last observed period -- as_of_today() would
correctly return None for those (see credit_domain_screen's earlier finding
on JCP/FTR/REV/PRTY/BBBY/RAD/TOYS), which is why this asks about each
issuer's own last known period instead. cross_spine_tieout's own period check
is still the final safety net if even the exact filed date guess is wrong.

Needs SEC EDGAR access for BOTH ingestion paths, and TWO user-agent env vars
(Trellis and CreditRiskLab use different ones) -- see the commands.
"""
from __future__ import annotations

from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv(override=True)

from creditrisklab.features.point_in_time import as_of_snapshot
from creditrisklab.ingest.trellis_adapter import fetch_fundamentals
from creditrisklab.universe import load_issuers
from trellis.ingest import fetch_all
from trellis.statements import build_annual_table, fill_derived_gaps

from keystone.tieout import Status, tie_out

FILING_LAG_DAYS = 7  # small buffer past the actual known filed date -- CreditRiskLab's
# own filter is `filed <= as_of` (inclusive), so this isn't guessing when a filing
# might land, just padding past a date we already know exactly.


def trellis_base_row(cik: int) -> tuple[dict | None, int | None, date | None]:
    """Returns (row, fiscal_year, latest_filed). latest_filed is the max
    `filed` date across any FY observation whose period_end falls in that
    year -- the actual date Trellis's own data says the period became
    knowable, not a generic days-after-year-end guess. Fixes a real bug
    found on live data: Party City's FY2022 10-K wasn't filed until
    2024-03-28 (a bankruptcy-related ~14-month delay), so a fixed 150-day
    buffer asked CreditRiskLab about a period that, at that point in time,
    genuinely didn't exist anywhere yet -- both spines were right; the
    heuristic asking the question was wrong."""
    observations = fetch_all(cik)
    result = build_annual_table(observations)
    if not result.table:
        return None, None, None
    fill_derived_gaps(result.table)  # mutates in place; same derivation chain
    fy = max(result.table)

    filed_dates = [
        date.fromisoformat(obs.filed)
        for key, obs_list in observations.items()
        if key != "_missing"  # fetch_all stashes list[str] (missing field names) under
                              # this key, not list[Observation] -- confirmed the crash
                              # this caused live: 'str' object has no attribute 'fiscal_period'
        for obs in obs_list
        if obs.fiscal_period == "FY" and date.fromisoformat(obs.period_end).year == fy
    ]
    latest_filed = max(filed_dates) if filed_dates else None
    return result.table[fy], fy, latest_filed


def main() -> None:
    issuers = load_issuers()
    print(f"{'Ticker':<8}{'Trellis FY':<12}{'CRL period':<14}{'Aligned':<10}{'Verified':<10}Mismatches")
    findings = []

    for issuer in issuers:
        try:
            row, fy, latest_filed = trellis_base_row(int(issuer.cik))
            if row is None:
                print(f"{issuer.ticker:<8}no Trellis data at all")
                continue

            as_of = ((latest_filed + timedelta(days=FILING_LAG_DAYS)) if latest_filed is not None
                     else date(fy, 12, 31) + timedelta(days=FILING_LAG_DAYS))
            frame = fetch_fundamentals(issuer.cik, ticker=issuer.ticker)
            snap = as_of_snapshot(frame, as_of)
            if snap is None:
                print(f"{issuer.ticker:<8}FY{fy:<10}no CreditRiskLab snapshot near that date")
                continue

            result = tie_out(row, fy, snap)
            mismatches = ",".join(r.field.trellis_name for r in result.rows
                                  if r.status is Status.MISMATCH)
            print(f"{issuer.ticker:<8}FY{fy:<10}{str(snap['period_end']):<14}"
                  f"{str(result.period_aligned):<10}{str(result.valuation_inputs_verified):<10}"
                  f"{mismatches or '-'}")
            if not result.valuation_inputs_verified:
                findings.append((issuer.ticker, result))
        except Exception as exc:  # noqa: BLE001 -- one issuer's failure shouldn't kill the batch
            print(f"{issuer.ticker:<8}FAILED: {exc}")

    if findings:
        print(f"\n{len(findings)} issuer(s) with unverified valuation inputs -- full detail:\n")
        for ticker, result in findings:
            print(f"--- {ticker} ---")
            print(result.explain())
            print()
    else:
        print("\nNo other unverified valuation inputs found across the panel.")


if __name__ == "__main__":
    main()
