"""test_cross_spine_tieout.py — Trellis side is REAL J&J data (ValuationLab's
committed snapshot). CreditRiskLab side is a hand-built dict in the exact
shape as_of_snapshot() returns (verified by reading point_in_time.py), NOT a
live CreditRiskLab ingestion: that needs EDGAR, which this sandbox can't
reach. run_tieout_experiment.py does the live version."""
from datetime import date

import pytest

from keystone.tieout import FIELDS, Status, tie_out
from keystone.jnj_fixture import load_snapshot


@pytest.fixture(scope="module")
def jnj_trellis():
    snap = load_snapshot("JNJ")
    return dict(snap["table"][snap["base_year"]]), snap["base_year"]


def _crl_snapshot_from(trellis_row, period_end=date(2025, 12, 28), **overrides):
    """Build a CreditRiskLab-shaped snapshot whose values agree with Trellis,
    with each field tagged by its shared first-priority tag."""
    snap = {"period_end": period_end, "_tags": {}, "derived_fields": []}
    for f in FIELDS:
        snap[f.crl_name] = trellis_row.get(f.trellis_name)
        snap["_tags"][f.crl_name] = f.first_tag
    snap.update(overrides)
    return snap


def test_agreeing_spines_verify(jnj_trellis):
    row, fy = jnj_trellis
    result = tie_out(row, fy, _crl_snapshot_from(row))
    assert result.period_aligned
    # MISSING_BOTH is agreement too (both spines lack the field) -- this test predates
    # that status. J&J's frozen snapshot has no interest_expense, so that row is
    # MISSING_BOTH here, not MATCH.
    assert all(r.status in (Status.MATCH, Status.MISSING_BOTH) for r in result.rows)
    assert result.valuation_inputs_verified


def test_catches_the_planted_cash_corruption(jnj_trellis):
    """The Phase-0 corruption (+10% cash_and_equivalents on the Trellis side)
    moved DCF, comps and precedent by an identical $0.82 and nothing inside
    ValuationLab could see it. The independent spine sees it immediately."""
    row, fy = jnj_trellis
    clean_crl = _crl_snapshot_from(row)
    corrupted_trellis = dict(row, cash_and_equivalents=row["cash_and_equivalents"] * 1.10)
    result = tie_out(corrupted_trellis, fy, clean_crl)
    assert not result.valuation_inputs_verified
    names = [r.field.trellis_name for r in result.valuation_input_mismatches]
    assert names == ["cash_and_equivalents"]
    cash_row = next(r for r in result.rows if r.field.trellis_name == "cash_and_equivalents")
    assert cash_row.rel_diff == pytest.approx(1 / 1.10 - 1, rel=1e-9)


def test_non_valuation_mismatch_does_not_block_valuation_verification(jnj_trellis):
    row, fy = jnj_trellis
    crl = _crl_snapshot_from(row, retained_earnings=row["retained_earnings"] * 1.5)
    result = tie_out(row, fy, crl)
    assert result.valuation_inputs_verified  # retained_earnings is not a valuation input
    re_row = next(r for r in result.rows if r.field.trellis_name == "retained_earnings")
    assert re_row.status is Status.MISMATCH


def test_period_mismatch_refuses_to_compare(jnj_trellis):
    row, fy = jnj_trellis
    result = tie_out(row, fy, _crl_snapshot_from(row, period_end=date(2024, 12, 29)))
    assert not result.period_aligned
    assert result.rows == ()
    assert not result.valuation_inputs_verified
    assert "PERIOD MISMATCH" in result.explain()


def test_missing_on_one_side_is_unverified_not_a_pass(jnj_trellis):
    row, fy = jnj_trellis
    result = tie_out(row, fy, _crl_snapshot_from(row, long_term_debt=None))
    ltd = next(r for r in result.rows if r.field.trellis_name == "long_term_debt")
    assert ltd.status is Status.MISSING_CRL
    assert not result.valuation_inputs_verified


def test_missing_on_both_sides_is_not_treated_as_a_disagreement(jnj_trellis):
    """Regression test for a real bug: the status assignment used to check
    only whether Trellis's value was None, so MISSING_TRELLIS fired even when
    CreditRiskLab was ALSO None -- 'both spines agree the data isn't there'
    was indistinguishable from 'CreditRiskLab found something Trellis
    missed'. Found by re-examining a live scan before building a fix on top
    of an unconfirmed asymmetry."""
    row = dict(jnj_trellis[0])
    row["long_term_debt"] = None
    result = tie_out(row, jnj_trellis[1], _crl_snapshot_from(row, long_term_debt=None))
    ltd = next(r for r in result.rows if r.field.trellis_name == "long_term_debt")
    assert ltd.status is Status.MISSING_BOTH
    assert ltd not in result.valuation_input_mismatches
    # long_term_debt was the only field forced to None here -- everything
    # else in the fixture still agrees, so the run should read verified.
    assert result.valuation_inputs_verified


def test_missing_both_does_not_mask_a_real_mismatch_elsewhere(jnj_trellis):
    row = dict(jnj_trellis[0])
    row["long_term_debt"] = None
    crl = _crl_snapshot_from(row, long_term_debt=None,
                             cash=row["cash_and_equivalents"] * 1.5)  # crl_name is "cash", not "cash_and_equivalents"
    result = tie_out(row, jnj_trellis[1], crl)
    assert not result.valuation_inputs_verified
    names = {r.field.trellis_name for r in result.valuation_input_mismatches}
    assert names == {"cash_and_equivalents"}  # long_term_debt correctly excluded


def test_fallback_tag_and_derivation_are_flagged(jnj_trellis):
    row, fy = jnj_trellis
    crl = _crl_snapshot_from(row)
    crl["_tags"]["long_term_debt"] = "LongTermDebtAndCapitalLeaseObligations"
    crl["derived_fields"] = ["total_liabilities"]
    result = tie_out(row, fy, crl)
    by_name = {r.field.trellis_name: r for r in result.rows}
    assert by_name["long_term_debt"].crl_used_fallback
    assert by_name["total_liabilities"].crl_derived
    assert not by_name["revenue"].crl_used_fallback


def test_period_end_accepts_iso_string(jnj_trellis):
    row, fy = jnj_trellis
    result = tie_out(row, fy, _crl_snapshot_from(row, period_end="2025-12-28"))
    assert result.period_aligned


def test_every_valuation_input_from_lineage_is_tied_out():
    """If shared_input_lineage ever adds a field a valuation method reads,
    the tie-out must cover it too, or a mis-resolved input goes unchecked."""
    from keystone.lineage import (COMPS_SUBJECT_FIELDS_BY_BASIS, DCF_SUBJECT_FIELDS,
                                      PRECEDENT_SUBJECT_FIELDS)
    lineage = set(DCF_SUBJECT_FIELDS) | set(PRECEDENT_SUBJECT_FIELDS)
    for fields in COMPS_SUBJECT_FIELDS_BY_BASIS.values():
        lineage |= set(fields)
    covered = {f.trellis_name for f in FIELDS if f.valuation_input}
    assert lineage <= covered
