#!/usr/bin/env python3
"""Swap in the verified quoter. Timestamped backup + atomic replace (the timer starts a
fresh process every 2 min, so a partial read must be impossible)."""
import os, shutil, hashlib, datetime

LIVE = "/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py"
NEW = "/tmp/new_quoter.py"


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


print("BEFORE md5", md5(LIVE))
print("NEW    md5", md5(NEW))
if md5(LIVE) == md5(NEW):
    print("IDENTICAL — nothing to do")
    raise SystemExit(0)

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = f"{LIVE}.bak-strandfix-{stamp}"
shutil.copy2(LIVE, bak)
print("BACKUP", bak, md5(bak))

tmp = LIVE + ".tmp"
shutil.copy2(NEW, tmp)
shutil.copymode(LIVE, tmp)
os.replace(tmp, LIVE)
print("AFTER  md5", md5(LIVE))
print("SWAPPED OK" if md5(LIVE) == md5(NEW) else "*** SWAP FAILED ***")
