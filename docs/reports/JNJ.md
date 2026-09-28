# Keystone — Johnson & Johnson (JNJ)

Run as of 2026-09-28 · manifest v2 · parameters `fba85a8bff7f`

## Verdict

**SUPPORTS** — credit (PD 0.86% = 0.4x the 2.0% base rate) and market pricing (equity is 97.0% of enterprise value; stressed below 25%) agree -- neither signals stress. Robust: unchanged for cushion thresholds 15-35% and PD multiples 1.5-3x. Market price is 110% of the median method value.

| Signal | Reading | Stressed |
|---|---|---|
| Credit | PD 0.86% (0.4x base rate; elevated above 2.0x) | no |
| Market | equity is 97.0% of enterprise value (stressed below 25%) | no |

## Credit read (CreditRiskLab)

- Period ending 2025-12-28 (out-of-sample for this fiscal year).
- Domain screen: in domain.
- Low-confidence features (cap-bounded training range): debt_ebitda,interest_cover.
- PD 0.86% at a 2% population default rate (sensitivity — base rate 1%: 0.43%; base rate 2%: 0.86%; base rate 3%: 1.30%).
- prior_correct only -- cross-fitted isotonic/Platt recalibration skipped for new-issuer scoring (see module docstring); at this panel's positive count that step would fall back to Platt regardless.
- Altman Z'' 4.41 (safe).

## Valuation

Market price $267.20 · base FY2025 · net debt $19.73B · cost of debt 1.91% · tax rate 14.3% · computed WACC 6.88%

| Method | Low | Mid | High | Status |
|---|---|---|---|---|
| DCF | $170.15 | $243.46 | $368.26 | ruled out: terminal value dominates: the conclusion restates the WACC and terminal growth assumptions rather than reading the forecast cash flows (terminal value 82% of EV (threshold 75%)) |
| Trading comps | $95.29 | $139.99 | $301.90 | ruled out: peer set is not comparable enough: a median across peers that disagree by more than 2x averages companies in different situations (3.0x spread across peers (threshold 2.0x)) |
| Precedent transactions | $181.09 | $326.28 | $471.47 | ruled out: sourced deals disagree too widely to form a range -- the answer is a function of which deal is picked, and a control premium is embedded that a trading valuation should not carry (2.6x spread between deals (threshold 2.0x)) |

Anchor: none — no method passed every check. Market price is 110% of the median method value.

- **DCF** — WACC 5.88%-7.88% (computed 6.88% +/-1pp) x g 1.5%-3.0%. Terminal value is 82% of EV. Debt is 6% of capital in WACC. 0 of 20 grid cells give equity <= 0 (floored at $0).
- **Trading comps** — EV/EBITDA 7.5x-22.6x across 4 peers. Pharma peers only; J&J's MedTech segment is not reflected in this peer set.
- **Precedent transactions** — EV/Revenue from 2 sourced deal(s). 
- **Shared subject inputs between methods** — dcf vs comps 50%, dcf vs precedent 40%, precedent vs comps 80%.
- **Derived (not reported) base-year fields** — operating_income = gross_profit - sga_expense - rnd_expense

## Data tie-out (Trellis vs CreditRiskLab)

| Field | Feeds valuation | Status | Difference |
|---|---|---|---|
| revenue | yes | match | +0.000% |
| operating_income | yes | match | +0.000% |
| depreciation_amortization | yes | match | +0.000% |
| long_term_debt | yes | match | +0.000% |
| cash_and_equivalents | yes | match | +0.000% |
| interest_expense | yes | match | +0.000% |
| net_interest | yes | missing_both | n/a |
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
| ABBV | $263.04 | 1,767,117,285 | 2026-09-15 | ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with Keystone as the J&J fixture |
| BMY | $63.73 | 2,042,714,656 | 2026-09-15 | ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with Keystone as the J&J fixture |
| JNJ | $267.20 | 2,409,898,597 | 2026-09-15 | ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with Keystone as the J&J fixture |
| MRK | $143.79 | 2,467,171,638 | 2026-09-15 | ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with Keystone as the J&J fixture |
| PFE | $27.55 | 5,699,673,589 | 2026-09-15 | ValuationLab data/snapshots/market.json (as_of 2026-09-15), bundled with Keystone as the J&J fixture |

Replay offline: `python -m keystone replay <this run's manifest.json>` — every number above must reproduce exactly from the cached EDGAR data (15 CreditRiskLab frames, 5 Trellis observation sets).
