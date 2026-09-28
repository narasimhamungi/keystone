"""run.py — one end-to-end Keystone run for any configured subject, plus exact replay.

Supersedes keystone_run.py (J&J) and keystone_run_cyh.py (CYH): both subjects now go
through the same code. Pipeline: credit read (CreditRiskLab, own EDGAR spine) -> valuation
(DCF/comps/precedent on Trellis) -> convergence diagnostic -> cross-spine tie-out ->
coherence verdict -> DCF drivers for Bridgework attribution. compute() is deterministic
given its inputs (no network, no clock); live and replay both call it, so a replay
exercises exactly the code a live run did.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from creditrisklab.features.panel import build_panel
from creditrisklab.features.point_in_time import as_of_snapshot
from creditrisklab.features.ratios import compute_ratios
from creditrisklab.universe import load_issuers
from valuationlab.triangulate import Method

from keystone.attribution import to_dcf_drivers
from keystone.coherence import DEFAULT_CUSHION_THRESHOLD, DEFAULT_PD_MULTIPLE_THRESHOLD, credit_equity_coherence
from keystone.convergence import convergence_diagnostic
from keystone.credit import DEFAULT_POPULATION_RATE, credit_read
from keystone.domain_screen import compute_training_ranges
from keystone.fetch import LiveFetcher, ReplayFetcher
from keystone.manifest import RunManifest, build_manifest, compare_outputs
from keystone.subjects import SUBJECTS, SubjectConfig
from keystone.tieout import DEFAULT_REL_TOL, FIELDS, tie_out
from keystone.valuation import build_methods, norm_cik

M = {Method.DCF: "dcf", Method.TRADING_COMPS: "comps", Method.PRECEDENT: "precedent"}


def default_parameters(subject: SubjectConfig, as_of: date) -> dict[str, Any]:
    """Everything a run depends on besides EDGAR data, recorded in the manifest. A replay
    recomputes from the CURRENT subject config and reports any difference from this."""
    return {
        "subject": subject.ticker, "as_of": as_of.isoformat(),
        "population_default_rate": DEFAULT_POPULATION_RATE,
        "pd_multiple_threshold": DEFAULT_PD_MULTIPLE_THRESHOLD,
        "cushion_threshold": DEFAULT_CUSHION_THRESHOLD,
        "tieout_rel_tol": DEFAULT_REL_TOL,
        "capm": {"beta": subject.capm.beta, "rf": subject.capm.risk_free_rate,
                 "erp": subject.capm.equity_risk_premium},
        "terminal_growth": subject.terminal_growth, "growth_range": list(subject.growth_range),
        "wacc_grid_offsets": list(subject.wacc_grid_offsets), "peers": list(subject.peers),
        "precedent_deals": [f"{d.acquirer} / {d.target} ({d.announced})" for d in subject.precedent_deals],
        "market": {t: {"price": m.share_price, "shares": m.shares_outstanding, "as_of": m.as_of,
                       "source": m.source} for t, m in sorted(subject.market.items())},
    }


@dataclass(frozen=True)
class RunResult:
    outputs: dict[str, Any]
    credit: Any
    annotated: Any
    coherence: Any
    tieout: Any
    built: dict


def _f(x):
    return None if x is None else float(x)


def compute(subject: SubjectConfig, fetcher, params: dict[str, Any], issuers=None) -> RunResult:
    as_of = date.fromisoformat(params["as_of"])
    issuers = issuers if issuers is not None else load_issuers()
    fundamentals = {norm_cik(i.cik): fetcher.crl_frame(i.cik, i.ticker) for i in issuers}
    panel = build_panel(issuers, fundamentals)
    ranges = compute_training_ranges(panel.to_dict("records"))
    subject_cik = fetcher.cik(subject.ticker)
    frame = fundamentals.get(subject_cik)
    if frame is None:
        frame = fetcher.crl_frame(subject_cik, subject.ticker)
    snap = as_of_snapshot(frame, as_of)
    if snap is None:
        raise RuntimeError(f"No visible {subject.ticker} snapshot as of {as_of}.")
    credit = credit_read(compute_ratios(snap), ranges, panel,
                         population_default_rate=params["population_default_rate"])

    built = build_methods(subject, fetcher)
    annotated = convergence_diagnostic(
        [built["dcf_range"], built["comps_range"], built["precedent_range"]],
        dcf_result=built["dcf"], comps_result=built["comps"],
        precedent_prices=built["precedent_prices"], market_price=built["market_price"])
    tie = tie_out(built["base"], built["base_year"], snap, rel_tol=params["tieout_rel_tol"])
    c = annotated.conclusion
    coherence = credit_equity_coherence(
        credit, market_price=built["market_price"], shares_outstanding=built["shares_outstanding"],
        net_debt=built["net_debt"], method_mids=[r.mid for r in c.ranges],
        pd_multiple_threshold=params["pd_multiple_threshold"],
        cushion_threshold=params["cushion_threshold"])
    recon = to_dcf_drivers(built["dcf"], built["net_debt"], built["shares_outstanding"])

    in_panel = bool(((panel["cik"].map(norm_cik) == subject_cik)
                     & (panel["period_end"].astype(str) == str(snap["period_end"]))).any())
    dv, sc, drv = credit.domain_verdict, credit.scored, built["drivers"]
    out: dict[str, Any] = {
        "subject": subject.ticker, "subject_name": subject.name,
        "credit.period_end": str(snap["period_end"]),
        "credit.observation_in_training_panel": in_panel,
        "credit.cannot_screen": credit.incomplete_reason,
        "credit.in_domain": None if dv is None else bool(dv.in_domain),
        "credit.driving_features": "" if dv is None else ",".join(dv.driving_features),
        "credit.low_confidence_features": "" if dv is None else ",".join(dv.low_confidence_features),
        "credit.pd": None if sc is None else _f(sc.pd),
        "credit.raw_probability": None if sc is None else _f(sc.raw_probability),
        "credit.pd_sensitivity": "" if sc is None else "; ".join(
            f"base rate {t:.0%}: {p:.2%}" for t, p in sorted(sc.pd_sensitivity.items())),
        "credit.calibration_note": "" if sc is None else sc.calibration_note,
        "credit.altman_z": _f(credit.altman_z_double_prime), "credit.altman_zone": credit.altman_zone,
        "valuation.base_year": int(built["base_year"]),
        "valuation.market_price": _f(built["market_price"]),
        "valuation.shares_outstanding": _f(built["shares_outstanding"]),
        "valuation.net_debt": _f(built["net_debt"]),
        "valuation.interest_rate": _f(drv.interest_rate), "valuation.tax_rate": _f(drv.tax_rate),
        "valuation.computed_wacc": _f(built["dcf"].wacc.wacc),
        "valuation.trellis_assumptions": " | ".join(getattr(drv, "assumptions", []) or []),
        "valuation.trellis_derived": " | ".join(built["base"].get("_derived", [])),
        "valuation.anchor": None if c.anchor_method is None else M[c.anchor_method],
        "valuation.warnings": " | ".join(getattr(c, "warnings", []) or []),
    }
    for r in c.ranges:
        k = M[r.method]
        out.update({f"valuation.{k}.low": _f(r.low), f"valuation.{k}.mid": _f(r.mid),
                    f"valuation.{k}.high": _f(r.high), f"valuation.{k}.basis": r.basis,
                    f"valuation.{k}.caveat": r.caveat, f"valuation.{k}.ruled_out": None})
    for d in c.recommendation.disqualified:
        out[f"valuation.{M[d.method]}.ruled_out"] = f"{d.reason} ({d.measured})"
    for n in annotated.shared_input_notes:
        out[f"valuation.overlap.{n.method_a}_{n.method_b}"] = _f(n.overlap_fraction)
    out.update({
        "coherence.verdict": coherence.verdict.value,
        "coherence.credit_elevated": coherence.credit_elevated,
        "coherence.market_stressed": coherence.market_stressed,
        "coherence.equity_cushion": _f(coherence.equity_cushion),
        "coherence.pd_multiple": _f(coherence.pd_multiple),
        "coherence.robust": coherence.robust,
        "coherence.market_to_median_value": _f(coherence.market_to_median_value),
        "coherence.explain": coherence.explain(),
        "tieout.period_aligned": bool(tie.period_aligned),
        "tieout.valuation_inputs_verified": bool(tie.valuation_inputs_verified),
        "tieout.mismatches": ",".join(r.field.trellis_name for r in tie.valuation_input_mismatches),
    })
    for row in tie.rows:
        out[f"tieout.{row.field.trellis_name}.status"] = row.status.value
        out[f"tieout.{row.field.trellis_name}.rel_diff"] = _f(row.rel_diff)
    d = recon.drivers
    out.update({"dcf.fcf_base": _f(d.fcf_base), "dcf.growth_explicit": _f(d.growth_explicit),
                "dcf.wacc": _f(d.wacc), "dcf.terminal_growth": _f(d.terminal_growth),
                "dcf.net_debt": _f(d.net_debt), "dcf.shares_outstanding": _f(d.shares_outstanding),
                "dcf.real_price": _f(recon.real_price), "dcf.reconstructed_price": _f(recon.reconstructed_price)})
    return RunResult(out, credit, annotated, coherence, tie, built)


def run_live(subject: SubjectConfig, out_dir: Path, *, as_of: date | None = None,
             fetcher: LiveFetcher | None = None, issuers=None) -> tuple[RunManifest, RunResult]:
    from keystone.report import render_report
    if as_of is None:
        from creditrisklab.ingest.edgar_client import as_of_today
        as_of = as_of_today()
    fetcher = fetcher or LiveFetcher()
    params = default_parameters(subject, as_of)
    result = compute(subject, fetcher, params, issuers=issuers)
    crl_rec, trellis_rec, ciks = fetcher.save(out_dir / "cache")
    manifest = build_manifest(subject.ticker, params, {}, crl_rec, result.outputs,
                              trellis_cache=trellis_rec, ciks=ciks)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(manifest.to_json())
    (out_dir / "report.md").write_text(render_report(manifest), encoding="utf-8")
    return manifest, result


def replay(manifest_path: Path, issuers=None) -> list[str]:
    """Offline. [] means every output reproduced exactly. Also reports any change to the
    subject's config since the run, so a difference can be traced to its cause."""
    manifest = RunManifest.from_json(manifest_path.read_text())
    subject = SUBJECTS[manifest.subject]
    now = default_parameters(subject, date.fromisoformat(manifest.parameters["as_of"]))
    config_diffs = [f"config changed since run: {k}" for k in sorted(set(now) | set(manifest.parameters))
                    if now.get(k) != manifest.parameters.get(k)]
    fetcher = ReplayFetcher(manifest, manifest_path.parent / "cache")
    result = compute(subject, fetcher, manifest.parameters, issuers=issuers)
    return config_diffs + compare_outputs(manifest.outputs, result.outputs)
