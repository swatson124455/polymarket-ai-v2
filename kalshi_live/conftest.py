"""The kalshi_live suite is cwd-dependent (tests load modules and fixture files by
relative path — audit 2026-08-02 lens 1: run from the repo root, 46 collection errors
masquerade as a broken suite). Pin the cwd to this directory so `pytest` gives the same
967-passing result from anywhere."""
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
