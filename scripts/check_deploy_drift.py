#!/usr/bin/env python3
"""S195 follow-up: detect drift between the local repo and the running VPS.

Catches the bug class where a manual prod hot-patch (e.g. `sudo pip install
rapidfuzz`, `ALTER TABLE ... SET DEFAULT NOW()`) is never lifted into code,
so the next fresh-venv deploy silently loses the patch.

Two probes:
  - `pip` drift: parses `requirements.txt` + `requirements-improvements.txt`
    and compares the explicitly-listed direct dependencies against the VPS
    venv's leaf packages (`pip list --not-required`). Leaf = no other
    installed package depends on it, so it lines up with what someone
    actually asked pip to install. Drift = a leaf on the VPS that isn't
    declared locally (the rapidfuzz hot-patch shape) OR a declared package
    that isn't installed at all (the "fresh deploy will lose it" shape).
  - `schema` drift: queries pg_catalog for SQL DEFAULTs on the columns S195
    hot-patched (esports_team_aliases.created_at, esports_unmatched_
    predictions.event_time). If those columns lack a SET DEFAULT on prod and
    migration 075 has not yet been applied, surface it.

Usage::
    python scripts/check_deploy_drift.py            # both probes
    python scripts/check_deploy_drift.py --pip      # pip drift only
    python scripts/check_deploy_drift.py --schema   # schema drift only
    python scripts/check_deploy_drift.py --vps user@host --venv /opt/.../venv

Exit codes::
    0 — no drift detected
    1 — drift detected (output names the probe + offenders)
    2 — usage / SSH / config error

This is intentionally non-blocking from deploy.sh in this first cut. Operators
should run it before each deploy after a session that touched dependencies or
applied any prod ALTER. Future revision can wire it as a deploy preflight gate
once the false-positive rate of the transitive-dep heuristic is measured.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

_REPO = Path(__file__).resolve().parent.parent
_REQ_FILES = ("requirements.txt", "requirements-improvements.txt")

# Leaf packages we always expect to see installed in the VPS venv even though
# they are not declared in requirements*.txt. pip itself, the build/install
# tooling, and a small pinch of standard scaffolding live here.
_KNOWN_VENV_INFRA = {
    "pip",
    "setuptools",
    "wheel",
}


_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """PEP 503 normalization: lowercase, `_`/`.` → `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirements(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _REQ_NAME_RE.match(s)
        if m:
            names.add(_normalize(m.group(1)))
    return names


def _parse_pip_freeze(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-e "):
            continue
        m = _REQ_NAME_RE.match(s)
        if m:
            names.add(_normalize(m.group(1)))
    return names


def _ssh_run(vps: str, key: str, cmd: str) -> str:
    args = [
        "ssh",
        "-i", key,
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        vps,
        cmd,
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"ssh failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def probe_pip(vps: str, key: str, venv: str) -> tuple[list[str], list[str]]:
    """Compare local requirements vs VPS leaf packages.

    Returns ``(extras, missing_required)``:
      - ``extras``: leaf packages on the VPS that are NOT declared locally
        (neither in requirements.txt nor in requirements-improvements.txt).
        These are the rapidfuzz-hot-patch shape — manual installs that
        future fresh deploys would silently lose. Reported, not gated:
        operators may legitimately install ad-hoc tooling.
      - ``missing_required``: packages declared in requirements.txt that are
        NOT installed on the VPS. This IS a real deploy hazard — the fresh
        venv path would also miss them. Packages declared only in
        requirements-improvements.txt are intentionally not installed by the
        current deploy script and are excluded from this list.
    """
    required = _parse_requirements(_REPO / "requirements.txt")
    improvements = _parse_requirements(_REPO / "requirements-improvements.txt")
    declared = required | improvements

    freeze_cmd = shlex.quote(f"{venv}/bin/pip") + " freeze --all"
    freeze_text = _ssh_run(vps, key, freeze_cmd)
    installed = _parse_pip_freeze(freeze_text)

    leaf_cmd = (
        shlex.quote(f"{venv}/bin/pip")
        + " list --not-required --format=freeze"
    )
    leaf_text = _ssh_run(vps, key, leaf_cmd)
    leaves = _parse_pip_freeze(leaf_text)

    infra = {_normalize(n) for n in _KNOWN_VENV_INFRA}
    extras = sorted(
        n for n in leaves
        if n not in declared and n not in infra
    )
    missing_required = sorted(n for n in required if n not in installed)
    return extras, missing_required


_S195_HOTPATCH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("esports_team_aliases", "created_at"),
    ("esports_unmatched_predictions", "event_time"),
)


def probe_schema(vps: str, key: str) -> list[str]:
    """Return list of column qualifiers missing a SQL DEFAULT for S195 hot-patch."""
    sql_lines = []
    for table, col in _S195_HOTPATCH_COLUMNS:
        sql_lines.append(
            "SELECT '{t}.{c}' AS qual, "
            "  pg_get_expr(d.adbin, d.adrelid) AS default_expr "
            "FROM pg_attribute a "
            "LEFT JOIN pg_attrdef d "
            "  ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE a.attrelid = '{t}'::regclass AND a.attname = '{c}'".format(
                t=table, c=col
            )
        )
    sql = " UNION ALL ".join(sql_lines) + ";"
    cmd = (
        "PGPASSWORD=$(sudo grep '^DATABASE_URL=' /opt/pa2-shared/.env "
        "| sed -n 's|.*://[^:]*:\\([^@]*\\)@.*|\\1|p') "
        f"psql -U polymarket -d polymarket -h localhost -p 6432 -t -A -F '|' -c "
        + shlex.quote(sql)
    )
    out = _ssh_run(vps, key, cmd)
    missing: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split("|", 1)
        if len(parts) != 2:
            continue
        qual, default_expr = parts[0].strip(), parts[1].strip()
        if not default_expr:
            missing.append(qual)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pip", action="store_true", help="check pip drift only")
    parser.add_argument("--schema", action="store_true", help="check schema drift only")
    parser.add_argument(
        "--vps",
        default=os.environ.get("VPS_HOST", "ubuntu@18.201.216.0"),
        help="user@host for ssh (default: $VPS_HOST or ubuntu@18.201.216.0)",
    )
    parser.add_argument(
        "--venv",
        default="/opt/pa2-shared/venv",
        help="path to the VPS venv (default: /opt/pa2-shared/venv)",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get(
            "SSH_KEY",
            str(Path.home() / ".ssh" / "LightsailDefaultKey-eu-west-1.pem"),
        ),
        help="ssh key path (default: $SSH_KEY or ~/.ssh/LightsailDefaultKey-eu-west-1.pem)",
    )
    args = parser.parse_args(argv)

    run_pip = args.pip or not args.schema
    run_schema = args.schema or not args.pip

    if not Path(args.key).exists():
        print(f"ERROR: ssh key not found at {args.key}", file=sys.stderr)
        return 2

    drift_found = False
    if run_pip:
        try:
            extras, missing_required = probe_pip(args.vps, args.key, args.venv)
        except RuntimeError as exc:
            print(f"pip probe failed: {exc}", file=sys.stderr)
            return 2
        if missing_required:
            drift_found = True
            print(
                "pip drift — declared in requirements.txt but NOT installed on VPS"
                " (fresh deploy would also miss these):"
            )
            for name in missing_required:
                print(f"  {name}")
        if extras:
            print(
                "pip notice — leaf packages on VPS NOT declared locally"
                " (potential hot-patches, triage manually):"
            )
            for name in extras:
                print(f"  {name}")
        if not (extras or missing_required):
            print("pip drift: clean")

    if run_schema:
        try:
            missing = probe_schema(args.vps, args.key)
        except RuntimeError as exc:
            print(f"schema probe failed: {exc}", file=sys.stderr)
            return 2
        if missing:
            drift_found = True
            print("schema drift — columns missing SQL DEFAULT (S195 hot-patch class):")
            for qual in missing:
                print(f"  {qual}")
        else:
            print("schema drift: clean for S195-tracked columns")

    return 1 if drift_found else 0


if __name__ == "__main__":
    sys.exit(main())
