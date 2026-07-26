#!/usr/bin/env python3
"""INTENT vs ACTUAL — every env knob the quoter reads, vs what live.env actually sets.

THROTTLE_SMART was found OFF because its key was simply absent from live.env. That is a
CLASS of defect, not one instance: any flag whose key is missing silently takes the code
default, and nothing logs it. This enumerates every one of them.
"""
import re, sys, os

SRC = sys.argv[1]
ENV = sys.argv[2]

src = open(SRC, encoding="utf-8", errors="replace").read()

# _envi("KALSHI_X", default) / _envf("KALSHI_X", default) / os.environ.get("KALSHI_X"...)
pats = [
    (r'_envi\(\s*"(KALSHI_[A-Z0-9_]+)"\s*,\s*([^)]+)\)', "int"),
    (r'_envf\(\s*"(KALSHI_[A-Z0-9_]+)"\s*,\s*([^)]+)\)', "float"),
    (r'os\.environ\.get\(\s*"(KALSHI_[A-Z0-9_]+)"\s*,\s*"([^"]*)"\s*\)', "str"),
    (r'os\.environ\.get\(\s*"(KALSHI_[A-Z0-9_]+)"\s*\)\s*==\s*"([^"]*)"', "boolflag"),
]
found = {}
for pat, kind in pats:
    for m in re.finditer(pat, src):
        k = m.group(1)
        d = m.group(2).strip()
        found.setdefault(k, (kind, d))

live = {}
for ln in open(ENV):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        a, b = ln.split("=", 1)
        live[a.strip()] = b.strip()

print(f"KNOBS THE CODE READS : {len(found)}")
print(f"KEYS live.env SETS   : {len(live)}")

missing = sorted(k for k in found if k not in live)
setk = sorted(k for k in found if k in live)
extra = sorted(k for k in live if k not in found and k.startswith("KALSHI_"))

print("\n" + "=" * 78)
print(f"A. READ BY CODE, ABSENT FROM live.env -> SILENTLY TAKES CODE DEFAULT  ({len(missing)})")
print("=" * 78)
for k in missing:
    kind, d = found[k]
    flag = "  <== BOOL FLAG, effectively OFF" if kind == "boolflag" else ""
    print(f"  {k:<38} default={d:<28} [{kind}]{flag}")

print("\n" + "=" * 78)
print(f"B. SET IN live.env, OVERRIDING THE CODE DEFAULT  ({len(setk)})")
print("=" * 78)
for k in setk:
    kind, d = found[k]
    same = ""
    try:
        if kind in ("int", "float") and abs(float(live[k]) - float(d)) < 1e-12:
            same = "  (== default)"
    except Exception:
        pass
    mark = "  <-- DIFFERS" if not same else ""
    print(f"  {k:<38} live={live[k]:<12} default={d:<20}{same}{mark}")

print("\n" + "=" * 78)
print(f"C. SET IN live.env BUT NOT READ BY THE QUOTER  ({len(extra)})  -- dead or read elsewhere")
print("=" * 78)
for k in extra:
    print(f"  {k:<38} = {live[k]}")
