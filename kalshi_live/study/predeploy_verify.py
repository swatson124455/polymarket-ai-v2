#!/usr/bin/env python3
"""PRE-DEPLOY verification of the NEW quoter at /tmp/new_quoter.py against the LIVE env.

Checks, in order:
  1. it compiles
  2. the four flags routed through _envb evaluate EXACTLY as the inline expressions did,
     under the real live.env (a flipped TAKER_FLATTEN would be a live money path change)
  3. the Python 3.13 scoping trap: a global referenced in run_once must not have become
     func-local (memory: rebinding a name anywhere in a func makes it local for the WHOLE
     func -> UnboundLocalError; this bit the WS daemon past 168 tests + 2 reviews)
  4. env_absent() reports something sane
Nothing is swapped by this script.
"""
import os, sys, py_compile, importlib.util

SRC = "/tmp/new_quoter.py"
for ln in open("/opt/pa2-maker-kalshi-live/live.env"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        os.environ[k] = v

py_compile.compile(SRC, doraise=True)
print("1. COMPILES ok")

# what the ORIGINAL inline expressions would yield under this exact environment
expected = {
    "JOIN_ALWAYS":           os.environ.get("KALSHI_JOIN_ALWAYS") == "1",
    "THROTTLE_SMART":        os.environ.get("KALSHI_THROTTLE_SMART") == "1",
    "REDUCE_ONLY_KEEP_BOTH": os.environ.get("KALSHI_REDUCE_ONLY_KEEP_BOTH", "1") == "1",
    "TAKER_FLATTEN":         os.environ.get("KALSHI_TAKER_FLATTEN", "1") == "1",
}

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
spec = importlib.util.spec_from_file_location("new_quoter", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["new_quoter"] = m
spec.loader.exec_module(m)

ok = True
print("2. FLAG SEMANTICS under live.env (old expression -> new value)")
for k, want in expected.items():
    got = getattr(m, k)
    same = (got == want)
    ok &= same
    print(f"   {k:<24} expected={want!s:<6} got={got!s:<6} {'OK' if same else '*** MISMATCH ***'}")

print("3. PY313 SCOPING GUARD")
for name in ("env_absent", "_SAFETY_KNOBS", "_ENV_DECLARED"):
    local = name in m.run_once.__code__.co_varnames
    ok &= not local
    print(f"   {name:<16} func-local in run_once? {local}  {'OK' if not local else '*** WOULD UnboundLocalError ***'}")

a = m.env_absent()
print(f"4. env_absent(): {len(a)} of {len(m._ENV_DECLARED)} declared knobs unset")
print(f"   protection knobs unset: {[k for k in m._SAFETY_KNOBS if k in a]}")
print()
print("VERDICT:", "PASS — safe to swap" if ok else "FAIL — DO NOT SWAP")
sys.exit(0 if ok else 1)
