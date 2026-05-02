#!/usr/bin/env python3
"""S208: Config drift audit — code os.getenv defaults vs VPS .env runtime.

Walks the codebase, extracts every os.getenv("KEY", default) and
os.environ.get("KEY", default) call (literal keys only — dynamic key
construction is skipped). SSH-reads the VPS .env file. Produces a
categorized drift report:

    DRIFT          — .env override differs from code default (the
                     class that produced the WEATHER_MIN_TRADE_USD
                     S207-handoff-vs-reality finding).
    REDUNDANT      — .env sets the value to the code default
                     (no operational change; tracking only).
    NO-DEFAULT     — code reads the key without a fallback. If .env
                     doesn't set it, the call returns None (or the
                     bot crashes for os.environ["KEY"]).
    ENV-ORPHAN     — .env sets a value with no code reference.
    ALIGNED        — code default in effect, no .env override needed.

This script is the third-instance evidence base for promoting the
"Hierarchical infrastructure verification" Protocol candidate at
§Protocol candidates → Protocol 13. The discipline rule it codifies:
runtime-config claims must be verified at the substrate where the
setting takes effect (.env), not at the default-query interface
(settings.py / `os.getenv` second-arg).

Usage:
    python scripts/config_drift_audit.py                  # default scope, full report
    python scripts/config_drift_audit.py --drift-only     # only the actionable section
    python scripts/config_drift_audit.py --vps-env PATH   # override VPS .env path
    python scripts/config_drift_audit.py --no-ssh         # skip SSH (assume local .env file)

Read-only — no DB, no .env writes, no service restarts.
"""
import argparse
import ast
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default VPS connection (can be overridden by env / args)
DEFAULT_SSH_KEY = str(Path.home() / ".ssh" / "LightsailDefaultKey-eu-west-1.pem")
DEFAULT_SSH_HOST = "ubuntu@18.201.216.0"
DEFAULT_VPS_ENV = "/opt/pa2-shared/.env"

# Skip these directories during code walk
SKIP_DIRS = {
    "review_package",  # vendored review snapshots
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".git",
    "data",            # data files, not source
    "output",          # session-local outputs
}


def extract_getenv_calls(filepath: Path):
    """Yield (key, default_repr, lineno) for each os.getenv-shape call.

    Matches:
        os.getenv("KEY")
        os.getenv("KEY", default)
        os.environ.get("KEY")
        os.environ.get("KEY", default)
        os.environ["KEY"]               (recorded as no-default key access)

    Skips dynamic keys (f-strings, variables).
    """
    try:
        src = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    try:
        tree = ast.parse(src, filename=str(filepath))
    except SyntaxError:
        return

    for node in ast.walk(tree):
        # Function-call form: os.getenv / os.environ.get
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            base = node.func.value
            is_getenv = (
                attr == "getenv"
                and isinstance(base, ast.Name)
                and base.id == "os"
            )
            is_environ_get = (
                attr == "get"
                and isinstance(base, ast.Attribute)
                and base.attr == "environ"
                and isinstance(base.value, ast.Name)
                and base.value.id == "os"
            )
            if not (is_getenv or is_environ_get):
                continue
            if not node.args:
                continue
            key_node = node.args[0]
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue  # dynamic key, skip
            key = key_node.value
            default_repr = None
            if len(node.args) >= 2:
                try:
                    default_repr = ast.unparse(node.args[1])
                except AttributeError:
                    default_repr = "<unparse-unavailable>"
            yield key, default_repr, node.lineno
            continue

        # Subscript form: os.environ["KEY"]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            attr_node = node.value
            if (
                attr_node.attr == "environ"
                and isinstance(attr_node.value, ast.Name)
                and attr_node.value.id == "os"
            ):
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    yield slice_node.value, "<no-default-subscript>", node.lineno


def collect_code_defaults():
    """Walk repo, collect all (key -> [(default_repr, location), ...])."""
    refs: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    for py_file in REPO_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        for key, default, lineno in extract_getenv_calls(py_file):
            refs[key].append((default, f"{rel}:{lineno}"))
    return dict(refs)


