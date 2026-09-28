"""run_manifest.py — the reproducibility record for a Keystone run.

Lifts Bridgework's audit.py pattern (sorted-keys JSON input hash, dependency
fingerprint, UTC timestamp) and fixes the one part that doesn't transfer:
Bridgework's _code_version() runs `git rev-parse` in the CURRENT WORKING
DIRECTORY, which records the commit of wherever the command was launched from,
not of the package that computed the numbers. Keystone consumes several repos,
some pip-installed from a git URL with no .git folder at all (Trellis, via
ValuationLab's dependency). Provenance here comes from each distribution's own
PEP 610 install record (direct_url.json): the exact commit_id for git installs,
the source directory (then its git HEAD, and whether it has uncommitted
changes) for editable installs.

EDGAR REPLAY CACHE
--------------------
CreditRiskLab's training panel and every candidate score are built from live
EDGAR fetches, so a run is not reproducible unless those fetched frames are
kept. save_fundamentals_cache() writes each issuer's long-format frame to CSV
and records its sha256; load_fundamentals_cache() refuses any file whose hash
no longer matches the manifest. A replay from cache needs no network at all.

CSV was chosen over pickle (not safe to load from an untrusted source, and
opaque) and parquet (pyarrow is not a dependency of any consumed repo). Two
things are restored on the way back in, both measured rather than assumed:

  * Floats are read with float_precision="round_trip". pandas' default C float
    parser is NOT exact: on CreditRiskLab's synthetic panel it reproduced
    values to ~7e-15 relative, so the rebuilt panel failed an exact
    DataFrame.equals check (a tolerant assert_frame_equal hid this in a first
    verification pass). With round_trip the rebuilt panel is exactly equal.
  * cik is read as a string and period_end/filed as dates, so the reloaded
    frame has exactly the schema edgar_client returns. A naive read turns
    "0001166126" into the int 1166126; build_panel happens not to depend on
    that column (it takes cik from the Issuer object), so this is kept for
    schema fidelity, not because a breakage was observed.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

MANIFEST_VERSION = 2  # v2: Trellis raw-observation cache + resolved CIKs
KEYSTONE_PACKAGES = ("keystone", "trellis", "valuationlab", "creditrisklab", "bridgework")
NUMERIC_DEPENDENCIES = ("pandas", "numpy", "scipy", "scikit-learn")
FRAME_COLUMNS = ("cik", "ticker", "field", "period_end", "filed", "form",
                 "fiscal_year", "value", "tag", "source")


class CacheIntegrityError(ValueError):
    """A cached input's sha256 no longer matches the manifest. Raised rather
    than replaying against a file that silently changed."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def input_hash(payload: Any) -> str:
    """Bridgework's convention: sha256 of sorted-keys JSON."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=5, check=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _file_uri_to_path(url: str) -> Path | None:
    if not url.startswith("file://"):
        return None
    text = url[len("file://"):]
    # file:///N:/... on Windows leaves a leading slash before the drive letter.
    if len(text) > 2 and text[0] == "/" and text[2] == ":":
        text = text[1:]
    import urllib.parse
    return Path(urllib.parse.unquote(text))


def _git_root_and_commit(path: Path) -> tuple[str | None, bool | None]:
    root = _git(path, "rev-parse", "--show-toplevel")
    if root is None:
        return None, None
    commit = _git(Path(root), "rev-parse", "HEAD")
    status = _git(Path(root), "status", "--porcelain")
    return commit, (None if status is None else bool(status))


def _locate_via_module(dist_name: str) -> Path | None:
    """Fallback when pip wrote no (or an unreadable) direct_url.json: find the
    package's own source tree from its importable module, independent of how
    pip recorded the install. Only used as a fallback -- direct_url.json, when
    present, is authoritative and checked first."""
    import importlib.util

    module_name = dist_name.lower().replace("-", "_")
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    origin = spec.submodule_search_locations[0] if spec.submodule_search_locations else spec.origin
    return Path(origin).resolve() if origin else None


def package_provenance(dist_name: str) -> dict[str, Any]:
    import importlib.metadata as md

    try:
        dist = md.distribution(dist_name)
    except md.PackageNotFoundError:
        return {"installed": False}
    info: dict[str, Any] = {"installed": True, "version": dist.version}
    raw = dist.read_text("direct_url.json")

    if raw:
        direct = json.loads(raw)
        info["url"] = direct.get("url")
        if "vcs_info" in direct:
            info["source"] = "vcs"
            info["commit"] = direct["vcs_info"].get("commit_id")
            return info
        if direct.get("dir_info", {}).get("editable"):
            info["source"] = "editable"
            path = _file_uri_to_path(direct.get("url", ""))
            if path is not None:
                info["commit"], info["dirty"] = _git_root_and_commit(path)
            return info
        info["source"] = "local archive/directory"
        return info

    # No PEP 610 record (observed on at least one real Windows install this
    # tool has seen, for a reason not diagnosed -- the module-path fallback
    # below doesn't need to know why, it just needs to find the code).
    path = _locate_via_module(dist_name)
    if path is None:
        info["source"] = "unknown (no PEP 610 install record, module not importable)"
        return info
    commit, dirty = _git_root_and_commit(path)
    if commit is None:
        info["source"] = f"unknown (found at {path}, not a git checkout)"
        return info
    info["source"] = "editable (recovered via module path, no PEP 610 record)"
    info["commit"], info["dirty"] = commit, dirty
    return info


def dependency_fingerprint(packages: Iterable[str] = NUMERIC_DEPENDENCIES) -> str:
    import importlib.metadata as md

    parts = []
    for pkg in packages:
        try:
            parts.append(f"{pkg}=={md.version(pkg)}")
        except md.PackageNotFoundError:
            parts.append(f"{pkg}==absent")
    joined = ";".join(parts)
    return f"{hashlib.sha256(joined.encode()).hexdigest()[:12]} ({joined})"


def save_fundamentals_cache(fundamentals: Mapping[str, pd.DataFrame],
                            cache_dir: Path) -> dict[str, dict[str, str]]:
    """Writes one CSV per CIK; returns {cik: {"file": name, "sha256": hex}}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    record = {}
    for cik, frame in fundamentals.items():
        path = cache_dir / f"fundamentals_{cik}.csv"
        frame.loc[:, list(FRAME_COLUMNS)].to_csv(path, index=False)
        record[str(cik)] = {"file": path.name, "sha256": sha256_file(path)}
    return record


