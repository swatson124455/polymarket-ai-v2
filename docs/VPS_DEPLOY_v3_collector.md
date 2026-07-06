# VPS Deploy — v3 signal collector (paper, strategy empty)

Deploys the MirrorBot v3 silo with **live signal collection** wired in: it
connects the RTDS trade feed, records the raw watched-wallet entry stream to
`mirror_rejected_signals` (tagged `metadata.source="mirror_v3"`), and lets old
MB be **stopped** instead of merely paused.

**Safety posture:** paper only (env_guard refuses to boot otherwise). No trading
— the strategy slot is empty behind the acceptance gate. The only side effects
are a read-only leaderboard GET, an RTDS subscribe, and INSERTs into the
`mirror_rejected_signals` instrumentation table.

**Coexistence:** v3 and old MB may run at the same time. Their rows are
distinguishable — v3 uses `rejection_reason='mirror_v3_strategy_gated'` +
`metadata.source='mirror_v3'`; old MB uses its gate-specific reasons. Start v3,
confirm it writes, THEN stop old MB. (During overlap, a gate run over the table
sees both streams — filter on the marker if you run the gate in that window.)

**Code source:** branch `claude/session-handoff-6uuafb` (not yet on master;
direct-master is operator-gated). Deploy-from-branch matches the established
runbook pattern.

---

## One SSH session does it all

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0
```

### 1. Pull the branch code (private repo — uses the box's existing git auth)

```bash
cd /tmp && rm -rf mb3 && git clone --depth 1 -b claude/session-handoff-6uuafb \
  https://github.com/swatson124455/polymarket-ai-v2 mb3
```

### 2. Env file — auto-fills DATABASE_URL from the shared env (no manual edit, no secret printed)

```bash
sudo cp /tmp/mb3/deploy/env.mirror3.example /opt/pa2-shared/.env.mirror3
sudo sed -i '/^DATABASE_URL=/d' /opt/pa2-shared/.env.mirror3
sudo grep -h '^DATABASE_URL=' /opt/pa2-shared/.env /opt/pa2-shared/.env.mirror 2>/dev/null \
  | head -1 | sudo tee -a /opt/pa2-shared/.env.mirror3 >/dev/null
sudo chown polymarket:polymarket /opt/pa2-shared/.env.mirror3 && sudo chmod 600 /opt/pa2-shared/.env.mirror3
# sanity (prints only set/unset, never the value):
sudo grep -q '^DATABASE_URL=postgresql' /opt/pa2-shared/.env.mirror3 \
  && echo "DATABASE_URL: set" || echo "DATABASE_URL: MISSING — do not proceed"
```

### 3. Install the unit + sync the silo code into the release

```bash
sudo cp /tmp/mb3/deploy/polymarket-mirror3.service /etc/systemd/system/
sudo rsync -a /tmp/mb3/mirror_v3 /opt/polymarket-ai-v2/
sudo chown -R polymarket:polymarket /opt/polymarket-ai-v2/mirror_v3
sudo systemctl daemon-reload && sudo systemctl enable --now polymarket-mirror3
```

### 4. Watch it boot

```bash
journalctl -u polymarket-mirror3 -n 30 --no-pager
```

**Expected:**
```
[MirrorBotV3] env guard OK: {'mode': 'paper', ...}
[MirrorBotV3] restored: entered=0 open=0 daily_exposure=$0.00 mode=paper
[MirrorBotV3] RTDS signal collection started watchlist=NNN      # NNN > 0
[MirrorBotV3] heartbeat {...} strategy=EMPTY(gated) watchlist=NNN collector={'events_seen': ...}
```
If it refuses to boot, env_guard prints exactly which key is wrong — that is the
safety spine working, not failing. `watchlist=0` means the leaderboard fetch
failed (check outbound network); it will retry on the 6h TTL.

---

## Verify it's collecting (then, and only then, stop old MB)

`collector.events_seen` should climb every heartbeat (the RTDS firehose is
busy). Rows land as **watched** wallets trade, which can take minutes:

```bash
# v3 rows in the last 15 min (0 is possible early — watched-wallet trades are sparser):
sudo -u polymarket psql polymarket -c "
SELECT rejection_reason, count(*) AS rows, max(event_time) AS latest
FROM mirror_rejected_signals
WHERE event_time > NOW() - interval '15 min'
  AND metadata->>'source' = 'mirror_v3'
GROUP BY 1;"
```

Once you see v3 rows (or `collector.logged` incrementing in the logs), old MB is
redundant. **Stop it:**

```bash
sudo systemctl stop polymarket-mirror
sudo systemctl disable polymarket-mirror     # keep it from restarting on boot
```

Old MB is now fully retired; v3 carries the signal stream. To roll back, re-enable
old MB (`sudo systemctl enable --now polymarket-mirror`) and stop v3.

---

## Not verified in-session (verify here)

No RTDS socket or DB exists in the build sandbox, so the live path is unit-tested
only. At deploy, confirm: (a) `watchlist=NNN` with NNN>0, (b) `collector.events_seen`
climbing, (c) v3 rows appear with `metadata.source='mirror_v3'`. If you see
`v3_signal_write_failed` warnings, the deployed `base_engine` `insert_mirror_rejected_signal`
signature differs from expected — paste the warning back and it's a one-line fix.
