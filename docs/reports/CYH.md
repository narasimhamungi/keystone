# Keystone — Community Health Systems (CYH)

Run as of 2026-09-28 · manifest v2 · parameters `c107a4467be4`

## Verdict

**SUPPORTS** — credit (PD 7.26% = 3.6x the 2.0% base rate) and market pricing (equity is 3.7% of enterprise value; stressed below 25%) agree -- both signal stress. Robust: unchanged for cushion thresholds 15-35% and PD multiples 1.5-3x. Market price is 11% of the median method value.

| Signal | Reading | Stressed |
|---|---|---|
| Credit | PD 7.26% (3.6x base rate; elevated above 2.0x) | yes |
| Market | equity is 3.7% of enterprise value (stressed below 25%) | yes |

## Credit read (CreditRiskLab)

- Period ending 2025-12-31 (out-of-sample for this fiscal year).
- Domain screen: in domain.
- Low-confidence features (cap-bounded training range): debt_ebitda,interest_cover.
- PD 7.26% at a 2% population default rate (sensitivity — base rate 1%: 3.73%; base rate 2%: 7.26%; base rate 3%: 10.60%).
- prior_correct only -- cross-fitted isotonic/Platt recalibration skipped for new-issuer scoring (see module docstring); at this panel's positive count that step would fall back to Platt regardless.
- Altman Z'' 0.28 (distress).

## Valuation

Market price $2.79 · base FY2025 · net debt $10.12B · cost of debt 7.57% · tax rate 36.3% · computed WACC 5.02%

| Method | Low | Mid | High | Status |
|---|---|---|---|---|
| DCF | $31.82 | $94.34 | $252.19 | ruled out: terminal value dominates: the conclusion restates the WACC and terminal growth assumptions rather than reading the forecast cash flows (terminal value 86% of EV (threshold 75%)) |
| Trading comps | $0.00 | $24.84 | $41.21 | ruled out: peer set too small for the median to be stable (3 peers (threshold 5)) |
| Precedent transactions | $0.00 | $0.00 | $0.00 | ruled out: a single transaction is an anecdote, not a range: zero width and a 1.0x spread by construction, which reads as precision it does not have (1 deal (threshold 2)) |

Anchor: none — no method passed every check. Market price is 11% of the median method value.

- **DCF** — WACC 4.02%-6.02% (computed 5.02% +/-1pp) x g 1.0%-2.5%. Terminal value is 86% of EV. Debt is 96% of capital in WACC. 0 of 20 grid cells give equity <= 0 (floored at $0).
- **Trading comps** — EV/EBITDA 5.2x-8.3x across 3 peers. EQUITY <= 0 before flooring (comps: low -1.75/share) -- floored at $0 under limited liability; this method implies the equity is worthless there. HCA FY2025: EBIT = pretax income + interest (no operating-income line). Their EBITDA includes non-operating items; the subject's uses its operating income -- definitions differ. Peers are 25-200x CYH's market cap. Kept at 3 (below the 5-peer minimum, so comps is ruled out) by decision: the only other listed pure acute-care operator, Ardent (IPO 2024), would make 4 -- still short -- and padding with behavioral or rehab operators to reach 5 would game the threshold.
- **Precedent transactions** — EV/Revenue from 1 sourced deal(s). EQUITY <= 0 before flooring (precedent: deal 1 -11.23/share (EV at the deal multiple vs net debt 10,120,000,000)) -- floored at $0 under limited liability; this method implies the equity is worthless there. Kept at one deal by decision: hospital-system M&A rarely discloses target financials (HCA's 10-K gives bolt-on prices with no target revenue). CYH's own divestiture prices are the best source for a future pass.
- **Shared subject inputs between methods** — dcf vs comps 50%, dcf vs precedent 40%, precedent vs comps 80%.
- **Trellis assumptions** — tax_rate: 2 lookback year(s) excluded as non-representative (rate 3.29 outside [0, 1]; loss/breakeven year); median of the remaining years used | Cannot compute a dividend growth rate (need 2+ consecutive years with a positive starting value) -- assumed 0.0 -- falling back to payout_ratio policy for this driver | interest_rate derived from net_interest (interest expense net of interest income) for at least one lookback year -- gross interest expense untagged; slightly understates the gross cost of debt | inventory_days: no data available in any lookback year -- assumed 0.0. This is very likely wrong, not a neutral default -- check whether the underlying tag needs a fallback added (see scripts/diagnose_tags.py) before trusting any forecast built on this driver.
- **Derived (not reported) base-year fields** — gross_profit = revenue - cost_of_revenue | sga_expense = gross_profit - operating_income

## Data tie-out (Trellis vs CreditRiskLab)

| Field | Feeds valuation | Status | Difference |
|---|---|---|---|
| revenue | yes | match | +0.000% |
| operating_income | yes | match | +0.000% |
| depreciation_amortization | yes | match | +0.000% |
| long_term_debt | yes | match | +0.000% |
| cash_and_equivalents | yes | match | +0.000% |
| interest_expense | yes | missing_both | n/a |
| net_interest | yes | match | +0.000% |
| total_assets | no | match | +0.000% |
| total_liabilities | no | match | +0.000% |
| stockholders_equity | no | match | +0.000% |
| retained_earnings | no | match | +0.000% |
| assets_current | no | match | +0.000% |
| liabilities_current | no | match | +0.000% |
| net_income | no | match | +0.000% |
| cfo | no | match | +0.000% |

Valuation inputs **verified** across both spines.

## Reproducibility

| Package | Version | Source | Commit |
|---|---|---|---|
| bridgework | 1.0.0 | vcs | 8f0ce30e1d1a |
| creditrisklab | 0.2.1 | vcs | 82bcf1411a7f |
| keystone | 0.1.0 | editable | 6bd0ce06019c |
| trellis | 0.2.0 | vcs | 824e492a84d2 |
| valuationlab | 0.2.0 | vcs | cb29cc62e6ad |

| Ticker | Price | Shares | As of | Source |
|---|---|---|---|---|
| CYH | $2.79 | 141,010,000 | 2026-07-28 | hand-sourced (Morningstar; corroborated within cents by StockTitan/eToro/Motley Fool, Jul-Aug 2026) |
| HCA | $402.59 | 221,839,800 | 2026-07-31 | hand-sourced (10jqka, equibles) |
| THC | $259.45 | 80,555,020 | 2026-08-12 | hand-sourced (Kalkine); shares DERIVED from disclosed market cap / price, not directly stated |
| UHS | $155.77 | 61,700,000 | 2026-07-24 | hand-sourced (ChartRow) |

Replay offline: `python -m keystone replay <this run's manifest.json>` — every number above must reproduce exactly from the cached EDGAR data (16 CreditRiskLab frames, 4 Trellis observation sets).
