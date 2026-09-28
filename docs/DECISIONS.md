# Decisions and findings

Why Keystone is built the way it is, and the upstream bugs that building it exposed. Each
decision states what was chosen, what was rejected, and why. Most were earned on live data,
not designed up front.

## Scope

**US SEC filers, non-financial companies only.** Banks and insurers are declined: debt is
their raw material rather than leverage, interest is revenue rather than expense, and the
ratio set CreditRiskLab is trained on does not apply. Trellis already refuses SIC 6000-6999;
live runs on JPM, BAC and WFC confirmed the credit screen cannot even compute (no working
capital), so the decline is structural, not a policy preference.

**Out of the automated core:** sum-of-the-parts, market-implied residual, catalysts, deal
analysis (DealLab) and investment ratings (EquityResearch). Each needs hand-sourced,
per-company research. EquityResearch was in scope until reading its report script showed
every gate input is a hand-written thesis (its own J&J report calls the solvency gate with
an empty placeholder).

## Architecture

**Two independent ingestion spines, kept separate.** CreditRiskLab reads EDGAR through its
own resolution logic; the valuation reads it through Trellis. Unifying them would simplify
the code and destroy the one property that makes the tie-out and the verdict meaningful:
agreement between independently resolved numbers.

**One code path for every subject.** A company is data (`subjects.py`: peers, CAPM inputs,
sourced deals, dated market prices), never code. J&J's frozen ValuationLab snapshot was
retired to a test fixture: it predated the Trellis fixes and carried a stale cost of debt.

**Replay caches raw Trellis observations, not finished tables.** A replay re-runs Trellis's
own resolution, so a change in Trellis between run and replay is reported, not hidden.

**Pinned upstream tags.** Trellis, ValuationLab and CreditRiskLab at v0.2.x, Bridgework at
v1.0.0. An upstream change reaches a verdict only through a deliberate re-pin. The first
clean install from the tags found a real bug no editable install could (below).

**Report rendered only from the manifest.** One Markdown file per run: byte-identical on
re-render, readable on GitHub, diffable across runs.

**Market data: cited, dated snapshots**, recorded per run. A live feed would make runs
irreproducible and raises licensing questions free sources don't settle.

## The verdict

**Retired: "market price below the lowest valuation".** Under limited liability, any heavily
indebted company has some method floored at $0, after which that test can never fire. It
returned UNDERCUTS for CYH by construction, not evidence.

**Adopted: credit versus the equity cushion.** Credit is elevated when the PD exceeds 2x the
population default rate; the market is stressed when equity is under 25% of enterprise value
(an ordinary recession-sized fall in enterprise value would erase it). Both thresholds are
stated judgements, like triangulate's 75% / 2.0x / 5-peer rules. Every verdict reports
whether it survives cushion thresholds of 15-35% and PD multiples of 1.5-3x; a verdict that
flips inside that band is labelled fragile, not presented as a finding.

## Valuation rules

- **Measured disqualification is mandatory.** `triangulate()` accepts its measured
  parameters as optional and silently skips those checks without them; Keystone always
  passes all six.
- **Precedent needs at least two deals.** One deal gives a zero-width range and a 1.0x
  spread, and was chosen as the anchor, the most trusted method (found on CYH).
- **WACC grid centred on the computed WACC, +/-1pp**, for every subject. A fixed 8-12% range
  for CYH was retracted: it rested on an unverified distress-yield claim, and the one bond
  quote found (10.875% secured notes due 2032 at a 6.90% yield) contradicts it.
- **Equity floored at $0, unfloored value disclosed.** CYH's one precedent deal implied
  -$11.23/share; the floor applies limited liability, the caveat keeps the finding.
- **Peers without an operating-income line** (hospital income statements) get
  EBIT = pretax income + interest, named in the comps caveat because it includes
  non-operating items the subject's own figure does not.
- **CYH comps kept at 3 peers (below the 5-peer minimum, so ruled out).** The only other
  listed pure acute-care operator would make 4; padding with behavioral or rehab operators
  to reach 5 would game the threshold. **CYH precedent kept at one deal (ruled out)**;
  CYH's own divestiture prices are the best source for a future pass.

## Credit rules

- **Domain screen: per-feature training range, not Mahalanobis.** With 105 issuer-years and
  12 features the covariance matrix is near-singular; a range screen is the weaker claim
  the sample can support.
- **Cap-bounded features are flagged low-confidence.** `debt_ebitda` and `interest_cover`
  training ranges were the regression's winsorization cap, not real companies.
- **Calibration: prior correction only** for a new company. The pipeline's cross-fitted
  recalibration has no reusable calibrator for an unseen issuer, and at 10 defaults it would
  fall back to Platt scaling anyway.

## Upstream bugs found by building Keystone

| Repo | Bug | Found on | Fixed in |
|---|---|---|---|
| CreditRiskLab | EBIT derived as pretax + interest when OperatingIncomeLoss was untagged: 31% too high for J&J (litigation charges) | J&J tie-out | v0.2.0 |
| CreditRiskLab | Missing R&D blocked the gross-profit EBIT path | RAD, NKE tie-out | v0.2.0 |
| CreditRiskLab | No revenue - cost of revenue fallback for gross profit | NKE tie-out | v0.2.0 |
| CreditRiskLab | Long-term-debt tag priority disagreed with Trellis (3.3% gap) | TOYS tie-out | v0.2.0 |
| CreditRiskLab | Model config located by a source-checkout path; failed when pip-installed | Clean install | v0.2.1 |
| Trellis | No 2024 `InterestExpenseNonoperating` tag: cost of debt silently 0.0 | CYH | v0.2.0 |
| Trellis | Signed net-interest tag inside the gross-interest chain | CYH | v0.2.0 |
| Trellis | Tax rate a plain mean over loss and near-breakeven years (one at 329%) | CYH | v0.2.0 |
| Trellis | No reported pretax income for companies without an operating-income line | HCA | v0.2.0 |
| ValuationLab | DCF required `inventory`; services companies hold none | CYH | v0.2.0 |
| ValuationLab | No minimum deal count; a single deal could anchor a valuation | CYH | v0.2.0 |

## Known, accepted differences

**Revlon, interest expense:** CreditRiskLab accepts `InterestExpenseDebtExcludingAmortization`,
a narrower measure Trellis deliberately does not (under alias merging, filing recency would
then choose between two scopes). Nothing Keystone values depends on it; 14 of 15 training
companies otherwise verify.
