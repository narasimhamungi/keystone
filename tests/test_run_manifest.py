"""test_run_manifest.py — cache round-trip verified against CreditRiskLab's
synthetic panel (real code path, synthetic data, no network)."""
import json

import pytest

from creditrisklab.config import feature_names, load_model_config
from creditrisklab.features.panel import build_panel
from creditrisklab.synthetic import build_fundamentals, stamped_issuers
from creditrisklab.universe import load_issuers

from keystone.credit import score_new_issuer
from keystone.manifest import (CacheIntegrityError, RunManifest, build_manifest, compare_outputs,
                          input_hash, load_fundamentals_cache, package_provenance,
                          save_fundamentals_cache, sha256_file)


@pytest.fixture(scope="module")
def synthetic():
    issuers = stamped_issuers(load_issuers())
    return issuers, build_fundamentals(issuers, seed=42)


def test_input_hash_is_key_order_independent():
    assert input_hash({"a": 1, "b": 2}) == input_hash({"b": 2, "a": 1})
    assert input_hash({"a": 1}) != input_hash({"a": 2})


def test_cache_round_trip_is_bit_exact(synthetic, tmp_path):
    issuers, fund = synthetic
    record = save_fundamentals_cache(fund, tmp_path)
    reloaded = load_fundamentals_cache(tmp_path, record)
    a, b = build_panel(issuers, fund), build_panel(issuers, reloaded)
    feats = feature_names(load_model_config())
    assert a[feats].equals(b[feats])
    subject = {f: a.iloc[3][f] for f in feats}
    assert score_new_issuer(subject, a).pd == score_new_issuer(subject, b).pd


def test_reloaded_frame_matches_edgar_client_schema(synthetic, tmp_path):
    _, fund = synthetic
    record = save_fundamentals_cache(fund, tmp_path)
    frame = next(iter(load_fundamentals_cache(tmp_path, record).values()))
    assert isinstance(frame["cik"].iloc[0], str) and frame["cik"].iloc[0].startswith("000")
    assert type(frame["period_end"].iloc[0]).__name__ == "date"


def test_tampered_cache_is_refused(synthetic, tmp_path):
    _, fund = synthetic
    record = save_fundamentals_cache(fund, tmp_path)
    cik, entry = next(iter(record.items()))
    with open(tmp_path / entry["file"], "a") as f:
        f.write("\n")
    with pytest.raises(CacheIntegrityError):
        load_fundamentals_cache(tmp_path, {cik: entry})


def test_compare_outputs():
    base = {"pd": 0.0033, "verdict": "supports", "ok": True}
    assert compare_outputs(base, dict(base)) == []
    assert compare_outputs(base, dict(base, pd=0.0034))
    assert compare_outputs(base, dict(base, ok=False))
    assert compare_outputs(base, {"pd": 0.0033, "verdict": "supports"})  # missing key


def test_package_provenance_reports_uninstalled():
    assert package_provenance("definitely-not-a-real-package-xyz") == {"installed": False}


def test_package_provenance_for_installed_package():
    info = package_provenance("creditrisklab")
    assert info["installed"] and info["version"]
    assert info["source"] in {"editable", "vcs", "unknown (no PEP 610 install record)",
                              "local archive/directory"}


def _no_direct_url(real_distribution):
    def patched(name):
        d = real_distribution(name)
        if name == "creditrisklab":
            orig = d.read_text
            d.read_text = lambda f: None if f == "direct_url.json" else orig(f)
        return d
    return patched


def test_provenance_fallback_recovers_the_commit_from_a_git_checkout(tmp_path):
    """Regression for a real Windows install where pip wrote no PEP 610 record. The
    fallback locates the package's own folder and reads its git HEAD. Builds its own git
    repo, so the result doesn't depend on how this machine installed creditrisklab (the
    first version assumed an editable checkout and failed against a pinned install)."""
    import importlib.metadata as md
    import subprocess
    from unittest import mock
    import keystone.manifest as km

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    with mock.patch("importlib.metadata.distribution", side_effect=_no_direct_url(md.distribution)), \
         mock.patch.object(km, "_locate_via_module", return_value=tmp_path):
        info = package_provenance("creditrisklab")
    assert info["source"] == "editable (recovered via module path, no PEP 610 record)"
    assert info["commit"] == head


def test_provenance_fallback_says_unknown_outside_a_git_checkout(tmp_path):
    import importlib.metadata as md
    from unittest import mock
    import keystone.manifest as km

    with mock.patch("importlib.metadata.distribution", side_effect=_no_direct_url(md.distribution)), \
         mock.patch.object(km, "_locate_via_module", return_value=tmp_path):
        info = package_provenance("creditrisklab")
    assert info["source"].startswith("unknown (found at") and "not a git checkout" in info["source"]
    assert "commit" not in info

def test_manifest_json_round_trip(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    m = build_manifest("JNJ", {"tau": 0.02}, {"x": f}, {}, {"pd": 0.01})
    back = RunManifest.from_json(m.to_json())
    assert back.outputs == {"pd": 0.01}
    assert back.input_files["x"] == sha256_file(f)
    assert back.parameters_hash == input_hash({"tau": 0.02})
    json.loads(m.to_json())  # valid JSON