def load_fundamentals_cache(cache_dir: Path,
                            record: Mapping[str, Mapping[str, str]]) -> dict[str, pd.DataFrame]:
    out = {}
    for cik, entry in record.items():
        path = cache_dir / entry["file"]
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            raise CacheIntegrityError(
                f"{path.name}: sha256 {actual[:12]}... does not match manifest "
                f"{entry['sha256'][:12]}... -- refusing to replay against a changed input.")
        frame = pd.read_csv(path, float_precision="round_trip",
                            dtype={"cik": str, "ticker": str, "field": str,
                                   "form": str, "tag": str, "source": str})
        for col in ("period_end", "filed"):
            frame[col] = pd.to_datetime(frame[col]).dt.date
        frame["fiscal_year"] = frame["fiscal_year"].astype("int64")
        frame["value"] = frame["value"].astype("float64")
        out[cik] = frame
    return out


@dataclass
class RunManifest:
    subject: str
    parameters: dict[str, Any]
    input_files: dict[str, str]          # label -> sha256
    edgar_cache: dict[str, dict[str, str]]  # cik -> {file, sha256}
    outputs: dict[str, Any]
    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    dependency_fingerprint: str = ""
    parameters_hash: str = ""
    run_timestamp_utc: str = ""
    python_version: str = ""
    platform: str = ""
    manifest_version: int = MANIFEST_VERSION
    trellis_cache: dict[str, dict[str, str]] = field(default_factory=dict)  # cik -> {file, sha256}
    ciks: dict[str, str] = field(default_factory=dict)  # ticker -> 10-digit CIK, as resolved live

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def from_json(cls, text: str) -> "RunManifest":
        return cls(**json.loads(text))


def build_manifest(subject: str, parameters: Mapping[str, Any],
                   input_files: Mapping[str, Path],
                   edgar_cache: Mapping[str, Mapping[str, str]],
                   outputs: Mapping[str, Any], *,
                   trellis_cache: Mapping[str, Mapping[str, str]] | None = None,
                   ciks: Mapping[str, str] | None = None) -> RunManifest:
    return RunManifest(
        subject=subject,
        parameters=dict(parameters),
        input_files={label: sha256_file(Path(p)) for label, p in input_files.items()},
        edgar_cache={k: dict(v) for k, v in edgar_cache.items()},
        outputs=dict(outputs),
        packages={name: package_provenance(name) for name in KEYSTONE_PACKAGES},
        dependency_fingerprint=dependency_fingerprint(),
        parameters_hash=input_hash(dict(parameters)),
        run_timestamp_utc=datetime.now(timezone.utc).isoformat(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        trellis_cache={k: dict(v) for k, v in (trellis_cache or {}).items()},
        ciks=dict(ciks or {}),
    )


def compare_outputs(expected: Mapping[str, Any], actual: Mapping[str, Any],
                    rel_tol: float = 1e-12) -> list[str]:
    """Differences between two output dicts; empty list means reproduced.
    Numbers compared to rel_tol (near-exact by default: a deterministic replay
    of the same inputs should match to floating-point noise, not 'roughly');
    everything else by equality."""
    diffs = []
    for key in sorted(set(expected) | set(actual)):
        if key not in expected or key not in actual:
            diffs.append(f"{key}: present in only one run")
            continue
        a, b = expected[key], actual[key]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                and not isinstance(a, bool) and not isinstance(b, bool):
            scale = max(abs(a), abs(b), 1e-300)
            if abs(a - b) / scale > rel_tol:
                diffs.append(f"{key}: {a!r} vs {b!r}")
        elif a != b:
            diffs.append(f"{key}: {a!r} vs {b!r}")
    return diffs
