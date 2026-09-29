# Keystone

**Does the equity market's pricing agree with a company's credit risk — and can every number behind that answer be traced and reproduced?**

Keystone runs one company through four independently built tools — [Trellis](https://github.com/narasimhamungi/trellis) (SEC XBRL ingestion and forecast), [ValuationLab](https://github.com/narasimhamungi/valuationlab) (DCF, trading comps, precedent transactions), [CreditRiskLab](https://github.com/narasimhamungi/CreditRiskLab) (default probability) and [Bridgework](https://github.com/narasimhamungi/bridgework) (Shapley attribution) — and cross-examines their answers instead of just collecting them.

## What one run produces

- **A credit read** from CreditRiskLab's own EDGAR ingestion: a domain screen (does this company resemble the training population at all?), a PD, and Altman Z''.
- **A valuation** from three methods, each with its range, caveats, and a measured reason if it is ruled out. No method is averaged into another; if none passes every check, Keystone says so rather than picking one.
- **A cross-spine data tie-out.** Trellis and CreditRiskLab ingest EDGAR independently. Keystone checks that they agree, field by field, on every input the valuation uses. Across CreditRiskLab's 15-company training panel this check found and fixed five real ingestion bugs; 14 of 15 companies now verify, and the 15th (Revlon) is a documented scope difference in an interest measure Trellis deliberately doesn't accept.
- **A verdict:** do credit and market pricing agree? Credit is *elevated* when the PD exceeds 2x the population default rate. The market is *stressed* when equity is under 25% of enterprise value — a cushion an ordinary recession-sized fall in enterprise value would erase. Every verdict reports whether it survives nearby thresholds (cushion 15–35%, PD multiple 1.5–3x).
- **A Markdown report and a manifest.** The manifest records package versions and commits, every parameter and dated market price, and sha256 hashes of cached EDGAR data. The report is rendered only from the manifest, and every run can be replayed offline, exactly.

## Validated on two very different companies

Live runs of the packaged pipeline, installed clean from the pinned upstream tags:

| | Johnson & Johnson | Community Health Systems |
|---|---|---|
| Shape | Mega-cap, healthy, multi-segment | Small-cap, distressed, single-segment hospital operator |
| Credit | PD 0.86% (0.4x base rate) | PD 7.26% (3.6x base rate), Altman Z'' 0.28 |
| Equity cushion | 97.0% of enterprise value | 3.7% of enterprise value |
| Verdict | **Supports** — neither signals stress | **Supports** — both signal stress |
| Robust to nearby thresholds | Yes | Yes |
| Offline replay | Reproduced exactly | Reproduced exactly |

Sample reports: [`docs/reports/JNJ.md`](docs/reports/JNJ.md), [`docs/reports/CYH.md`](docs/reports/CYH.md).

Taking CYH end to end surfaced about a dozen real bugs no J&J run could have, each fixed at its source in the upstream repos: a missing 2024 XBRL interest tag that silently zeroed the cost of debt, a tax rate averaged over loss years (one at 329%), a single precedent deal accepted as a valuation anchor, and more. The first clean install from the pinned tags found one more: CreditRiskLab located its model configuration by a source-checkout path that doesn't exist in an installed package — fixed in v0.2.1 by shipping the configuration inside the package.

## Usage

```bash
pip install -e .                                   # pulls the four upstream repos at pinned tags
python -m keystone run CYH                         # live run -> keystone_out/CYH/{manifest.json, cache/, report.md}
python -m keystone replay keystone_out/CYH/manifest.json      # offline; must reproduce exactly
python -m keystone report keystone_out/CYH/manifest.json      # re-render the report
python -m keystone compare old/manifest.json new/manifest.json # Bridgework: why did the DCF price move?
```

A live run needs `CREDITRISKLAB_SEC_UA` and `TRELLIS_USER_AGENT` set (e.g. in `.env`) to a descriptive user agent with a contact address, as SEC requires. Tests run fully offline: `pip install -e .[dev] && pytest`.

## Scope and limits

- **US SEC filers, non-financial companies only.** Banks and insurers are declined: debt is raw material for them, not leverage, and the ratio set CreditRiskLab is trained on does not apply.
- **Adding a company means curating its inputs** — peers, CAPM inputs, sourced precedent deals and dated market data — in `keystone/subjects.py`. That is analyst judgement, not something Keystone generates.
- **The credit model is small** (15 issuers, 10 defaults). Its PD is reported with a domain screen and sensitivity range, not as a validated forecast.
- **The 2.0x spread gate is weak at small peer counts.** Max/min spread widens with sample size by construction; at four peers with pharma's dispersion, ValuationLab measured pure sampling exceeding 2.0x about 68% of the time. Both sample subjects sit under the 5-peer minimum (J&J 4, CYH 3), so comps are ruled out either way — but J&J's report prints the spread reason (3.0x), which should be read as "too few peers to judge", not as proof the peers are non-comparable. The same statistic gates precedents (J&J: 2.6x across deals).
- **Market data is cited, dated snapshots**, recorded in every manifest, not a live feed: results must be traceable to the prices used and reproducible.
- **Out of the automated core by design:** sum-of-the-parts, market-implied residuals, catalysts, deal analysis and investment ratings. Each needs hand-sourced, per-company research.

Every design decision, and each upstream bug building Keystone exposed, is logged in [`docs/DECISIONS.md`](docs/DECISIONS.md).
