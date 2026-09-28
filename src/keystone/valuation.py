"""valuation.py — DCF, trading comps and precedent transactions for ANY configured subject.

Generalized from the CYH adapter, which was built to prove the pipeline reaches past J&J.
Every rule here was earned on live data and is documented where it lives:
  * base year = the most recent COMPLETE, non-stub year (ValuationLab's own snapshot recipe),
    with `inventory` optional -- services companies never tag it;
  * the DCF uses the subject's OWN derived interest and tax rates (never silent defaults),
    and stops loudly if Trellis derives a 0.0 interest rate on a levered balance sheet;
  * the WACC grid is centred on the computed WACC (+/-1pp) for every subject;
  * equity prices are floored at $0 (limited liability), with unfloored values disclosed;
  * peers need only the five fields comps reads, and a peer with no operating-income line
    gets EBIT = pretax income + interest, named in the comps caveat.
Data comes only through a fetcher (keystone.fetch), so the same code runs live and replays.
"""
from __future__ import annotations

from trellis.companies import get_profile
from trellis.forecast import derive_drivers_from_history, run_forecast
from trellis.statements import fill_derived_gaps
from valuationlab.comps import build_peer_multiple, implied_value, summarize
from valuationlab.dcf import MarketData, build_ufcf_series, run_dcf, sensitivity_grid
from valuationlab.precedent import apply_multiple
from valuationlab.triangulate import Method, MethodRange

from keystone.subjects import SubjectConfig

REQUIRED = ("revenue", "operating_income", "income_tax_expense", "capex",
            "depreciation_amortization", "accounts_receivable",
            "accounts_payable", "long_term_debt", "cash_and_equivalents")
PEER_REQUIRED = ("revenue", "depreciation_amortization", "long_term_debt", "cash_and_equivalents")


def norm_cik(cik) -> str:
    return str(int(cik)).zfill(10)


def _select_year(table: dict, stub_years: set, ok) -> int | None:
    newest = max(table)
    for year in sorted(table, reverse=True):
        if newest - year > 5:
            break
        if year not in stub_years and ok(table[year]):
            return year
    return None


def base_and_forecast(ticker: str, fetcher) -> tuple[dict, dict, int, object]:
    cik = fetcher.cik(ticker)
    table, stub_years = fetcher.trellis_table(cik)
    if not table:
        raise ValueError(f"{ticker}: Trellis returned no annual data at all.")
    derived = fill_derived_gaps(table)
    base_year = _select_year(table, stub_years, lambda r: all(f in r for f in REQUIRED))
    if base_year is None:
        newest = max(table)
        raise RuntimeError(f"{ticker}: no recent complete year. Missing in FY{newest}: "
                           f"{[f for f in REQUIRED if f not in table[newest]]}")
    drivers = derive_drivers_from_history(table, base_year, lookback_years=5,
                                          overrides=get_profile(int(cik)).overrides,
                                          exclude_years=stub_years)
    if drivers.interest_rate == 0.0 and table[base_year].get("long_term_debt", 0.0) > 0:
        _raise_zero_interest(ticker, cik, base_year, table[base_year]["long_term_debt"])
    forecast = run_forecast(table, base_year, drivers, years=5)
    base = dict(table[base_year])
    base["_interest_rate"] = drivers.interest_rate
    base["_tax_rate"] = drivers.tax_rate
    base["_derived"] = [f"{d.canonical_name} = {d.method}" for d in (derived or []) if d.year == base_year]
    return base, forecast, base_year, drivers


def _raise_zero_interest(ticker, cik, base_year, ltd) -> None:
    """A zero cost of debt on a levered balance sheet is impossible (CYH, found live).
    Name every interest tag the filer uses so the right one can be added to Trellis."""
    from trellis.ingest import fetch_companyfacts
    gaap = ((fetch_companyfacts(int(cik)) or {}).get("facts", {}).get("us-gaap", {}))
    recent = str(base_year - 5)
    tags = sorted(t for t, b in gaap.items() if "Interest" in t and any(
        str(e.get("form", "")).upper() in {"10-K", "10-K/A"} and str(e.get("end", ""))[:4] >= recent
        for es in b.get("units", {}).values() for e in es))
    raise RuntimeError(f"{ticker}: Trellis derived interest_rate = 0.0 but FY{base_year} long-term "
                       f"debt is {ltd:,.0f}. Interest-related tags in its 10-Ks since {recent}: {tags}")


def ebit_from_pretax(row: dict) -> float | None:
    """EBIT = pretax income + interest for a peer with no operating-income line (hospital
    income statements have no gross-profit/SG&A structure). Gross interest first, else a
    NEGATIVE net_interest at its absolute value, else None -- CreditRiskLab's validated
    rule. EBIT by definition, NOT operating income (it carries non-operating items)."""
    pretax = row.get("pretax_income")
    if pretax is None:
        return None
    if row.get("interest_expense") is not None:
        return float(pretax) + abs(float(row["interest_expense"]))
    net = row.get("net_interest")
    if net is not None and float(net) < 0:
        return float(pretax) - float(net)
    return None


def peer_base(ticker: str, fetcher) -> tuple[dict, int, str | None]:
    table, stub_years = fetcher.trellis_table(fetcher.cik(ticker))
    if not table:
        raise ValueError(f"{ticker}: Trellis returned no annual data at all.")
    fill_derived_gaps(table)
    newest = max(table)
    for year in sorted(table, reverse=True):
        if newest - year > 5:
            break
        if year in stub_years or not all(f in table[year] for f in PEER_REQUIRED):
            continue
        row = dict(table[year])
        if "operating_income" in row:
            return row, year, None
        ebit = ebit_from_pretax(row)
        if ebit is not None:
            row["operating_income"] = ebit
            return row, year, f"{ticker} FY{year}: EBIT = pretax income + interest (no operating-income line)"
    raise RuntimeError(f"{ticker}: no recent year usable for comps; FY{newest} missing "
                       f"{[f for f in PEER_REQUIRED if f not in table[newest]]}")


