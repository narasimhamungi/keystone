"""cross_spine_tieout.py — ties Trellis's base-year numbers to CreditRiskLab's
independently-ingested numbers for the same company and fiscal period.

WHY THIS INSTEAD OF A "FACE STATEMENT" TIE-OUT
------------------------------------------------
The architecture doc specified tying Trellis's numbers to "the filed
statement's headline numbers." In XBRL the tagged fact IS the headline
number, so a same-spine re-read proves little. What already exists is better:
CreditRiskLab ingests from EDGAR through its OWN resolution logic
(ingest/schema.py + features/point_in_time.py), fully independent of Trellis
(confirmed: CreditRiskLab's validation run is 100% direct-EDGAR, zero Trellis
rows). Comparing the two is a genuine cross-check of the exact failure the
planted-corruption experiment demonstrated: one mis-resolved subject field
(cash_and_equivalents, long_term_debt) moves DCF, comps, and precedent by the
identical amount, and nothing inside ValuationLab can see it.

WHAT IT CATCHES, AND WHAT IT CANNOT
--------------------------------------
Every field below has an IDENTICAL first-priority XBRL tag in both spines
(verified by reading trellis/schema.py and creditrisklab/ingest/schema.py),
so a disagreement means the two resolved the same tag differently: wrong
period, different restatement vintage, a fallback tag used on one side,
or duplicate-fact handling. That's the resolution layer, where silent
mapping bugs live (Trellis's own history includes a fiscal-year bug of
exactly this kind). It CANNOT catch a filer tagging the wrong number itself:
both spines would read the same wrong fact and agree. Agreement means
"resolved consistently," not "true."
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping

# Relative tolerance. Identical tags on the identical period and vintage should
# match exactly (XBRL facts are exact integers), so any gap beyond rounding is
# a resolution difference. A stated judgement call, not a derived number.
DEFAULT_REL_TOL = 0.001


@dataclass(frozen=True)
class TieOutField:
    trellis_name: str
    crl_name: str
    first_tag: str          # identical first-priority tag in both chains (verified)
    valuation_input: bool   # feeds DCF/comps/precedent (shared_input_lineage union)
    note: str = ""


FIELDS: tuple[TieOutField, ...] = (
    TieOutField("revenue", "revenue",
                "RevenueFromContractWithCustomerExcludingAssessedTax", True),
    TieOutField("operating_income", "ebit", "OperatingIncomeLoss", True,
                "CreditRiskLab derives EBIT from pre-tax income + interest when "
                "OperatingIncomeLoss is untagged; Trellis does not."),
    TieOutField("depreciation_amortization", "depreciation_amortisation",
                "DepreciationDepletionAndAmortization", True),
    TieOutField("long_term_debt", "long_term_debt", "LongTermDebtNoncurrent", True,
                "Fallback order differs: Trellis falls back to LongTermDebt "
                "(includes current maturities), CreditRiskLab to "
                "LongTermDebtAndCapitalLeaseObligations."),
    TieOutField("cash_and_equivalents", "cash",
                "CashAndCashEquivalentsAtCarryingValue", True,
                "CreditRiskLab alone falls back to a restricted-cash-inclusive tag."),
    TieOutField("interest_expense", "interest_expense", "InterestExpense", True,
                "Feeds the DCF's cost of debt via Trellis's derived interest_rate. Added after "
                "a live finding: Trellis lacked the 2024 InterestExpenseNonoperating relabel, "
                "so CYH's cost of debt fell to 0.0 and J&J's frozen FY2025 snapshot has no "
                "interest_expense at all. CreditRiskLab's chain also accepts broader tags "
                "(InterestAndDebtExpense) Trellis deliberately doesn't -- a fallback-tag flag "
                "here may be a scope difference, not an error."),
    TieOutField("net_interest", "net_interest", "InterestIncomeExpenseNonoperatingNet", True,
                "Signed (negative = net expense). Trellis's cost-of-debt fallback when gross "
                "interest is untagged (Community Health Systems reports only this). Identical "
                "chain order in both spines."),
    TieOutField("total_assets", "total_assets", "Assets", False),
    TieOutField("total_liabilities", "total_liabilities", "Liabilities", False,
                "CreditRiskLab derives assets minus equity when Liabilities is untagged."),
    TieOutField("stockholders_equity", "equity", "StockholdersEquity", False),
    TieOutField("retained_earnings", "retained_earnings",
                "RetainedEarningsAccumulatedDeficit", False),
    TieOutField("assets_current", "current_assets", "AssetsCurrent", False),
    TieOutField("liabilities_current", "current_liabilities", "LiabilitiesCurrent", False),
    TieOutField("net_income", "net_income", "NetIncomeLoss", False),
    TieOutField("cfo", "cfo", "NetCashProvidedByUsedInOperatingActivities", False),
)


class Status(Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    MISSING_BOTH = "missing_both"       # neither spine has it -- nothing to reconcile
    MISSING_TRELLIS = "missing_trellis"  # asymmetric: CreditRiskLab has it, Trellis doesn't
    MISSING_CRL = "missing_creditrisklab"  # asymmetric: Trellis has it, CreditRiskLab doesn't


@dataclass(frozen=True)
class TieOutRow:
    field: TieOutField
    trellis_value: float | None
    crl_value: float | None
    status: Status
    rel_diff: float | None
    crl_tag_used: str | None     # from CreditRiskLab's own _tags record
    crl_derived: bool            # CreditRiskLab computed this rather than read it
    crl_used_fallback: bool      # CreditRiskLab resolved to a tag other than first_tag


@dataclass(frozen=True)
class TieOutResult:
    period_aligned: bool
    trellis_fiscal_year: int
    crl_period_end: date | None
    rows: tuple[TieOutRow, ...]

    @property
    def valuation_input_mismatches(self) -> tuple[TieOutRow, ...]:
        """Rows needing attention: a real disagreement (MISMATCH) or an
        asymmetric gap (only one spine has the field) -- not MISSING_BOTH,
        which is agreement that the data isn't there, not a dispute."""
        return tuple(r for r in self.rows
                     if r.field.valuation_input and r.status not in (Status.MATCH, Status.MISSING_BOTH))

    @property
    def valuation_inputs_verified(self) -> bool:
        """True only if the period aligned AND every valuation input matched.
        Missing on either side counts as unverified, not as a pass."""
        return self.period_aligned and not self.valuation_input_mismatches

    def explain(self) -> str:
        if not self.period_aligned:
            return (f"PERIOD MISMATCH: Trellis base year FY{self.trellis_fiscal_year} vs "
                    f"CreditRiskLab period ending {self.crl_period_end}. Not compared: "
                    f"a field-by-field comparison across different periods would report "
                    f"differences that are timing, not mapping errors.")
        lines = [f"Cross-spine tie-out, FY{self.trellis_fiscal_year} "
                 f"(CreditRiskLab period ending {self.crl_period_end}):"]
        for r in self.rows:
            tag = "VAL" if r.field.valuation_input else "   "
            diff = f"{r.rel_diff:+.3%}" if r.rel_diff is not None else "   n/a"
            flags = []
            if r.crl_derived:
                flags.append("CRL-derived")
            if r.crl_used_fallback:
                flags.append(f"CRL fallback tag {r.crl_tag_used}")
            flag = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {tag} {r.field.trellis_name:<26}{r.status.value:<22}{diff}{flag}")
        verdict = ("valuation inputs VERIFIED across both spines"
                   if self.valuation_inputs_verified else
                   f"valuation inputs NOT verified: "
                   f"{', '.join(r.field.trellis_name for r in self.valuation_input_mismatches)}")
        lines.append(f"  -> {verdict}")
        return "\n".join(lines)


