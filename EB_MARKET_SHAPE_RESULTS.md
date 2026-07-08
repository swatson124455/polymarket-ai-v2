# EB Market-Shape Probe — Results

## STATUS: ⛔ BLOCKED — NO DATA YET (cloud session could not reach any market source)

> **Next VPS-capable session: OVERWRITE this file** with the real probe output.
> This is a placeholder recording *why* step 1 has not produced data, not results.

**Attempted:** 2026-07-08 (cloud/sandboxed session, branch
`claude/esports-sharp-line-rebuild-36c8u9`)
**Script:** `scripts/esports_market_shape_probe.py` (read-only; unchanged)

---

## 1. What step 1 needs vs. what this environment has

`esports_market_shape_probe.py` needs **two** live sources:
1. the **prod `markets` table** (to discover esports condition_ids), and
2. the **live CLOB** `https://clob.polymarket.com/markets/{cid}` (the ground-truth
   outcome labels that decide shape-1 vs shape-2).

Neither is reachable from this cloud session:

| Source | Result | Evidence |
|---|---|---|
| VPS `18.201.216.0:22` | **Unreachable** | `TCP:22 connection refused`; no SSH key present in `~/.ssh/` |
| `clob.polymarket.com:443` | **Egress-denied** | proxy `403 CONNECT` (`connect_rejected` in `$HTTPS_PROXY/__agentproxy/status`) |
| `gamma-api.polymarket.com:443` | **Egress-denied** | proxy `403 CONNECT` (same status log) |
| Prod / local DB | **None running** | `DATABASE_URL` → `localhost:5432` but the Postgres port is **closed** |

The agent-proxy README is explicit: policy denials (403/407) must be **reported, not
routed around**. So the probe cannot be run here by any legitimate path. This matches
the prior session's finding (`EB_SHARP_LINE_STATE.md` §5: *"Blocked from cloud
sessions — no VPS/DB/API"*).

## 2. What was NOT done, and why (guardrail compliance)

- **Parser hardening (step 2)** and **matcher root-fix (step 3)** were **not**
  attempted. Both depend on the real question phrasings this probe would return, and
  the matcher edit is a working-code change the plumbing spec + CLAUDE.md require to be
  **verified live before/after**. Doing them blind would mean guessing phrasings and
  editing live-path code unverified — a direct violation of the **correct-or-absent**
  contract (a flipped orientation *inverts* the edge — the S152/B2 loss) and the
  explicit *"don't over-invest in parser coverage before odds data exists"* guardrail.
- No deploy (EB halted; code not wired in).
- The offline core was re-verified: **59/59** tests green
  (`tests/unit/test_esports_orientation.py`, `tests/unit/test_esports_sharp_reference.py`),
  run with `-o addopts="" --noconftest` (repo conftest needs the full trading-system
  dep tree, absent here; the two EB modules are pure stdlib).

## 3. How to actually run it (VPS-capable session)

```bash
git checkout claude/esports-sharp-line-rebuild-36c8u9   # or the startup branch
# on the VPS, with the prod env loaded (DATABASE_URL set):
python scripts/esports_market_shape_probe.py            # 20 DB rows, 8 CLOB peeks
python scripts/esports_market_shape_probe.py 40 12      # wider sample
```

Then **replace this whole file** with the probe's stdout (it is not secret), commit,
and push. That real output unblocks step 2 (harden the shape-1 parser against the
actual phrasings) and step 3 (persist `yes_is_team_a` onto the matcher `market_dict`).

## 4. Reminder: the deeper binding blocker

Even with shapes confirmed, the **end-to-end backtest stays blocked**: `pinnacle_odds`
is empty (B13). No sharp odds exist until the OddsPapi paid tier is live. Per
`EB_SHARP_LINE_STATE.md` §5.4, that — not parser coverage — is the real constraint.
