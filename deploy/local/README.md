# Run the EsportsBot odds collector on your own PC (Windows)

This moves the hourly PinnOdds + Polymarket snapshot collector off the VPS and
onto your Windows machine. **Two things to know before you start:**

1. **Your PC must stay awake 24/7.** Every time it sleeps, you get a hole in the
   data for that hour. This is the one real downside vs the always-on VPS.
2. **Run the collector in ONE place only.** The API key has a shared rate limit,
   so the setup ends by turning the VPS copy off. Never run both at once.

Everything on the VPS *other than* the collector (the trading system, results
fetcher, etc.) is untouched.

## Do this

**Step 1 — Install Python** (skip if you already have it). Go to
<https://www.python.org/downloads/>, run the installer, and **tick the box
"Add Python to PATH"** before clicking Install.

**Step 2 — Download the setup script.** Open PowerShell and paste:

```powershell
mkdir C:\eb-odds; curl.exe -fsSL https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/claude/esports-sharp-line-rebuild-gqy1na/deploy/local/eb_setup_local.ps1 -o C:\eb-odds\eb_setup_local.ps1
```

**Step 3 — Run it.** This does everything: downloads the collector, copies your
data + aliases + API key from the VPS, schedules it hourly, and runs one test
tick.

```powershell
powershell -ExecutionPolicy Bypass -File C:\eb-odds\eb_setup_local.ps1
```

Watch the output — every step prints `OK`. If a step says `FAIL`, it stops
there and tells you why (usually: Python not on PATH, or the SSH key isn't at
`C:\Users\<you>\.ssh\LightsailDefaultKey-eu-west-1.pem`).

**Step 4 — Confirm it's collecting.** Wait for the next hour, then:

```powershell
Get-Content C:\eb-odds\collect.log -Tail 3
```

A healthy line ends with `appended=... pm_matched=... dur=...`. (A `429` line
just means the rate limit is napping; the next hour retries.)

**Step 5 — Turn off the VPS copy** (only after Step 4 looks healthy, so you're
never running both):

```powershell
powershell -ExecutionPolicy Bypass -File C:\eb-odds\eb_setup_local.ps1 -DisableVps
```

## Everyday use

- **Check on it:** `Get-Content C:\eb-odds\collect.log -Tail 3`
- **Undo the local schedule:** `schtasks /Delete /TN "EB Odds Collector" /F`
  (then ask for the one-liner to switch the VPS collector back on)

## Before the backtest / audit

The audit and backtest scripts still run on the VPS and read the snapshot file
there. So before a readout (e.g. after the July 15–19 slate), copy your local
data back up first:

```powershell
scp -i $HOME\.ssh\LightsailDefaultKey-eu-west-1.pem C:\eb-odds\pinnodds_snapshots.jsonl ubuntu@18.201.216.0:/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl
```
