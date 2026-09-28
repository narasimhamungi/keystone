"""Full pipeline, offline: live -> cache -> manifest -> report -> replay, through the real
code with only the network replaced (see conftest.FakeFetcher)."""
import dataclasses
import json
import shutil

import pytest

from keystone import run as runmod
from keystone.manifest import CacheIntegrityError, RunManifest
from keystone.report import render_report
from keystone.subjects import SUBJECTS
from tests.conftest import AS_OF


@pytest.fixture
def live(tmp_path, fake_fetcher, synthetic_credit):
    issuers, _ = synthetic_credit
    out = tmp_path / "JNJ"
    manifest, result = runmod.run_live(SUBJECTS["JNJ"], out, as_of=AS_OF, fetcher=fake_fetcher, issuers=issuers)
    return out, manifest, result, issuers


def test_live_run_writes_manifest_cache_and_report(live):
    out, manifest, _, _ = live
    assert (out / "manifest.json").exists() and (out / "report.md").exists()
    assert len(manifest.trellis_cache) == 5  # J&J + 4 peers, raw observations
    assert manifest.ciks["JNJ"] == "0000200406"
    assert manifest.manifest_version == 2


def test_replay_reproduces_every_output_exactly(live):
    out, _, _, issuers = live
    assert runmod.replay(out / "manifest.json", issuers=issuers) == []


def test_replay_refuses_a_tampered_trellis_cache(live):
    out, manifest, _, issuers = live
    f = out / "cache" / next(iter(manifest.trellis_cache.values()))["file"]
    f.write_text(f.read_text().replace("10-K", "10-Q", 1))
    with pytest.raises(CacheIntegrityError):
        runmod.replay(out / "manifest.json", issuers=issuers)


def test_replay_names_a_config_change_and_its_effect(live, monkeypatch):
    out, _, _, issuers = live
    changed = dataclasses.replace(SUBJECTS["JNJ"], terminal_growth=0.02)
    monkeypatch.setitem(SUBJECTS, "JNJ", changed)
    diffs = runmod.replay(out / "manifest.json", issuers=issuers)
    assert "config changed since run: terminal_growth" in diffs
    assert any(d.startswith("valuation.dcf.") or d.startswith("dcf.") for d in diffs)


def test_report_is_deterministic_and_rendered_only_from_the_manifest(live):
    out, manifest, _, _ = live
    again = render_report(RunManifest.from_json((out / "manifest.json").read_text()))
    assert again == (out / "report.md").read_text(encoding="utf-8") == render_report(manifest)
    assert "## Verdict" in again and "## Data tie-out" in again and "## Reproducibility" in again


def test_mismatched_fiscal_years_are_not_tied_out(live):
    """Synthetic credit ends in FY2023; the valuation base is FY2025 -- the pairing the old
    coherence script once made silently must be refused."""
    _, manifest, _, _ = live
    o = manifest.outputs
    assert o["credit.period_end"].startswith("2023") and o["valuation.base_year"] == 2025
    assert o["tieout.period_aligned"] is False
    assert "Not compared" in render_report(manifest)


def test_outputs_are_flat_json_scalars(live):
    _, manifest, _, _ = live
    for k, v in manifest.outputs.items():
        assert v is None or isinstance(v, (bool, int, float, str)), k
    json.dumps(manifest.outputs)


def test_compare_attributes_a_dcf_change_with_bridgework(live, tmp_path, fake_fetcher, synthetic_credit, monkeypatch):
    from keystone.__main__ import _attribution
    out_a, _, _, issuers = live
    monkeypatch.setitem(SUBJECTS, "JNJ", dataclasses.replace(SUBJECTS["JNJ"], terminal_growth=0.02))
    from tests.conftest import FakeFetcher
    out_b = tmp_path / "JNJ_b"
    runmod.run_live(SUBJECTS["JNJ"], out_b, as_of=AS_OF,
                    fetcher=FakeFetcher(*synthetic_credit), issuers=issuers)
    text = _attribution(out_a / "manifest.json", out_b / "manifest.json")
    assert "terminal_growth" in text and "% of change" in text
