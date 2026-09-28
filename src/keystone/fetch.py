"""fetch.py — the only place Keystone touches the network, and how every run replays.

LiveFetcher calls EDGAR (CIK resolution, CreditRiskLab fundamentals, Trellis raw
observations) and remembers every response; save() writes them to a cache with sha256
hashes recorded in the manifest. ReplayFetcher serves the same interface from that cache,
refusing any file whose hash changed. Trellis is cached as RAW observations, not finished
tables, so a replay re-runs Trellis's own resolution: if Trellis's code changes between run
and replay, the replay reports the drift instead of hiding it.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from keystone.manifest import CacheIntegrityError, load_fundamentals_cache, save_fundamentals_cache, sha256_file
from keystone.valuation import norm_cik


def _build_table(raw: dict) -> tuple[dict, set]:
    """Fresh table on every call: fill_derived_gaps mutates what it is given."""
    from trellis.statements import build_annual_table
    result = build_annual_table(raw)
    return result.table, {sp.year for sp in result.stub_periods}


def _raw_to_json(raw: dict) -> dict:
    return {k: (list(v) if k == "_missing" else [dataclasses.asdict(o) for o in v]) for k, v in raw.items()}


def _raw_from_json(data: dict) -> dict:
    from trellis.ingest import Observation
    return {k: (v if k == "_missing" else [Observation(**o) for o in v]) for k, v in data.items()}


class LiveFetcher:
    def __init__(self) -> None:
        self.ciks: dict[str, str] = {}
        self.crl_frames: dict = {}
        self.trellis_raw: dict = {}

    # Network hooks -- the only methods that reach EDGAR. Tests override these.
    def _resolve(self, ticker: str) -> str | None:
        from creditrisklab.ingest.edgar_client import resolve_cik_by_ticker
        return resolve_cik_by_ticker(ticker)

    def _crl_fetch(self, cik: str, ticker: str):
        from creditrisklab.ingest.trellis_adapter import fetch_fundamentals
        return fetch_fundamentals(cik, ticker=ticker)

    def _trellis_fetch(self, cik: str) -> dict:
        from trellis.ingest import fetch_all
        return fetch_all(int(cik))

    def cik(self, ticker: str) -> str:
        if ticker not in self.ciks:
            found = self._resolve(ticker)
            if found is None:
                raise ValueError(f"{ticker}: could not resolve CIK via SEC's ticker map.")
            self.ciks[ticker] = norm_cik(found)
        return self.ciks[ticker]

    def crl_frame(self, cik, ticker: str):
        cik = norm_cik(cik)
        if cik not in self.crl_frames:
            self.crl_frames[cik] = self._crl_fetch(cik, ticker)
        return self.crl_frames[cik]

    def trellis_table(self, cik) -> tuple[dict, set]:
        cik = norm_cik(cik)
        if cik not in self.trellis_raw:
            self.trellis_raw[cik] = self._trellis_fetch(cik)
        return _build_table(self.trellis_raw[cik])

    def save(self, cache_dir: Path) -> tuple[dict, dict, dict]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        crl_record = save_fundamentals_cache(self.crl_frames, cache_dir)
        trellis_record = {}
        for cik, raw in self.trellis_raw.items():
            path = cache_dir / f"trellis_{cik}.json"
            path.write_text(json.dumps(_raw_to_json(raw), sort_keys=True))
            trellis_record[cik] = {"file": path.name, "sha256": sha256_file(path)}
        return crl_record, trellis_record, dict(self.ciks)


class ReplayFetcher:
    def __init__(self, manifest, cache_dir: Path) -> None:
        self._ciks = dict(manifest.ciks)
        self._crl = load_fundamentals_cache(cache_dir, manifest.edgar_cache)
        self._trellis = {}
        for cik, entry in manifest.trellis_cache.items():
            path = cache_dir / entry["file"]
            if sha256_file(path) != entry["sha256"]:
                raise CacheIntegrityError(f"{path.name}: sha256 does not match the manifest -- "
                                          f"refusing to replay against a changed input.")
            self._trellis[cik] = _raw_from_json(json.loads(path.read_text()))

    def cik(self, ticker: str) -> str:
        if ticker not in self._ciks:
            raise ValueError(f"{ticker}: not in this run's recorded CIKs -- cannot resolve offline.")
        return self._ciks[ticker]

    def crl_frame(self, cik, ticker: str):
        return self._crl[norm_cik(cik)]

    def trellis_table(self, cik) -> tuple[dict, set]:
        return _build_table(self._trellis[norm_cik(cik)])
