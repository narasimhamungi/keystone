"""report.py — the run report: one static Markdown file per run, rendered ONLY from the
manifest. Same manifest -> byte-identical report (it can be re-rendered any time without
re-running), and Markdown renders on GitHub and diffs cleanly, so a number that moves
between runs shows up in `git diff`.
"""
from __future__ import annotations

from keystone.manifest import RunManifest
from keystone.tieout import FIELDS

METHODS = (("dcf", "DCF"), ("comps", "Trading comps"), ("precedent", "Precedent transactions"))


def _money(x) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _big(x) -> str:
    if x is None:
        return "n/a"
    return f"-${abs(x) / 1e9:,.2f}B" if x < 0 else f"${x / 1e9:,.2f}B"


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}%}"


def _yn(x) -> str:
    return "n/a" if x is None else ("yes" if x else "no")


def render_report(m: RunManifest) -> str:
    o, p = m.outputs, m.parameters
    L: list[str] = []
    add = L.append
    add(f"# Keystone — {o['subject_name']} ({o['subject']})")
    add("")
    add(f"Run as of {p['as_of']} · manifest v{m.manifest_version} · parameters `{m.parameters_hash[:12]}`")
    add("")

    add("## Verdict")
    add("")
    verdict = o["coherence.verdict"].upper()
    text = o["coherence.explain"]
    if text.upper().startswith(verdict + ":"):
        text = text.split(":", 1)[1].strip()
    add(f"**{verdict}** — {text}")
    add("")
    add("| Signal | Reading | Stressed |")
    add("|---|---|---|")
    credit_reading = (f"PD {_pct(o['credit.pd'], 2)} ({o['coherence.pd_multiple']:.1f}x base rate; "
                      f"elevated above {p['pd_multiple_threshold']:.1f}x)" if o["coherence.pd_multiple"] is not None
                      else f"Altman Z'' {o['credit.altman_z']:.2f} ({o['credit.altman_zone']})"
                      if o["credit.altman_z"] is not None else "no credit signal")
    add(f"| Credit | {credit_reading} | {_yn(o['coherence.credit_elevated'])} |")
    add(f"| Market | equity is {_pct(o['coherence.equity_cushion'])} of enterprise value "
        f"(stressed below {p['cushion_threshold']:.0%}) | {_yn(o['coherence.market_stressed'])} |")
    add("")

    add("## Credit read (CreditRiskLab)")
    add("")
    status = ("in-sample: this exact observation is in the training panel" if o["credit.observation_in_training_panel"]
              else "out-of-sample for this fiscal year")
    add(f"- Period ending {o['credit.period_end']} ({status}).")
    if o["credit.cannot_screen"]:
        add(f"- Domain screen could not run: {o['credit.cannot_screen']}")
    else:
        dom = "in domain" if o["credit.in_domain"] else f"OUT of domain (driving: {o['credit.driving_features']}); PD suppressed"
        add(f"- Domain screen: {dom}.")
        if o["credit.low_confidence_features"]:
            add(f"- Low-confidence features (cap-bounded training range): {o['credit.low_confidence_features']}.")
    if o["credit.pd"] is not None:
        add(f"- PD {_pct(o['credit.pd'], 2)} at a {p['population_default_rate']:.0%} population default rate "
            f"(sensitivity — {o['credit.pd_sensitivity']}).")
        add(f"- {o['credit.calibration_note']}")
    z = o["credit.altman_z"]
    add(f"- Altman Z'' {'n/a' if z is None else format(z, '.2f')} ({o['credit.altman_zone']}).")
    add("")

    add("## Valuation")
    add("")
    add(f"Market price {_money(o['valuation.market_price'])} · base FY{o['valuation.base_year']} · net debt "
        f"{_big(o['valuation.net_debt'])} · cost of debt {_pct(o['valuation.interest_rate'], 2)} · tax rate "
        f"{_pct(o['valuation.tax_rate'])} · computed WACC {_pct(o['valuation.computed_wacc'], 2)}")
    add("")
    add("| Method | Low | Mid | High | Status |")
    add("|---|---|---|---|---|")
    for key, label in METHODS:
        ro = o.get(f"valuation.{key}.ruled_out")
        add(f"| {label} | {_money(o.get(f'valuation.{key}.low'))} | {_money(o.get(f'valuation.{key}.mid'))} | "
            f"{_money(o.get(f'valuation.{key}.high'))} | {'ruled out: ' + ro if ro else 'usable'} |")
    add("")
    add(f"Anchor: {o['valuation.anchor'] or 'none — no method passed every check'}. "
        f"Market price is {_pct(o['coherence.market_to_median_value'], 0)} of the median method value.")
    add("")
    for key, label in METHODS:
        add(f"- **{label}** — {o.get(f'valuation.{key}.basis')}. {o.get(f'valuation.{key}.caveat')}")
    overlaps = sorted((k.split(".")[-1], v) for k, v in o.items() if k.startswith("valuation.overlap."))
    add(f"- **Shared subject inputs between methods** — "
        + ", ".join(f"{k.replace('_', ' vs ')} {v:.0%}" for k, v in overlaps) + ".")
    if o["valuation.trellis_assumptions"]:
        add(f"- **Trellis assumptions** — {o['valuation.trellis_assumptions']}")
    if o["valuation.trellis_derived"]:
        add(f"- **Derived (not reported) base-year fields** — {o['valuation.trellis_derived']}")
    add("")

    add("## Data tie-out (Trellis vs CreditRiskLab)")
    add("")
    if not o["tieout.period_aligned"]:
        add(f"Not compared: the two spines describe different fiscal periods (credit period ending "
            f"{o['credit.period_end']}, valuation base FY{o['valuation.base_year']}).")
    else:
        add("| Field | Feeds valuation | Status | Difference |")
        add("|---|---|---|---|")
        for f in FIELDS:
            st = o.get(f"tieout.{f.trellis_name}.status")
            rd = o.get(f"tieout.{f.trellis_name}.rel_diff")
            add(f"| {f.trellis_name} | {'yes' if f.valuation_input else 'no'} | {st} | "
                f"{'n/a' if rd is None else f'{rd:+.3%}'} |")
        add("")
        add("Valuation inputs **verified** across both spines." if o["tieout.valuation_inputs_verified"]
            else f"Valuation inputs **NOT verified**: {o['tieout.mismatches']}.")
    add("")

    add("## Reproducibility")
    add("")
    add("| Package | Version | Source | Commit |")
    add("|---|---|---|---|")
    for name, info in sorted(m.packages.items()):
        commit = (info.get("commit") or "")[:12] + (" (dirty)" if info.get("dirty") else "")
        add(f"| {name} | {info.get('version', '-')} | {info.get('source', 'not installed')} | {commit or '-'} |")
    add("")
    add("| Ticker | Price | Shares | As of | Source |")
    add("|---|---|---|---|---|")
    for t, md in sorted(p["market"].items()):
        add(f"| {t} | {_money(md['price'])} | {md['shares']:,.0f} | {md['as_of']} | {md['source']} |")
    add("")
    add(f"Replay offline: `python -m keystone replay <this run's manifest.json>` — every number above "
        f"must reproduce exactly from the cached EDGAR data ({len(m.edgar_cache)} CreditRiskLab frames, "
        f"{len(m.trellis_cache)} Trellis observation sets).")
    add("")
    return "\n".join(L)