def _period_end_as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def tie_out(
    trellis_base_row: Mapping[str, float | None],
    trellis_fiscal_year: int,
    crl_snapshot: Mapping[str, object],
    *,
    rel_tol: float = DEFAULT_REL_TOL,
) -> TieOutResult:
    """trellis_base_row: the subject's base-year row from Trellis (e.g. a
    ValuationLab snapshot's table[base_year]). crl_snapshot: the dict returned
    by creditrisklab.features.point_in_time.as_of_snapshot().

    Period alignment is checked by calendar year of CreditRiskLab's
    period_end against Trellis's fiscal-year label. Deliberately strict: a
    retailer whose fiscal year ends in late January would be flagged as a
    mismatch rather than silently matched under a guessed convention. The
    loud direction, on purpose."""
    period_end = _period_end_as_date(crl_snapshot.get("period_end"))
    aligned = period_end is not None and period_end.year == trellis_fiscal_year
    if not aligned:
        return TieOutResult(False, trellis_fiscal_year, period_end, ())

    tags_used = crl_snapshot.get("_tags") or {}
    derived = set(crl_snapshot.get("derived_fields") or [])
    rows = []
    for f in FIELDS:
        tv = trellis_base_row.get(f.trellis_name)
        cv = crl_snapshot.get(f.crl_name)
        tv = None if tv is None else float(tv)
        cv = None if cv is None else float(cv)
        crl_tag = tags_used.get(f.crl_name)
        if tv is None and cv is None:
            status, rel = Status.MISSING_BOTH, None
        elif tv is None:
            status, rel = Status.MISSING_TRELLIS, None
        elif cv is None:
            status, rel = Status.MISSING_CRL, None
        else:
            denom = abs(tv) if tv != 0 else 1.0
            rel = (cv - tv) / denom
            status = Status.MATCH if abs(rel) <= rel_tol else Status.MISMATCH
        rows.append(TieOutRow(
            field=f, trellis_value=tv, crl_value=cv, status=status, rel_diff=rel,
            crl_tag_used=crl_tag, crl_derived=f.crl_name in derived,
            crl_used_fallback=crl_tag is not None and crl_tag != f.first_tag,
        ))
    return TieOutResult(True, trellis_fiscal_year, period_end, tuple(rows))