def floor_note(label: str, raw: dict, extra: str = "") -> str:
    """Limited liability floors equity at $0; the UNFLOORED values stay in the caveat,
    because 'worthless at this method' is a finding (CYH's one deal implied -$11.23)."""
    neg = {k: v for k, v in raw.items() if v <= 0}
    if not neg:
        return ""
    shown = ", ".join(f"{k} {v:,.2f}" for k, v in neg.items())
    return (f"EQUITY <= 0 before flooring ({label}: {shown}/share{extra}) -- floored at $0 "
            f"under limited liability; this method implies the equity is worthless there. ")


def build_methods(subject: SubjectConfig, fetcher) -> dict:
    base, forecast, base_year, drivers = base_and_forecast(subject.ticker, fetcher)
    mkt = subject.market[subject.ticker]
    dcf_market = MarketData(share_price=mkt.share_price, shares_outstanding=mkt.shares_outstanding,
                            beta=subject.capm.beta, risk_free_rate=subject.capm.risk_free_rate,
                            equity_risk_premium=subject.capm.equity_risk_premium, as_of=mkt.as_of)
    ir, tr = base["_interest_rate"], base["_tax_rate"]
    net_debt = base.get("long_term_debt", 0.0) - base.get("cash_and_equivalents", 0.0)

    dcf = run_dcf(base, forecast, dcf_market, ir, tr, subject.terminal_growth)
    w0 = dcf.wacc.wacc
    wacc_range = [w0 + d for d in subject.wacc_grid_offsets]
    grid = sensitivity_grid(base, forecast, dcf_market, ir, tr, wacc_range, list(subject.growth_range))
    cells = [v for v in grid.values() if v == v]  # NaN only where WACC <= g
    if not cells or max(cells) <= 0:
        ufcf = build_ufcf_series(base, forecast)
        g = subject.terminal_growth
        ev = sum(u.ufcf / (1 + w0) ** i for i, u in enumerate(ufcf, 1)) + \
            ufcf[-1].ufcf * (1 + g) / (w0 - g) / (1 + w0) ** len(ufcf)
        raise ValueError(f"{subject.ticker}: DCF equity <= 0 at every grid cell (EV {ev:,.0f} at "
                         f"WACC {w0:.2%} vs net debt {net_debt:,.0f}; UFCF "
                         f"{[round(u.ufcf) for u in ufcf]}).")
    n_nonpos = sum(1 for v in cells if v <= 0)
    dcf_range = MethodRange(
        method=Method.DCF, low=max(min(cells), 0.0), mid=max(dcf.implied_share_price, 0.0), high=max(cells),
        basis=f"WACC {min(wacc_range):.2%}-{max(wacc_range):.2%} (computed {w0:.2%} +/-1pp) x g "
              f"{min(subject.growth_range):.1%}-{max(subject.growth_range):.1%}",
        caveat=(f"Terminal value is {dcf.pv_terminal_value / dcf.enterprise_value:.0%} of EV. Debt is "
                f"{dcf.wacc.weight_debt:.0%} of capital in WACC. {n_nonpos} of {len(cells)} grid cells "
                f"give equity <= 0 (floored at $0)."),
        provenance=f"Trellis forecast off FY{base_year}")

    peer_multiples, peer_notes = [], []
    for t in subject.peers:
        row, fy, note = peer_base(t, fetcher)
        if note:
            peer_notes.append(note)
        peer_multiples.append(build_peer_multiple(t, t, fy, row, subject.market[t]))
    comps = summarize(peer_multiples, basis=subject.comps_basis)
    ci = implied_value(base, comps, mkt.shares_outstanding)
    raw_c = {k: ci[f"implied_price_{k}"] for k in ("low", "median", "high")}
    pn = ("; ".join(peer_notes) + ". Their EBITDA includes non-operating items; the subject's "
          "uses its operating income -- definitions differ. ") if peer_notes else ""
    comps_range = MethodRange(
        method=Method.TRADING_COMPS, low=max(raw_c["low"], 0.0), mid=max(raw_c["median"], 0.0),
        high=max(raw_c["high"], 0.0),
        basis=f"EV/EBITDA {comps.low:.1f}x-{comps.high:.1f}x across {len(comps.included)} peers",
        caveat=floor_note("comps", raw_c) + pn + subject.comps_caveat,
        provenance="Trellis annual tables; market data per subject config")

    subject_ebitda = base["operating_income"] + base["depreciation_amortization"]
    raw_prec = [(apply_multiple(d, base["revenue"], subject_ebitda)["implied_ev_from_revenue_multiple"]
                 - net_debt) / mkt.shares_outstanding for d in subject.precedent_deals]
    prec = [max(v, 0.0) for v in raw_prec]
    n = len(prec)
    prec_range = MethodRange(
        method=Method.PRECEDENT, low=min(prec), mid=sum(prec) / n, high=max(prec),
        basis=f"EV/Revenue from {n} sourced deal(s)",
        caveat=(floor_note("precedent", {f"deal {i + 1}": v for i, v in enumerate(raw_prec)},
                           f" (EV at the deal multiple vs net debt {net_debt:,.0f})")
                + subject.precedent_caveat),
        provenance="Sourced deals per subject config")

    return dict(dcf=dcf, dcf_range=dcf_range, comps=comps, comps_range=comps_range,
                precedent_prices=prec, precedent_range=prec_range, market_price=mkt.share_price,
                shares_outstanding=mkt.shares_outstanding, net_debt=net_debt, base=base,
                base_year=base_year, drivers=drivers)
