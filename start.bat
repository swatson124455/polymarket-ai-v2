@echo off
title Polymarket AI V2 - Paper Trading
cd /d "%~dp0"
echo ============================================================
echo  Polymarket AI V2 - Paper Trading (11 models + RL agent)
echo  Press Ctrl+C to stop gracefully
echo ============================================================
echo.
python main.py
echo.
echo ============================================================
echo  System exited. Check output above for errors.
echo ============================================================
pause