def fetch_vps_env(ssh_key: str, ssh_host: str, vps_env: str) -> str:
    """SSH-read the VPS .env file. Returns raw text."""
    cmd = ["ssh", "-i", ssh_key, ssh_host, f"sudo cat {vps_env}"]
    return subprocess.check_output(cmd, text=True)


def parse_env_text(text: str) -> dict[str, tuple[str, int]]:
    """Parse KEY=VALUE pairs (skipping comments/blank lines).

    Returns dict of key -> (value, line_no).
    """
    pairs: dict[str, tuple[str, int]] = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Strip inline comments (only if quote-balanced)
        # — keep it simple, skip this for now
        pairs[key] = (value, line_no)
    return pairs


def normalize(s: str | None) -> str | None:
    """Normalize for comparison: strip outer quotes + whitespace."""
    if s is None:
        return None
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]
    return s


def values_equal(code_default: str | None, env_value: str | None) -> bool:
    """Compare a code default and a .env runtime value with type-aware
    equality so '5.0' == '5', 'True' == 'true', etc."""
    if code_default is None or env_value is None:
        return code_default == env_value
    cd = code_default.strip()
    ev = env_value.strip()
    if cd == ev:
        return True
    # Numeric equivalence (handles 5.0 vs 5, 0.05 vs .05, etc.)
    try:
        return float(cd) == float(ev)
    except ValueError:
        pass
    # Boolean-like equivalence
    cd_lower = cd.lower()
    ev_lower = ev.lower()
    bool_true = {"true", "1", "yes", "on"}
    bool_false = {"false", "0", "no", "off"}
    if cd_lower in bool_true and ev_lower in bool_true:
        return True
    if cd_lower in bool_false and ev_lower in bool_false:
        return True
    return False


def categorize(code: dict, env: dict) -> dict:
    """Classify each unique key into buckets."""
    buckets = {
        "drift": [],          # (key, code_default, env_value, refs)
        "redundant": [],      # (key, value, refs)
        "no_default_in_env": [],   # key in code w/o default, IS in .env
        "no_default_missing": [],  # key in code w/o default, NOT in .env
        "aligned_no_override": [], # key has default, .env doesn't override
        "env_orphan": [],     # key in .env, not in code
        "multi_default": [],  # key has multiple distinct defaults in code
    }

    # Detect multi-default keys (code references with conflicting defaults)
    for key, refs in code.items():
        unique_defaults = {normalize(d) for d, _ in refs if d is not None}
        if len(unique_defaults) > 1:
            buckets["multi_default"].append((key, sorted(unique_defaults), refs))

    all_keys = set(code) | set(env)
    for key in sorted(all_keys):
        env_pair = env.get(key)
        env_value = env_pair[0] if env_pair else None

        if key not in code:
            buckets["env_orphan"].append((key, env_value, env_pair[1]))
            continue

        refs = code[key]
        # Take first observed default (most-cited usage)
        first_default = refs[0][0]
        first_default_norm = normalize(first_default)

        # Treat the magic markers as "no default"
        no_default = first_default in (None, "<no-default-subscript>", "None")

        if no_default:
            if env_value is None:
                buckets["no_default_missing"].append((key, refs))
            else:
                buckets["no_default_in_env"].append((key, env_value, refs))
            continue

        if env_value is None:
            buckets["aligned_no_override"].append((key, first_default_norm, refs))
            continue

        if values_equal(first_default_norm, env_value):
            buckets["redundant"].append((key, env_value.strip(), refs))
        else:
            buckets["drift"].append((key, first_default_norm, env_value.strip(), refs))

    return buckets


