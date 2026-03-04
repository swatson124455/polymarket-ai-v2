"""Launcher for paper trading. Structlog writes directly to data/paper_trading.log."""
import os
import sys
import subprocess

LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "paper_trading.log")

if __name__ == "__main__":
    # Clear previous log
    with open(LOG_FILE, "w") as f:
        f.write("")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        # Don't redirect stdout to log file — structlog writes directly to the file
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Paper trading started: PID {proc.pid}")
    print(f"Log: {LOG_FILE}")
    print(f"Monitor: powershell -Command \"Get-Content '{LOG_FILE}' -Tail 30 -Wait\"")
