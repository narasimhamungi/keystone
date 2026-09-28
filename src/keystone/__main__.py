"""Keystone command line.

  python -m keystone run JNJ|CYH [--out keystone_out]   live run -> manifest, cache, report.md,
                                                        then an immediate offline replay check
  python -m keystone replay <manifest.json>             offline; every output must reproduce
  python -m keystone report <manifest.json>             re-render the report from a manifest
  python -m keystone compare <old.json> <new.json>      Bridgework Shapley attribution of the
                                                        DCF price change between two runs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _attribution(old_path: Path, new_path: Path) -> str:
    from bridgework.dcf import DCFDrivers
    from keystone.attribution import ReconstructedDCF, attribute_dcf_change
    from keystone.manifest import RunManifest

    def recon(path: Path) -> ReconstructedDCF:
        o = RunManifest.from_json(path.read_text()).outputs
        d = DCFDrivers(fcf_base=o["dcf.fcf_base"], growth_explicit=o["dcf.growth_explicit"],
                       wacc=o["dcf.wacc"], terminal_growth=o["dcf.terminal_growth"],
                       net_debt=o["dcf.net_debt"], shares_outstanding=o["dcf.shares_outstanding"])
        return ReconstructedDCF(d, o["dcf.real_price"], o["dcf.reconstructed_price"])

    a, b = recon(old_path), recon(new_path)
    table = attribute_dcf_change(a, b)
    lines = [f"DCF price {a.real_price:,.2f} -> {b.real_price:,.2f} (reconstruction error "
             f"{a.reconstruction_error_pct:+.2%} / {b.reconstruction_error_pct:+.2%}; the change is "
             f"attributed on the reconstructed model):"]
    for _, r in table.iterrows():
        lines.append(f"  {r['driver']:<20}{r['shapley_contribution']:>+12,.2f}  ({r['pct_of_variance']:.1f}% of change)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="keystone")
    sub = ap.add_subparsers(dest="cmd", required=True)
    from keystone.subjects import SUBJECTS
    p_run = sub.add_parser("run")
    p_run.add_argument("subject", choices=sorted(SUBJECTS))
    p_run.add_argument("--out", type=Path, default=Path("keystone_out"))
    sub.add_parser("replay").add_argument("manifest", type=Path)
    sub.add_parser("report").add_argument("manifest", type=Path)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("old", type=Path)
    p_cmp.add_argument("new", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "compare":
        print(_attribution(args.old, args.new))
        return 0
    if args.cmd == "report":
        from keystone.manifest import RunManifest
        from keystone.report import render_report
        print(render_report(RunManifest.from_json(args.manifest.read_text())))
        return 0
    from keystone.run import replay, run_live
    if args.cmd == "replay":
        diffs = replay(args.manifest)
        print("REPRODUCED: every output matches the manifest." if not diffs else
              "NOT REPRODUCED:\n  " + "\n  ".join(diffs))
        return 0 if not diffs else 1

    from dotenv import load_dotenv
    load_dotenv(override=True)  # CREDITRISKLAB_SEC_UA and TRELLIS_USER_AGENT
    out_dir = args.out / args.subject
    manifest, result = run_live(SUBJECTS[args.subject], out_dir)
    print(result.outputs["coherence.explain"])
    print(f"Report: {(out_dir / 'report.md').resolve()}")
    diffs = replay(out_dir / "manifest.json")
    print("Replay from cache (no network): " + ("REPRODUCED -- every output matches." if not diffs
          else "NOT REPRODUCED: " + "; ".join(diffs)))
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