def print_report(buckets: dict, drift_only: bool = False) -> None:
    print("=" * 78)
    print("  S208 Config Drift Audit")
    print(f"  Codebase: {REPO_ROOT}")
    print("=" * 78)
    print()

    drifts = buckets["drift"]
    redundant = buckets["redundant"]
    aligned = buckets["aligned_no_override"]
    no_def_in_env = buckets["no_default_in_env"]
    no_def_missing = buckets["no_default_missing"]
    env_orphan = buckets["env_orphan"]
    multi_def = buckets["multi_default"]

    print(f"DRIFT (.env override differs from code default): {len(drifts)}")
    print(f"REDUNDANT (.env matches code default):           {len(redundant)}")
    print(f"NO-DEFAULT, .env present:                        {len(no_def_in_env)}")
    print(f"NO-DEFAULT, .env MISSING:                        {len(no_def_missing)}")
    print(f"ALIGNED-NO-OVERRIDE (code default in effect):    {len(aligned)}")
    print(f"ENV-ORPHANS (.env-only, no code ref):            {len(env_orphan)}")
    print(f"MULTI-DEFAULT (same key, conflicting defaults):  {len(multi_def)}")
    print()

    section("DRIFT — .env override differs from code default", drifts, format_drift)
    if drift_only:
        return

    section("MULTI-DEFAULT — same key with conflicting defaults in code",
            multi_def, format_multi_default)
    section("NO-DEFAULT, .env MISSING — code reads w/o default and .env doesn't set",
            no_def_missing, format_no_default_missing)
    section("NO-DEFAULT, .env present — must-be-set keys (sanity)",
            no_def_in_env, format_no_default_in_env)
    section("ENV-ORPHANS — keys in .env with no os.getenv ref in code",
            env_orphan, format_env_orphan)
    section("REDUNDANT — .env redundantly sets to code default",
            redundant, format_redundant, cap=30)
    section("ALIGNED — code default applies, no .env override",
            aligned, format_aligned, cap=30)


def section(title: str, items: list, formatter, cap: int | None = None) -> None:
    print("=" * 78)
    print(f"  {title} ({len(items)})")
    print("=" * 78)
    if not items:
        print("  (none)")
        print()
        return
    shown = items[:cap] if cap else items
    for entry in shown:
        formatter(entry)
    if cap and len(items) > cap:
        print(f"  ... and {len(items) - cap} more (truncated)")
    print()


def format_drift(entry):
    key, code_default, env_value, refs = entry
    print(f"  {key}")
    print(f"    code default: {code_default!r}")
    print(f"    .env value:   {env_value!r}")
    print(f"    first ref:    {refs[0][1]} ({len(refs)} total)")


def format_multi_default(entry):
    key, defaults, refs = entry
    print(f"  {key}: defaults={defaults!r}")
    for d, loc in refs:
        print(f"    {loc}: default={d!r}")


def format_no_default_missing(entry):
    key, refs = entry
    locs = ", ".join(loc for _, loc in refs[:3])
    if len(refs) > 3:
        locs += f", +{len(refs) - 3} more"
    print(f"  {key:50s} ({locs})")


def format_no_default_in_env(entry):
    key, env_value, refs = entry
    print(f"  {key:50s} = {env_value!r}  (refs: {refs[0][1]})")


def format_env_orphan(entry):
    key, value, line = entry
    print(f"  .env:{line:3d}  {key:50s} = {value!r}")


def format_redundant(entry):
    key, value, refs = entry
    print(f"  {key:50s} = {value!r}")


def format_aligned(entry):
    key, default, refs = entry
    print(f"  {key:50s} default={default!r}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ssh-key", default=DEFAULT_SSH_KEY)
    p.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    p.add_argument("--vps-env", default=DEFAULT_VPS_ENV)
    p.add_argument("--no-ssh", action="store_true",
                   help="Skip SSH; read --local-env-file instead")
    p.add_argument("--local-env-file", default=None,
                   help="Local .env file (when --no-ssh)")
    p.add_argument("--drift-only", action="store_true",
                   help="Only print the DRIFT section")
    args = p.parse_args(argv)

    print("[1/2] Walking codebase for os.getenv calls...", file=sys.stderr)
    code = collect_code_defaults()
    print(f"      {len(code)} unique keys", file=sys.stderr)

    print("[2/2] Reading .env...", file=sys.stderr)
    if args.no_ssh:
        if not args.local_env_file:
            print("ERROR: --no-ssh requires --local-env-file", file=sys.stderr)
            return 2
        env_text = Path(args.local_env_file).read_text(encoding="utf-8")
    else:
        env_text = fetch_vps_env(args.ssh_key, args.ssh_host, args.vps_env)
    env = parse_env_text(env_text)
    print(f"      {len(env)} keys in .env", file=sys.stderr)
    print(file=sys.stderr)

    buckets = categorize(code, env)
    print_report(buckets, drift_only=args.drift_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
